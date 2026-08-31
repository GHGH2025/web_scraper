"""Run scrape + extract, then save deals to Mongo raw/filtered."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeout

from db import close_client, ensure_indexes, ping, promote_to_filtered, upsert_raw_listings
from extract_fom import run as extract_run
from scrape_fom import log, run as scrape_run, setup_logging

JOB_LOG_PATH = Path(__file__).resolve().parent / "logs" / "fom_job.log"


def run_job(
    headed: bool = False,
    county: str | None = None,
    timeout_ms: int = 45000,
    delay_sec: float = 0.6,
    limit: int | None = None,
) -> dict:
    log.info("=== daily job start county=%s headed=%s ===", county or "all", headed)
    ping()
    ensure_indexes()

    scrape_result = scrape_run(
        headed=headed,
        county=county,
        timeout_ms=timeout_ms,
    )
    cards = list(scrape_result.get("listings") or [])
    log.info("Scrape collected %s deals", scrape_result.get("card_count"))
    if not cards:
        log.warning("No deals scraped — skipping extract")
        empty = {"inserted": 0, "modified": 0, "skipped": 0, "total": 0}
        return {
            "scrape": scrape_result,
            "extract_ok": 0,
            "extract_failed": 0,
            "raw": empty,
            "filtered": {
                "inserted": 0,
                "skipped_pending": 0,
                "skipped_posted_30d": 0,
                "skipped_no_address": 0,
                "skipped_no_id": 0,
                "total": 0,
            },
        }

    extract_result = extract_run(
        listings=cards,
        headed=headed,
        timeout_ms=timeout_ms,
        limit=limit,
        delay_sec=delay_sec,
    )
    listings = list(extract_result.get("listings") or [])
    raw_stats = upsert_raw_listings(listings)
    filter_stats = promote_to_filtered(listings)
    log.info("=== daily job done raw=%s filtered=%s ===", raw_stats, filter_stats)
    return {
        "scrape": scrape_result,
        "extract_ok": extract_result.get("extracted_ok"),
        "extract_failed": extract_result.get("extracted_failed"),
        "raw": raw_stats,
        "filtered": filter_stats,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape + extract Florida Off Market deals and save to MongoDB")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--county", default=None)
    parser.add_argument("--timeout", type=int, default=45000)
    parser.add_argument("--delay", type=float, default=0.6)
    parser.add_argument("--limit", type=int, default=None, help="Max deals to extract (for testing)")
    parser.add_argument("--log-file", default=str(JOB_LOG_PATH))
    args = parser.parse_args()
    setup_logging(Path(args.log_file))
    county = (args.county or "").strip() or None

    try:
        run_job(
            headed=args.headed,
            county=county,
            timeout_ms=args.timeout,
            delay_sec=args.delay,
            limit=args.limit,
        )
    except PlaywrightTimeout as exc:
        log.exception("Timed out: %s", exc)
        sys.exit(2)
    except Exception as exc:
        log.exception("Daily job failed: %s", exc)
        sys.exit(1)
    finally:
        close_client()


if __name__ == "__main__":
    main()
