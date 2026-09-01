"""Shared browser runner for website scraping providers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

try:  # Supports both `python scraper_engine.py` and `import scraper.scraper_engine`.
    from .providers.base import ScraperProvider
except ImportError:  # pragma: no cover - exercised by the script-style entry points.
    from providers.base import ScraperProvider


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
            try:
                page.goto(self.provider.base_url, wait_until="domcontentloaded", timeout=self.timeout_ms)
                self.provider.authenticate(page, self.timeout_ms)
                return self.provider.collect_listings(page, self.timeout_ms, filters)
            finally:
                self.session_root.mkdir(parents=True, exist_ok=True)
                context.storage_state(path=str(self.state_path))
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
            try:
                page.goto(self.provider.base_url, wait_until="domcontentloaded", timeout=self.timeout_ms)
                self.provider.authenticate(page, self.timeout_ms)
                extracted: list[dict[str, Any]] = []
                for item in listings:
                    try:
                        extracted.append(self.provider.extract_listing(page, item, self.timeout_ms))
                    except Exception as exc:
                        extracted.append({**item, "error": str(exc)})
                return extracted
            finally:
                self.session_root.mkdir(parents=True, exist_ok=True)
                context.storage_state(path=str(self.state_path))
                context.close()
                browser.close()
