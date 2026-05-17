#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-python3}"
LOG_DIR="$PROJECT_ROOT/logs"

mkdir -p "$LOG_DIR"

timestamp="$(date +%Y%m%d_%H%M%S)"
log_file="$LOG_DIR/olx_phone_enrichment_$timestamp.log"
pid_file="$LOG_DIR/olx_phone_enrichment.pid"

cd "$PROJECT_ROOT"

nohup "$PYTHON" scripts/enrich_olx_phones_browser.py \
  --headless \
  --manual-wait 0 \
  --phone-wait 15 \
  --sleep 5 \
  "$@" > "$log_file" 2>&1 &

pid="$!"
echo "$pid" > "$pid_file"

echo "Started OLX phone enrichment"
echo "PID: $pid"
echo "PID file: $pid_file"
echo "Log file: $log_file"
