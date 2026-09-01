"""Florida Off Market provider definition.

Its production scraper remains available through the existing
``scrape_fom.py`` and ``extract_fom.py`` entry points. The provider hooks are
intentionally the next migration seam, so the shared engine can replace those
entry points incrementally without changing the scheduled job today.
"""

from __future__ import annotations

from typing import Any

from .base import ScraperProvider


class FloridaOffMarketProvider(ScraperProvider):
    name = "florida_off_market"
    base_url = "https://floridaoffmarket.mysharetribe.com"
    session_filename = "florida_off_market.json"

    def authenticate(self, page: Any, timeout_ms: int) -> None:
        raise NotImplementedError(
            "Move Florida Off Market authentication from scrape_fom.py into this provider"
        )

    def collect_listings(self, page: Any, timeout_ms: int, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        raise NotImplementedError(
            "Move Florida Off Market listing collection from scrape_fom.py into this provider"
        )

    def extract_listing(self, page: Any, listing: dict[str, Any], timeout_ms: int) -> dict[str, Any]:
        raise NotImplementedError(
            "Move Florida Off Market detail extraction from extract_fom.py into this provider"
        )
