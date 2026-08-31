#!/usr/bin/env bash
# Login to Florida Off Market and write listing cards to JSON.
#
#   ./run_scrape_fom.sh
#   ./run_scrape_fom.sh --headed
#   COUNTY=miami-dade ./run_scrape_fom.sh   # optional county filter
#
# Requires scraper/.env with FOM_EMAIL and FOM_PASSWORD.

set -euo pipefail

cd "$(dirname "$0")"

log() {
  echo "$(date '+%Y-%m-%d %H:%M:%S') [run_scrape_fom] $*"
}

log "Working directory: $(pwd)"

if [ -x ".venv/bin/python" ]; then
  PYTHON=".venv/bin/python"
elif [ -x ".venv/Scripts/python.exe" ]; then
  PYTHON=".venv/Scripts/python.exe"
elif [ -x "venv/bin/python" ]; then
  PYTHON="venv/bin/python"
else
  log "ERROR: No venv found."
  log "  python -m venv .venv && .venv/bin/pip install -r requirements.txt && .venv/bin/python -m playwright install chromium"
  exit 1
fi
log "Python: ${PYTHON}"

if [ ! -f ".env" ]; then
  log "ERROR: Missing .env — copy .env.example and set FOM_EMAIL / FOM_PASSWORD."
  exit 1
fi
log "Found .env"

STEM="${COUNTY:-all}"
mkdir -p data logs
OUT="${OUT:-data/listings_${STEM}.json}"
LOG_FILE="${LOG_FILE:-logs/scrape_fom.log}"

log "Source=https://floridaoffmarket.mysharetribe.com/s"
log "County filter=${COUNTY:-none}"
log "JSON out=${OUT}"
log "Log file=${LOG_FILE}"
log "Starting scrape..."

DETAILS_OUT="${DETAILS_OUT:-$OUT}"
EXTRACT_LOG="${EXTRACT_LOG:-logs/extract_fom.log}"

SCRAPE_ARGS=(--out "$OUT" --log-file "$LOG_FILE")
if [ -n "${COUNTY:-}" ]; then
  SCRAPE_ARGS+=(--county "$COUNTY")
fi

set +e
"$PYTHON" scrape_fom.py "${SCRAPE_ARGS[@]}" "$@"
status=$?
set -e

if [ "$status" -ne 0 ]; then
  log "ERROR: list job exited with status ${status}. See ${LOG_FILE}"
  exit "$status"
fi

log "List job finished OK"
log "Saved listings JSON: $(pwd)/${OUT}"
log "Saved deal links JSON: $(pwd)/data/links_${STEM}.json"
log "Starting extract job for each deal URL..."

set +e
EXTRACT_ARGS=(--in "$OUT" --out "$DETAILS_OUT" --log-file "$EXTRACT_LOG")
if [ -n "${COUNTY:-}" ]; then
  EXTRACT_ARGS+=(--county "$COUNTY")
fi
"$PYTHON" extract_fom.py "${EXTRACT_ARGS[@]}"
extract_status=$?
set -e

if [ "$extract_status" -eq 0 ]; then
  log "Extract job finished OK"
  log "Updated listings JSON: $(pwd)/${DETAILS_OUT}"
  log "Saved extract log:  $(pwd)/${EXTRACT_LOG}"
else
  log "ERROR: extract job exited with status ${extract_status}. See ${EXTRACT_LOG}"
  exit "$extract_status"
fi
