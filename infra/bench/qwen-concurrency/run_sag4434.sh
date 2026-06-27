#!/usr/bin/env bash
# SAG-4434 off-hours runner for the qwen3:30b-a3b concurrency benchmark.
# Safe to fire on a cron: the harness self-gates on idle (exit 3 under load),
# this wrapper skips once results exist, and flock prevents overlapping runs.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG="$DIR/sag4434-cron.log"

# Already produced results? Nothing to do — leave the artifact for the heartbeat to post.
# SAG-4463: glob updated to sag4463 (harness now writes results-sag4463-*.json).
if ls "$DIR"/results-sag4463-*.json >/dev/null 2>&1; then
  echo "[$(date -Is)] results already present, skipping" >> "$LOG"
  exit 0
fi

echo "[$(date -Is)] attempting benchmark run" >> "$LOG"
# -n: fail fast if another attempt holds the lock (overlapping cron fire).
flock -n "$DIR/.bench.lock" python3 "$DIR/sag4434_bench.py" >> "$LOG" 2>&1
rc=$?
echo "[$(date -Is)] harness exit=$rc (3=deferred-under-load)" >> "$LOG"
exit "$rc"
