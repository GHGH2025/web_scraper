#!/usr/bin/env bash
# Smoke-test the Rezzie provider against the live buyer dashboard.
#
#   ./test_rezzie.sh                    # headless browser, extract all listings
#   ./test_rezzie.sh --cards-only       # collect cards without detail pages
#   ./test_rezzie.sh --limit 5
#
# Requires scraper/.env with REZZIE_EMAIL and REZZIE_PASSWORD.

set -euo pipefail

cd "$(dirname "$0")"

log() {
  echo "$(date '+%Y-%m-%d %H:%M:%S') [test_rezzie] $*"
}

if [ -x ".venv/bin/python" ]; then
  PYTHON=".venv/bin/python"
elif [ -x ".venv/Scripts/python.exe" ]; then
  PYTHON=".venv/Scripts/python.exe"
elif [ -x "venv/bin/python" ]; then
  PYTHON="venv/bin/python"
elif [ -x "venv/Scripts/python.exe" ]; then
  PYTHON="venv/Scripts/python.exe"
else
  log "ERROR: No virtual environment found."
  log "Create one and install requirements plus Chromium first."
  exit 1
fi

if [ ! -f ".env" ]; then
  log "ERROR: Missing scraper/.env. Copy .env.example and set REZZIE_EMAIL / REZZIE_PASSWORD."
  exit 1
fi

CARDS_ONLY=false
# An empty limit means every accessible listing. Use --limit only for a
# smaller local smoke test.
LIMIT=""
OUT="${OUT:-data/rezzie_test.json}"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --cards-only) CARDS_ONLY=true ;;
    --limit)
      shift
      if [ "$#" -eq 0 ]; then
        log "ERROR: --limit requires a number."
        exit 2
      fi
      LIMIT="$1"
      ;;
    --out)
      shift
      if [ "$#" -eq 0 ]; then
        log "ERROR: --out requires a path."
        exit 2
      fi
      OUT="$1"
      ;;
    *)
      log "ERROR: Unknown argument: $1"
      exit 2
      ;;
  esac
  shift
done

mkdir -p "$(dirname "$OUT")"
export REZZIE_TEST_CARDS_ONLY="$CARDS_ONLY"
export REZZIE_TEST_LIMIT="$LIMIT"
export REZZIE_TEST_OUT="$OUT"

log "Python=${PYTHON} headless=true cards_only=${CARDS_ONLY} limit=${LIMIT:-all}"
log "Output=${OUT}"

"$PYTHON" -c '
import json
import os
from pathlib import Path

from providers import get_provider
from scraper_engine import ScraperEngine

cards_only = os.environ["REZZIE_TEST_CARDS_ONLY"].lower() == "true"
limit_text = os.environ["REZZIE_TEST_LIMIT"].strip()
limit = max(0, int(limit_text)) if limit_text else None
output = Path(os.environ["REZZIE_TEST_OUT"])

engine = ScraperEngine(get_provider("rezzie"), headed=False)
cards = engine.scrape()
selected = cards if limit is None else cards[:limit]
details = [] if cards_only or not selected else engine.extract(selected)
payload = {
    "provider": "rezzie",
    "card_count": len(cards),
    "cards": cards,
    "details_extracted": len(details),
    "details": details,
}
output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
print(f"Collected {len(cards)} cards; extracted {len(details)} details")
print(f"Wrote {output.resolve()}")
if not cards:
    raise SystemExit(
        "No Rezzie listing cards found. Confirm the buyer account has matched "
        "properties, then check the dashboard listing URL pattern."
    )
'

log "Rezzie smoke test finished successfully."
