"""Shared paths and logging for the scraper process."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "logs"

log = logging.getLogger("scraper")


def setup_logging(log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-5s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(fmt)
    for name in ("scraper", "rezzie", "rezzie_job"):
        named = logging.getLogger(name)
        named.setLevel(logging.DEBUG)
        named.handlers.clear()
        named.addHandler(file_handler)
        named.addHandler(console)
        named.propagate = False
