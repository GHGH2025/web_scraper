"""Provider contract used by the shared scraper engine.

The engine owns browser lifecycle, sessions, and common error handling. A
provider owns everything that varies by website: URLs, authentication, page
selectors, pagination, and field extraction.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ScraperProvider(ABC):
    """Website-specific implementation consumed by :class:`ScraperEngine`."""

    name: str
    base_url: str
    session_filename: str

    @abstractmethod
    def authenticate(self, page: Any, timeout_ms: int) -> None:
        """Ensure ``page`` is authenticated, if the site requires it."""

    @abstractmethod
    def collect_listings(
        self,
        page: Any,
        timeout_ms: int,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Collect lightweight listing records and detail URLs."""

    @abstractmethod
    def extract_listing(
        self,
        page: Any,
        listing: dict[str, Any],
        timeout_ms: int,
    ) -> dict[str, Any]:
        """Extract the normalized fields from one detail page."""
