#!/usr/bin/env bash
# Extract deal fields from URLs saved by the list job.
#
#   ./run_extract_fom.sh
#   COUNTY=broward ./run_extract_fom.sh
#   ./run_extract_fom.sh --limit 2 --headed

set -euo pipefail

cd "$(dirname "$0")"

log() {
  echo "$(date '+%Y-%m-%d %H:%M:%S') [run_extract_fom] $*"
}

if [ -x ".venv/bin/python" ]; then
  PYTHON=".venv/bin/python"
elif [ -x ".venv/Scripts/python.exe" ]; then
  PYTHON=".venv/Scripts/python.exe"
elif [ -x "venv/bin/python" ]; then
  PYTHON="venv/bin/python"
else
  log "ERROR: No venv found."
  exit 1
fi

STEM="${COUNTY:-all}"
IN="${IN:-data/listings_${STEM}.json}"
OUT="${OUT:-$IN}"
LOG_FILE="${LOG_FILE:-logs/extract_fom.log}"

mkdir -p data logs
log "Input=${IN}"
log "Updating objects in=${OUT}"
log "Starting extract..."

EXTRACT_ARGS=(--in "$IN" --out "$OUT" --log-file "$LOG_FILE")
if [ -n "${COUNTY:-}" ]; then
  EXTRACT_ARGS+=(--county "$COUNTY")
fi
"$PYTHON" extract_fom.py "${EXTRACT_ARGS[@]}" "$@"

log "Updated listings JSON: $(pwd)/${OUT}"
log "Saved log: $(pwd)/${LOG_FILE}"
