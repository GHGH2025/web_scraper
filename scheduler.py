"""Keep the scraper process running and fire the Rezzie job at 1:00 AM.

Usage:
  python scheduler.py              # wait for 1:00 AM America/New_York
  python scheduler.py --now        # run immediately, then stay on the cron
  python scheduler.py --once       # run immediately and exit
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv

from db import close_client
from rezzie_job import run_job as run_rezzie_job
from scrape_fom import ROOT, log, setup_logging

load_dotenv(ROOT / ".env")

DEFAULT_TZ = "America/New_York"
SCHEDULER_LOG_PATH = Path(__file__).resolve().parent / "logs" / "scheduler.log"
RETRY_SLEEP_SEC = 60


def _timezone() -> ZoneInfo:
    name = (os.getenv("SCHEDULER_TZ") or DEFAULT_TZ).strip() or DEFAULT_TZ
    return ZoneInfo(name)


def _run_named(name: str, fn, **kwargs):
    try:
        return fn(**kwargs)
    except Exception as exc:
        log.exception("%s failed; retrying once in %ss: %s", name, RETRY_SLEEP_SEC, exc)
        time.sleep(RETRY_SLEEP_SEC)
        try:
            return fn(**kwargs)
        except Exception as retry_exc:
            log.exception("%s failed again; continuing: %s", name, retry_exc)
            return None


def _scheduled_job(timeout_ms: int) -> object:
    log.info("Cron fired — starting Rezzie headless web job")
    return _run_named("Rezzie", run_rezzie_job, timeout_ms=timeout_ms)


def main() -> None:
    parser = argparse.ArgumentParser(description="APScheduler cron for the Rezzie web job")
    parser.add_argument("--now", action="store_true", help="Run the job once at startup, then keep the 1 AM cron")
    parser.add_argument("--once", action="store_true", help="Run the job once and exit (no scheduler)")
    parser.add_argument("--timeout", type=int, default=45000)
    parser.add_argument("--log-file", default=str(SCHEDULER_LOG_PATH))
    args = parser.parse_args()

    setup_logging(Path(args.log_file))
    tz = _timezone()
    log.info("Scheduler timezone=%s cron=01:00", tz.key)

    if args.once:
        try:
            rezzie = _scheduled_job(timeout_ms=args.timeout)
            if rezzie is None:
                sys.exit(1)
        finally:
            close_client()
        return

    scheduler = BlockingScheduler(timezone=tz)
    scheduler.add_job(
        _scheduled_job,
        CronTrigger(hour=1, minute=0, timezone=tz),
        kwargs={"timeout_ms": args.timeout},
        id="rezzie_daily_scrape",
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
            _scheduled_job(timeout_ms=args.timeout)
        except Exception as exc:
            log.exception("Startup job failed (scheduler will still keep running): %s", exc)

    log.info("Scheduler started. Daily job log: %s", SCHEDULER_LOG_PATH.resolve())
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Scheduler stopped")
    finally:
        close_client()


if __name__ == "__main__":
    main()
