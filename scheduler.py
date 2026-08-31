"""Keep the scraper process running and fire the daily job at 1:00 AM.

Usage:
  python scheduler.py              # wait for 1:00 AM America/New_York
  python scheduler.py --now        # run immediately, then stay on the cron
  python scheduler.py --once       # run immediately and exit
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv

from db import close_client
from job import JOB_LOG_PATH, run_job
from scrape_fom import ROOT, log, setup_logging

load_dotenv(ROOT / ".env")

DEFAULT_TZ = "America/New_York"
SCHEDULER_LOG_PATH = Path(__file__).resolve().parent / "logs" / "scheduler.log"


def _timezone() -> ZoneInfo:
    name = (os.getenv("SCHEDULER_TZ") or DEFAULT_TZ).strip() or DEFAULT_TZ
    return ZoneInfo(name)


def _scheduled_job(headed: bool, county: str | None, timeout_ms: int, delay_sec: float) -> None:
    log.info("Cron fired — starting scrape + extract + raw/filtered Mongo write")
    run_job(
        headed=headed,
        county=county,
        timeout_ms=timeout_ms,
        delay_sec=delay_sec,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="APScheduler cron for the Florida Off Market scrape job")
    parser.add_argument("--now", action="store_true", help="Run the job once at startup, then keep the 1 AM cron")
    parser.add_argument("--once", action="store_true", help="Run the job once and exit (no scheduler)")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--county", default=None)
    parser.add_argument("--timeout", type=int, default=45000)
    parser.add_argument("--delay", type=float, default=0.6)
    parser.add_argument("--log-file", default=str(SCHEDULER_LOG_PATH))
    args = parser.parse_args()

    setup_logging(Path(args.log_file))
    tz = _timezone()
    county = (args.county or "").strip() or None
    log.info("Scheduler timezone=%s cron=01:00", tz.key)

    if args.once:
        try:
            run_job(
                headed=args.headed,
                county=county,
                timeout_ms=args.timeout,
                delay_sec=args.delay,
            )
        except Exception as exc:
            log.exception("One-shot job failed: %s", exc)
            sys.exit(1)
        finally:
            close_client()
        return

    scheduler = BlockingScheduler(timezone=tz)
    scheduler.add_job(
        _scheduled_job,
        CronTrigger(hour=1, minute=0, timezone=tz),
        kwargs={
            "headed": args.headed,
            "county": county,
            "timeout_ms": args.timeout,
            "delay_sec": args.delay,
        },
        id="fom_daily_scrape",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )
    next_run = scheduler.get_jobs()[0].trigger.get_next_fire_time(None, datetime.now(tz))
    log.info("Next run: %s", next_run)

    if args.now:
        log.info("--now: running job immediately before waiting for cron")
        try:
            run_job(
                headed=args.headed,
                county=county,
                timeout_ms=args.timeout,
                delay_sec=args.delay,
            )
        except Exception as exc:
            log.exception("Startup job failed (scheduler will still keep running): %s", exc)

    log.info("Scheduler started. Daily job log: %s", JOB_LOG_PATH.resolve())
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Scheduler stopped")
    finally:
        close_client()


if __name__ == "__main__":
    main()
