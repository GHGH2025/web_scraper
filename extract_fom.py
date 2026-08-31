"""Visit each deal URL from the list job and extract listing fields."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

from scrape_fom import (
    BASE_URL,
    DATA_DIR,
    LISTING_HREF_RE,
    LOGIN_PATH,
    PRICE_RE,
    SESSION_DIR,
    STATE_PATH,
    _dismiss_banners,
    _ensure_logged_in,
    _load_creds,
    log,
    setup_logging,
)

DEFAULT_LOG_PATH = Path(__file__).resolve().parent / "logs" / "extract_fom.log"
DEFAULT_IN = DATA_DIR / "listings_all.json"

DETAIL_LABELS = [
    "Garage / Carport",
    "Estimated Repairs",
    "Estimated ARV",
    "Year Built",
    "Lot Sq Ft",
    "Lot Acres",
    "Occupancy",
    "Sq-Ft",
    "County",
    "State",
    "Beds",
    "Baths",
    "Pool",
]

STOP_HEADINGS = [
    "Details",
    "Transaction Type",
    "Payment Methods",
    "Location",
    "About the Wholesaler",
    "Send Private Message",
]

PHOTO_COUNT_RE = re.compile(r"View large photos\s*\((\d+)\)", re.I)
PROFILE_RE = re.compile(r"Profile:\s*(.+)", re.I)


def _to_number(value: str | None):
    if value is None:
        return None
    cleaned = re.sub(r"[^\d.-]", "", str(value).strip())
    if cleaned in ("", "-", ".", "-."):
        return None
    try:
        if "." in cleaned:
            return float(cleaned)
        return int(cleaned)
    except ValueError:
        return None


def _price_usd(price_text: str | None):
    if not price_text:
        return None
    return _to_number(price_text)


def _slice_section(text: str, heading: str, stop: list[str]) -> str:
    pattern = rf"(?im)^[ \t]*{re.escape(heading)}[ \t]*$"
    match = re.search(pattern, text)
    if not match:
        return ""
    rest = text[match.end() :]
    others = [h for h in stop if h.lower() != heading.lower()]
    if others:
        stop_pat = r"(?im)^[ \t]*(" + "|".join(re.escape(h) for h in others) + r")[ \t]*$"
        stop_match = re.search(stop_pat, rest)
        if stop_match:
            rest = rest[: stop_match.start()]
    return rest.strip()


def _parse_details(blob: str) -> dict[str, str]:
    if not blob:
        return {}
    compact = re.sub(r"\s+", " ", blob).strip()
    labels = sorted(DETAIL_LABELS, key=len, reverse=True)
    pattern = "(" + "|".join(re.escape(label) for label in labels) + ")"
    parts = re.split(pattern, compact)
    parsed: dict[str, str] = {}
    i = 1
    while i < len(parts) - 1:
        label = parts[i].strip()
        value = parts[i + 1].strip()
        if label and value:
            parsed[label] = value
        i += 2
    return parsed


def _parse_multi_values(blob: str) -> list[str]:
    if not blob:
        return []
    values = []
    for line in blob.splitlines():
        item = line.strip(" \t-•")
        if item:
            values.append(item)
    return values


def _norm_url(url: str) -> str:
    return (url or "").strip().split("?")[0].rstrip("/").lower()


def _load_payload(path: Path) -> dict:
    log.info("Reading listings file %s", path)
    if not path.exists():
        raise FileNotFoundError(f"Listings file not found: {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise RuntimeError("Listings file must be a JSON object")
    listings = list(raw.get("listings") or [])
    seen = {_norm_url(item.get("url") or "") for item in listings}
    seen.discard("")
    for url in raw.get("links") or []:
        url = (url or "").strip()
        key = _norm_url(url)
        if not key or key in seen:
            continue
        seen.add(key)
        match = LISTING_HREF_RE.search(url)
        listings.append(
            {
                "listing_id": match.group(2) if match else None,
                "slug": match.group(1) if match else None,
                "url": url,
            }
        )
    raw["listings"] = listings
    log.info("Loaded %s listing objects", len(listings))
    return raw


def _write_payload(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _merge_extracted(listing: dict, extracted: dict) -> None:
    saved_url = listing.get("url")
    saved_card_text = listing.get("card_text")
    listing.update(extracted)
    if saved_url:
        listing["url"] = saved_url
    if saved_card_text:
        listing["card_text"] = saved_card_text
    if not listing.get("error"):
        listing.pop("error", None)


def _extract_images(page) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()

    def _add(src: str) -> None:
        src = (src or "").strip()
        if not src or src.startswith("data:"):
            return
        if src.startswith("//"):
            src = "https:" + src
        if src not in seen:
            seen.add(src)
            urls.append(src)

    for img in page.locator("img").all():
        _add(img.get_attribute("src") or "")
        srcset = img.get_attribute("srcset") or ""
        if srcset:
            _add(srcset.split(",")[0].strip().split(" ")[0])
    return urls


def _extract_deal(page, job: dict, timeout_ms: int) -> dict:
    url = job["url"]
    log.info("Opening deal %s", url)
    page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    _dismiss_banners(page)
    if LOGIN_PATH in (page.url or ""):
        raise RuntimeError("Redirected to login — session expired")

    try:
        page.get_by_role("heading", name="Details").first.wait_for(state="visible", timeout=timeout_ms)
    except PlaywrightTimeout:
        log.warning("Details heading not visible yet on %s — extracting what is on the page", url)

    title = None
    h1 = page.locator("h1")
    try:
        if h1.count() and h1.first.is_visible():
            title = h1.first.inner_text().strip() or None
    except Exception:
        title = None
    if not title:
        page_title = (page.title() or "").strip()
        title = re.sub(r"\s*\|\s*FloridaOffMarket\.com\s*$", "", page_title, flags=re.I).strip() or None
        log.info("Title from document.title: %s", title)

    try:
        main_text = page.locator("main").inner_text(timeout=8000)
    except Exception:
        main_text = page.inner_text("body")

    details_blob = _slice_section(main_text, "Details", STOP_HEADINGS)
    details = _parse_details(details_blob)
    transaction_type = _parse_multi_values(_slice_section(main_text, "Transaction Type", STOP_HEADINGS))
    payment_methods = _parse_multi_values(_slice_section(main_text, "Payment Methods", STOP_HEADINGS))

    description = ""
    details_match = re.search(r"(?im)^[ \t]*Details[ \t]*$", main_text)
    if details_match:
        raw_desc = main_text[: details_match.start()]
        raw_desc = re.sub(r"(?im)^View large photos.*$", "", raw_desc)
        if title:
            raw_desc = raw_desc.replace(title, "", 1)
        description = re.sub(r"\n{3,}", "\n\n", raw_desc).strip()

    price_match = PRICE_RE.search(main_text or "")
    price_text = price_match.group(0) if price_match else job.get("card_price") or job.get("price")

    photo_match = PHOTO_COUNT_RE.search(main_text or "")
    photo_count = int(photo_match.group(1)) if photo_match else None
    images = _extract_images(page)

    about = _slice_section(main_text, "About the Wholesaler", STOP_HEADINGS)
    wholesaler = None
    profile_match = PROFILE_RE.search(about)
    if profile_match:
        wholesaler = profile_match.group(1).strip().rstrip(".")
    wholesaler_bio = PROFILE_RE.sub("", about).strip() or None
    if wholesaler_bio:
        wholesaler_bio = re.split(r"(?i)\bView profile\b", wholesaler_bio, maxsplit=1)[0].strip() or None
        wholesaler_bio = re.sub(r"\u2026more\s*$", "", wholesaler_bio or "").strip() or None

    id_match = LISTING_HREF_RE.search(page.url or url)
    listing_id = job.get("listing_id") or (id_match.group(2) if id_match else None)

    deal = {
        "listing_id": listing_id,
        "url": page.url or url,
        "title": title,
        "address": title,
        "price": price_text,
        "price_usd": _price_usd(price_text),
        "description": description or None,
        "state": details.get("State"),
        "county": details.get("County"),
        "beds": _to_number(details.get("Beds")),
        "baths": _to_number(details.get("Baths")),
        "sqft": _to_number(details.get("Sq-Ft")),
        "lot_sqft": _to_number(details.get("Lot Sq Ft")),
        "lot_acres": _to_number(details.get("Lot Acres")),
        "year_built": _to_number(details.get("Year Built")),
        "estimated_arv": _to_number(details.get("Estimated ARV")),
        "estimated_repairs": _to_number(details.get("Estimated Repairs")),
        "occupancy": details.get("Occupancy"),
        "pool": details.get("Pool"),
        "garage_carport": details.get("Garage / Carport"),
        "transaction_type": transaction_type,
        "payment_methods": payment_methods,
        "wholesaler": wholesaler,
        "wholesaler_bio": wholesaler_bio,
        "photo_count": photo_count if photo_count is not None else len(images) or None,
        "images": images,
        "details_raw": details,
        "error": None,
    }
    log.info(
        "Extracted %s | %s | beds=%s baths=%s sqft=%s arv=%s repairs=%s",
        deal["title"],
        deal["price"],
        deal["beds"],
        deal["baths"],
        deal["sqft"],
        deal["estimated_arv"],
        deal["estimated_repairs"],
    )
    return deal


def run(
    listings: list[dict] | None = None,
    input_path: Path | None = None,
    out_path: Path | None = None,
    headed: bool = False,
    timeout_ms: int = 45000,
    limit: int | None = None,
    delay_sec: float = 0.6,
) -> dict:
    if listings is None:
        if input_path is None:
            raise RuntimeError("listings or input_path is required")
        log.info("=== extract start in=%s headed=%s ===", input_path, headed)
        payload = _load_payload(input_path)
        listings = list(payload.get("listings") or [])
    else:
        listings = list(listings)
        log.info("=== extract start in-memory=%s headed=%s ===", len(listings), headed)

    if limit is not None:
        listings = listings[: max(limit, 0)]
        log.info("Limit applied: %s deals", len(listings))
    if not listings:
        raise RuntimeError("No deal URLs found to extract")

    email, password = _load_creds()
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    ok = 0
    failed = 0

    with sync_playwright() as p:
        log.info("Launching Chromium headless=%s", not headed)
        browser = p.chromium.launch(headless=not headed)
        context_kwargs = {"viewport": {"width": 1400, "height": 900}}
        if STATE_PATH.exists():
            log.info("Reusing saved session %s", STATE_PATH)
            context_kwargs["storage_state"] = str(STATE_PATH)
        else:
            log.info("No saved session — will log in fresh")
        context = browser.new_context(**context_kwargs)
        page = context.new_page()
        page.set_default_timeout(timeout_ms)

        page.goto(BASE_URL, wait_until="domcontentloaded")
        _dismiss_banners(page)
        _ensure_logged_in(page, email, password, timeout_ms)

        for index, listing in enumerate(listings, start=1):
            url = listing.get("url")
            log.info("Deal %s/%s — extracting %s", index, len(listings), url)
            try:
                extracted = _extract_deal(page, listing, timeout_ms)
                _merge_extracted(listing, extracted)
                ok += 1
            except Exception as exc:
                failed += 1
                listing["error"] = str(exc)
                log.exception("Failed to extract %s: %s", url, exc)
            if out_path is not None:
                _write_payload(
                    out_path,
                    {
                        "listings": listings,
                        "extracted_ok": ok,
                        "extracted_failed": failed,
                    },
                )
            if index < len(listings) and delay_sec > 0:
                time.sleep(delay_sec)

        log.info("Saving browser session to %s", STATE_PATH)
        context.storage_state(path=str(STATE_PATH))
        context.close()
        browser.close()

    result = {
        "listings": listings,
        "extracted_ok": ok,
        "extracted_failed": failed,
    }
    if out_path is not None:
        _write_payload(out_path, result)
        log.info("Wrote %s listings (%s ok, %s failed) to %s", len(listings), ok, failed, out_path.resolve())
    log.info("Extracted %s listings (%s ok, %s failed)", len(listings), ok, failed)
    log.info("=== extract done ===")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract deal fields from Florida Off Market listing URLs")
    parser.add_argument("--in", dest="input_path", default=str(DEFAULT_IN), help="JSON from the list job")
    parser.add_argument("--out", default=None, help="JSON to update (default: same as --in)")
    parser.add_argument("--county", default=None, help="Optional label for default output filename")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--timeout", type=int, default=45000)
    parser.add_argument("--limit", type=int, default=None, help="Max deals to extract (for testing)")
    parser.add_argument("--delay", type=float, default=0.6, help="Seconds between deal pages")
    parser.add_argument("--log-file", default=str(DEFAULT_LOG_PATH))
    args = parser.parse_args()

    setup_logging(Path(args.log_file))
    log.info("Log file: %s", Path(args.log_file).resolve())

    input_path = Path(args.input_path)
    out_path = Path(args.out) if args.out else None

    try:
        run(
            input_path=input_path,
            out_path=out_path,
            headed=args.headed,
            timeout_ms=args.timeout,
            limit=args.limit,
            delay_sec=args.delay,
        )
    except PlaywrightTimeout as exc:
        log.exception("Timed out: %s", exc)
        sys.exit(2)
    except Exception as exc:
        log.exception("Extract failed: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
