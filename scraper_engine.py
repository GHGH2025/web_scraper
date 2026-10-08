"""Shared browser runner for website scraping providers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

try:  # Supports both `python scraper_engine.py` and `import scraper.scraper_engine`.
    from .providers.base import ScraperProvider
except ImportError:  # pragma: no cover - exercised by the script-style entry points.
    from providers.base import ScraperProvider


def _is_auth_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(token in msg for token in ("login", "auth", "sign in", "sign-in", "mfa", "captcha"))


class ScraperEngine:
    """Run a provider while keeping browser/session concerns in one place."""

    def __init__(
        self,
        provider: ScraperProvider,
        *,
        session_root: Path | None = None,
        headed: bool = False,
        timeout_ms: int = 45000,
    ) -> None:
        self.provider = provider
        self.session_root = session_root or Path(__file__).resolve().parent / ".session"
        self.headed = headed
        self.timeout_ms = timeout_ms

    @property
    def state_path(self) -> Path:
        return self.session_root / self.provider.session_filename

    def scrape(
        self,
        *,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Authenticate and collect listing cards through the provider."""
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=not self.headed)
            context_options: dict[str, Any] = {"viewport": {"width": 1400, "height": 900}}
            if self.state_path.exists():
                context_options["storage_state"] = str(self.state_path)
            context = browser.new_context(**context_options)
            page = context.new_page()
            page.set_default_timeout(self.timeout_ms)
            signed_in = False
            try:
                page.goto(self.provider.base_url, wait_until="domcontentloaded", timeout=self.timeout_ms)
                self.provider.authenticate(page, self.timeout_ms)
                cards = self.provider.collect_listings(page, self.timeout_ms, filters)
                signed_in = True
                return cards
            finally:
                self._save_session(context, signed_in)
                context.close()
                browser.close()

    def extract(
        self,
        listings: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Authenticate and extract each listing through the provider."""
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=not self.headed)
            context_options: dict[str, Any] = {"viewport": {"width": 1400, "height": 900}}
            if self.state_path.exists():
                context_options["storage_state"] = str(self.state_path)
            context = browser.new_context(**context_options)
            page = context.new_page()
            page.set_default_timeout(self.timeout_ms)
            signed_in = False
            try:
                page.goto(self.provider.base_url, wait_until="domcontentloaded", timeout=self.timeout_ms)
                self.provider.authenticate(page, self.timeout_ms)
                signed_in = True
                extracted: list[dict[str, Any]] = []
                for item in listings:
                    try:
                        extracted.append(self.provider.extract_listing(page, item, self.timeout_ms))
                    except Exception as exc:
                        if _is_auth_error(exc):
                            try:
                                self.provider.authenticate(page, self.timeout_ms)
                                extracted.append(self.provider.extract_listing(page, item, self.timeout_ms))
                                continue
                            except Exception as retry_exc:
                                extracted.append({**item, "error": str(retry_exc)})
                                continue
                        extracted.append({**item, "error": str(exc)})
                return extracted
            finally:
                self._save_session(context, signed_in)
                context.close()
                browser.close()

    def _save_session(self, context: Any, signed_in: bool) -> None:
        """Keep the saved browser session only after a real sign-in.

        A logged-out visit used to overwrite ``.session/rezzie.json``, so the
        next morning started on the public homepage again.
        """
        if not signed_in:
            return
        self.session_root.mkdir(parents=True, exist_ok=True)
        context.storage_state(path=str(self.state_path))
