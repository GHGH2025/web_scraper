"""Run the complete headless Rezzie scrape and queue accepted web listings."""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from db import (
    MAX_JOB_SAMPLE,
    ensure_indexes,
    persist_rezzie_job_run,
    ping,
    promote_to_filtered,
    upsert_raw_listings,
)
from providers import RezzieProvider
from scraper_engine import ScraperEngine

SOURCE = RezzieProvider.name
JOB_TZ = ZoneInfo("America/New_York")
log = logging.getLogger("rezzie_job")


def _target_date(when: datetime | None = None) -> str:
    stamp = when or datetime.now(JOB_TZ)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=ZoneInfo("UTC")).astimezone(JOB_TZ)
    else:
        stamp = stamp.astimezone(JOB_TZ)
    return stamp.date().isoformat()


def _extraction_errors(extracted: list[dict[str, Any]]) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    for item in extracted:
        err = item.get("error")
        if not err:
            continue
        errors.append(
            {
                "listing_id": item.get("listing_id"),
                "url": item.get("url"),
                "title": item.get("title") or item.get("address"),
                "error": str(err)[:500],
            }
        )
        if len(errors) >= MAX_JOB_SAMPLE:
            break
    return errors


def run_job(*, timeout_ms: int = 45000) -> dict[str, Any]:
    """Scrape every accessible Rezzie listing, then write raw and queue records."""
    started = datetime.utcnow()
    started_mono = time.monotonic()
    result: dict[str, Any] = {
        "source": SOURCE,
        "run_at": started,
        "target_date": _target_date(),
        "ok": False,
        "error": None,
        "started_at": started,
        "finished_at": None,
        "duration_sec": None,
        "timeout_ms": timeout_ms,
        "card_count": 0,
        "extracted_count": 0,
        "extraction_failed": 0,
        "extraction_errors": [],
        "raw": {},
        "queued": {},
        "queued_listings": [],
    }

    try:
        ping()
        ensure_indexes()
        engine = ScraperEngine(RezzieProvider(), headed=False, timeout_ms=timeout_ms)
        cards = engine.scrape()
        extracted = engine.extract(cards)
        listings = [item for item in extracted if not item.get("error")]
        raw = upsert_raw_listings(listings, source=SOURCE)
        queued = promote_to_filtered(listings, source=SOURCE)
        queued_listings = list(queued.pop("inserted_listings", []) or [])

        result.update(
            {
                "ok": True,
                "card_count": len(cards),
                "extracted_count": len(listings),
                "extraction_failed": len(extracted) - len(listings),
                "extraction_errors": _extraction_errors(extracted),
                "raw": raw,
                "queued": queued,
                "queued_listings": queued_listings,
            }
        )
        log.info("Rezzie job complete: %s", {k: result[k] for k in (
            "ok", "card_count", "extracted_count", "extraction_failed", "raw", "queued"
        )})
        return result
    except Exception as exc:
        result["ok"] = False
        result["error"] = str(exc)[:1000]
        log.exception("Rezzie job failed: %s", exc)
        raise
    finally:
        finished = datetime.utcnow()
        result["finished_at"] = finished
        result["duration_sec"] = round(time.monotonic() - started_mono, 2)
        try:
            persist_rezzie_job_run(result)
        except Exception:
            log.exception("Failed to persist Rezzie job run")
