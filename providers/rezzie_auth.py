"""Issue a fresh Rezzie session and overwrite the saved browser tokens.

The buyer access token lasts 24 hours. The daily job runs 24 hours later, so
reusing yesterday's token lands on the public homepage. Each run signs in
again and replaces the tokens in ``.session/rezzie.json``.
"""

from __future__ import annotations

import base64
import json
import logging
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("rezzie")

API_BASE = "https://rezzie-backend-staging.lektikprojects.com/api/v1"
ORIGIN = "https://rezzie.com"
LOGIN_ATTEMPTS = 3
LOGIN_RETRY_SLEEP_SEC = 2

_ROLE = {
    "SELLER": "seller",
    "WHOLESALER": "seller",
    "BUYER": "buyer",
    "INVESTOR": "buyer",
    "ADMIN": "admin",
    "COMPANY": "company",
}


def issue_session(email: str, password: str, state_path: Path) -> dict[str, Any]:
    """Log in and return the new token payload. Refresh only if login fails."""
    saved_refresh = read_refresh_token(state_path)
    last_error: Exception | None = None
    for attempt in range(1, LOGIN_ATTEMPTS + 1):
        try:
            body = _post("/auth/login", {"email": email.strip().lower(), "password": password})
            session = session_from_login(body)
            log.info(
                "Rezzie login replaced the saved tokens; access token expires at %s (attempt %s)",
                session["expires_at"],
                attempt,
            )
            return session
        except Exception as exc:
            last_error = exc
            log.warning("Rezzie login attempt %s/%s failed: %s", attempt, LOGIN_ATTEMPTS, exc)
            if attempt < LOGIN_ATTEMPTS:
                time.sleep(LOGIN_RETRY_SLEEP_SEC)
    if saved_refresh:
        try:
            body = _post("/auth/refresh", {"refreshToken": saved_refresh})
            session = session_from_refresh(body, state_path)
            log.info(
                "Rezzie refresh replaced the access token; it expires at %s",
                session["expires_at"],
            )
            return session
        except Exception as exc:
            log.warning("Rezzie refresh fallback failed: %s", exc)
    raise RuntimeError(f"Rezzie login failed after {LOGIN_ATTEMPTS} attempts: {last_error}")


def write_storage(state_path: Path, session: dict[str, Any]) -> None:
    """Overwrite Rezzie auth keys in the Playwright storage file."""
    state_path.parent.mkdir(parents=True, exist_ok=True)
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
    else:
        state = {"cookies": [], "origins": []}
    if not isinstance(state, dict):
        state = {"cookies": [], "origins": []}
    origins = state.setdefault("origins", [])
    if not isinstance(origins, list):
        origins = []
        state["origins"] = origins
    origin = next((item for item in origins if isinstance(item, dict) and item.get("origin") == ORIGIN), None)
    if origin is None:
        origin = {"origin": ORIGIN, "localStorage": []}
        origins.append(origin)
    items = origin.setdefault("localStorage", [])
    if not isinstance(items, list):
        items = []
        origin["localStorage"] = items
    updates = {
        "rezzie_auth_token": session["token"],
        "rezzie_refresh_token": session["refresh_token"],
    }
    if session.get("user_json"):
        updates["rezzie_user_data"] = session["user_json"]
    if session.get("store_json"):
        updates["rezzie-auth-store"] = _merge_store(items, session["store_json"])
    by_name = {item.get("name"): item for item in items if isinstance(item, dict)}
    for name, value in updates.items():
        current = by_name.get(name)
        if current is None:
            items.append({"name": name, "value": value})
        else:
            current["value"] = value
    temporary = state_path.with_suffix(state_path.suffix + ".tmp")
    temporary.write_text(json.dumps(state), encoding="utf-8")
    temporary.replace(state_path)


def read_refresh_token(state_path: Path) -> str:
    return _storage_value(state_path, "rezzie_refresh_token")


def session_from_login(body: dict[str, Any]) -> dict[str, Any]:
    payload = _unwrap(body)
    token = str(payload.get("token") or "").strip()
    refresh = str(payload.get("refreshToken") or "").strip()
    if not token or not refresh:
        raise RuntimeError("Rezzie login response did not include a new token pair")
    user = _user_record(payload)
    store = {
        "state": {
            "user": user,
            "isAuthenticated": True,
            "currentWholesaler": None,
            "companyActingAs": "company",
        },
        "version": 0,
    }
    return {
        "token": token,
        "refresh_token": refresh,
        "user_json": json.dumps(user),
        "store_json": json.dumps(store),
        "expires_at": _expires_at(token),
    }


def session_from_refresh(body: dict[str, Any], state_path: Path) -> dict[str, Any]:
    payload = _unwrap(body)
    token = str(payload.get("token") or "").strip()
    refresh = str(payload.get("refreshToken") or "").strip()
    if not token or not refresh:
        raise RuntimeError("Rezzie refresh response did not include a new token pair")
    return {
        "token": token,
        "refresh_token": refresh,
        "user_json": _storage_value(state_path, "rezzie_user_data"),
        "store_json": _storage_value(state_path, "rezzie-auth-store"),
        "expires_at": _expires_at(token),
    }


def _post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        API_BASE + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "RichieWebScraper/1.0",
            "X-App-Version": "0.1.0",
            "X-Client-Platform": "web",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Rezzie {path} failed ({exc.code}): {_error_message(exc)}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Rezzie {path} could not be reached: {exc.reason}") from exc
    try:
        body = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Rezzie {path} returned a non-JSON response") from exc
    if not isinstance(body, dict):
        raise RuntimeError(f"Rezzie {path} returned an unexpected response")
    return body


def _error_message(exc: urllib.error.HTTPError) -> str:
    raw = exc.read().decode("utf-8", errors="replace")
    message = raw[:180]
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict):
        message = str(parsed.get("message") or parsed.get("error") or message)
    message = " ".join(message.split())
    if "password" in message.lower():
        return "request rejected"
    return message[:180] or "request rejected"


def _unwrap(body: dict[str, Any]) -> dict[str, Any]:
    nested = body.get("data")
    if isinstance(nested, dict) and (nested.get("token") or nested.get("refreshToken")):
        return nested
    return body


def _user_record(payload: dict[str, Any]) -> dict[str, Any]:
    user_type = payload.get("userType")
    user_id = payload.get("userId")
    first = payload.get("firstName") or ""
    last = payload.get("lastName") or ""
    first_login = payload.get("firstLogin")
    if first_login is None:
        first_login = bool(payload.get("isFirstLogin"))
    return {
        "userId": user_id,
        "id": user_id,
        "email": payload.get("email"),
        "name": f"{first} {last}".strip(),
        "firstName": first,
        "lastName": last,
        "userType": user_type,
        "role": _ROLE.get(str(user_type or "").upper(), "buyer"),
        "active": payload.get("active"),
        "approved": payload.get("approved"),
        "emailVerified": payload.get("emailVerified"),
        "firstLogin": first_login,
        "isActive": payload.get("active"),
        "companyId": payload.get("companyId") or (user_id if user_type == "COMPANY" else None),
        "companySubdomain": payload.get("subdomain", payload.get("companySubdomain")),
        "canActAsAgent": payload.get("canActAsAgent"),
        "wholesalerAccess": [],
        "currentWholesaler": "",
        "preferences": {
            "currency": "USD",
            "notifications": {"email": True, "sms": False, "push": False},
            "theme": "light",
        },
        "buyBox": {
            "name": "",
            "locations": [],
            "priceRange": {"min": 0, "max": 1_000_000},
            "maxDaysOnMarket": 365,
            "propertyTypes": [],
            "isActive": False,
        },
        "joinedDate": datetime.now(timezone.utc).isoformat(),
        "generalTermsAccepted": payload.get("generalTermsAccepted"),
        "wholesalerTermsAccepted": payload.get("wholesalerTermsAccepted"),
        "investorTermsAccepted": payload.get("investorTermsAccepted"),
    }


def _expires_at(token: str) -> str:
    try:
        segment = token.split(".")[1]
        segment += "=" * (-len(segment) % 4)
        payload = json.loads(base64.urlsafe_b64decode(segment))
        exp = payload.get("exp")
        if isinstance(exp, (int, float)):
            return datetime.fromtimestamp(exp, timezone.utc).isoformat()
    except Exception:
        pass
    return "unknown"


def _storage_value(state_path: Path, name: str) -> str:
    if not state_path.exists():
        return ""
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    for origin in state.get("origins") or []:
        if not isinstance(origin, dict) or origin.get("origin") != ORIGIN:
            continue
        for item in origin.get("localStorage") or []:
            if isinstance(item, dict) and item.get("name") == name:
                return str(item.get("value") or "")
    return ""


def _merge_store(items: list[Any], store_json: str) -> str:
    """Keep the previous wholesaler context when replacing the auth store."""
    try:
        new_store = json.loads(store_json)
    except json.JSONDecodeError:
        return store_json
    if not isinstance(new_store, dict):
        return store_json
    current = next((item for item in items if isinstance(item, dict) and item.get("name") == "rezzie-auth-store"), None)
    if not current:
        return json.dumps(new_store)
    try:
        old_store = json.loads(current.get("value") or "{}")
    except json.JSONDecodeError:
        return json.dumps(new_store)
    if not isinstance(old_store, dict):
        return json.dumps(new_store)
    if "version" in old_store:
        new_store["version"] = old_store["version"]
    old_state = old_store.get("state") if isinstance(old_store.get("state"), dict) else {}
    new_state = new_store.setdefault("state", {})
    if isinstance(new_state, dict):
        if old_state.get("currentWholesaler"):
            new_state["currentWholesaler"] = old_state["currentWholesaler"]
        if old_state.get("companyActingAs"):
            new_state["companyActingAs"] = old_state["companyActingAs"]
    return json.dumps(new_store)
