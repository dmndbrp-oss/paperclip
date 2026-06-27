#!/usr/bin/env bash
# Run qwen3-coder:30b bench then notify Paperclip issue with result.
set -euo pipefail
BENCH_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG="$BENCH_DIR/completion-notifier.log"
ISSUE_ID="bfcc0267-47a8-466c-b381-a8d4fa2e4480"
API_URL="http://localhost:3100"
API_KEY="${PAPERCLIP_API_KEY:-}"

echo "=== completion-notifier started $(date -u +%Y-%m-%dT%H:%M:%SZ) ===" | tee -a "$LOG"

# Run qwen3-coder:30b bench
echo "[$(date -u +%H:%M:%SZ)] Starting qwen3-coder:30b bench..." | tee -a "$LOG"
if BENCH_MODELS="qwen3-coder:30b" BENCH_OUT="results-sag2554-qwen3coder.json" \
   python3 "$BENCH_DIR/run.py" 2>&1 | tee -a "$LOG"; then
  STATUS="success"
  echo "[$(date -u +%H:%M:%SZ)] qwen3-coder bench COMPLETE" | tee -a "$LOG"
else
  STATUS="error"
  echo "[$(date -u +%H:%M:%SZ)] qwen3-coder bench FAILED" | tee -a "$LOG"
fi

# Parse results if available
RESULT_LINE=""
if [ -f "$BENCH_DIR/results-sag2554-qwen3coder.json" ]; then
  RESULT_LINE=$(python3 -c "
import json
from pathlib import Path
d = json.loads(Path('$BENCH_DIR/results-sag2554-qwen3coder.json').read_text())
for r in d.get('rows', []):
    print(f\"{r['model']}: single {r['single_tool']} ({r['single_pct']}%)  multi {r['multi_tool']} ({r['multi_pct']}%)  tok/s {r.get('tok_per_sec')}\")
" 2>/dev/null || echo "parse error")
fi

# Post notification to Paperclip issue
if [ -n "\$API_KEY" ]; then
  BODY="qwen3-coder:30b bench \$STATUS. \$RESULT_LINE Ready to merge + commit results-sag2553.json."
  curl -s -X POST "\$API_URL/api/issues/\$ISSUE_ID/comments" \
    -H "Authorization: Bearer \$API_KEY" \
    -H "Content-Type: application/json" \
    -d "{\"body\": \"\$BODY\"}" \
    >> "\$LOG" 2>&1 && echo "[$(date -u +%H:%M:%SZ)] Paperclip comment posted" | tee -a "\$LOG"
else
  echo "[$(date -u +%H:%M:%SZ)] WARNING: PAPERCLIP_API_KEY not set" | tee -a "\$LOG"
fi

echo "=== completion-notifier DONE $(date -u +%Y-%m-%dT%H:%M:%SZ) ===" | tee -a "\$LOG"
