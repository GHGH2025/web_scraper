"""Run the complete headless Rezzie scrape and queue accepted web listings."""

from __future__ import annotations

import logging
from typing import Any

from db import ensure_indexes, ping, promote_to_filtered, upsert_raw_listings
from providers import RezzieProvider
from scraper_engine import ScraperEngine

SOURCE = RezzieProvider.name
log = logging.getLogger("rezzie_job")


def run_job(*, timeout_ms: int = 45000) -> dict[str, Any]:
    """Scrape every accessible Rezzie listing, then write raw and queue records."""
    ping()
    ensure_indexes()
    engine = ScraperEngine(RezzieProvider(), headed=False, timeout_ms=timeout_ms)
    cards = engine.scrape()
    extracted = engine.extract(cards)
    listings = [item for item in extracted if not item.get("error")]
    raw = upsert_raw_listings(listings, source=SOURCE)
    queued = promote_to_filtered(listings, source=SOURCE)
    result = {
        "source": SOURCE,
        "card_count": len(cards),
        "extracted_count": len(listings),
        "extraction_failed": len(extracted) - len(listings),
        "raw": raw,
        "queued": queued,
    }
    log.info("Rezzie job complete: %s", result)
    return result
