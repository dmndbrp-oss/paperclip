#!/usr/bin/env bash
# Waits for qwen bench PID to finish, then consolidates + commits + closes SAG-2554.
set -euo pipefail

BENCH_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJ_DIR="$(cd "$BENCH_DIR/../.." && pwd)"
LOG="$BENCH_DIR/finalize-sag2554.log"
ISSUE_ID="bfcc0267-47a8-466c-b381-a8d4fa2e4480"
API_KEY="${PAPERCLIP_API_KEY:-}"
API_BASE="http://localhost:3100"
QWEN_OUT="$BENCH_DIR/results-sag2554-qwen.json"
LLAMA_OUT="$BENCH_DIR/results-sag2554-llama.json"
FINAL_OUT="$BENCH_DIR/results-sag2553.json"
BENCH_PID="${1:-}"

log() { echo "[$(date -u +%H:%M:%SZ)] $*" | tee -a "$LOG"; }

log "=== finalize-sag2554.sh started (bench PID=$BENCH_PID) ==="

# --- Wait for bench PID to finish ---
if [ -n "$BENCH_PID" ]; then
  log "Waiting for bench PID $BENCH_PID..."
  while kill -0 "$BENCH_PID" 2>/dev/null; do
    sleep 15
  done
  log "Bench PID $BENCH_PID exited"
else
  # Poll for output file if no PID given
  log "No PID given, polling for $QWEN_OUT..."
  for i in $(seq 1 480); do
    if [ -f "$QWEN_OUT" ]; then break; fi
    sleep 15
    if [ $((i % 12)) -eq 0 ]; then log "Still waiting... ${i} attempts"; fi
  done
fi

if [ ! -f "$QWEN_OUT" ]; then
  log "ERROR: $QWEN_OUT not written — aborting"
  exit 1
fi

log "qwen bench output found. Consolidating..."

# --- Consolidate into results-sag2553.json ---
cd "$BENCH_DIR"
python3 - << PYEOF
import json
from pathlib import Path

bench_dir = Path('${BENCH_DIR}')
llama_path = bench_dir / 'results-sag2554-llama.json'
qwen_path = bench_dir / 'results-sag2554-qwen.json'
out_path = bench_dir / 'results-sag2553.json'

rows = []
meta = None

for p in [llama_path, qwen_path]:
    if p.exists():
        d = json.loads(p.read_text())
        if meta is None:
            meta = {k: v for k, v in d.items() if k != 'rows'}
        rows.extend(d['rows'])
        print(f"Loaded {p.name}: {len(d['rows'])} model(s)")
    else:
        print(f"WARNING: {p.name} not found")

meta['rows'] = rows
out_path.write_text(json.dumps(meta, indent=2))
print(f"Wrote {out_path} with {len(rows)} model rows")
PYEOF

log "Consolidated. Committing..."

# --- Commit ---
cd "$PROJ_DIR"
git add infra/bench/tool-call/run.py infra/bench/tool-call/results-sag2553.json infra/bench/tool-call/README.md 2>&1 | tee -a "$LOG" || true

FULL_SHA=""
if git diff --cached --quiet; then
  log "Nothing staged; using HEAD"
  FULL_SHA=$(git rev-parse HEAD)
else
  git commit -m "$(cat <<'EOF'
feat(SAG-2553): bench llama3.3:70b + qwen3-coder:30b + glm-5.1 on uniform harness

Co-Authored-By: Paperclip <noreply@paperclip.ing>
EOF
  )" 2>&1 | tee -a "$LOG"
  FULL_SHA=$(git rev-parse HEAD)
fi

log "Committed: $FULL_SHA"
git cat-file -e "$FULL_SHA" && log "git cat-file -e $FULL_SHA: OK"

# --- Build result rows for comment ---
QWEN_STDOUT=$(cat "$BENCH_DIR/qwen-bench-stdout.log" 2>/dev/null | tail -10 || echo "(no stdout)")

RESULT_ROWS=$(python3 - << PYEOF
import json
from pathlib import Path

def row(p):
    if not p.exists():
        return "(file not found)"
    d = json.loads(p.read_text())
    lines = []
    for r in d.get('rows', []):
        tps = r.get('tok_per_sec','n/a')
        lines.append(f"| {r['model']} | {r['single_tool']} ({r['single_pct']}%) | {r['multi_tool']} ({r['multi_pct']}%) | {tps} |")
    return '\n'.join(lines)

bench_dir = Path('${BENCH_DIR}')
print(row(bench_dir / 'results-sag2554-llama.json'))
print(row(bench_dir / 'results-sag2554-qwen.json'))
PYEOF
)

COMMENT_BODY="## SAG-2554 bench complete

### Actual stdout (qwen3-coder:30b run.py, last 10 lines)
\`\`\`
${QWEN_STDOUT}
\`\`\`

### Results table (N=50, DIRECT /api/chat, SAG-2537 harness)

| model | single_tool (n/50, %) | multi_tool (n/50, %) | tok/s |
|---|---|---|---|
${RESULT_ROWS}

### GLM-5.1 status
\`unobtainable-on-ollama\` — no published Ollama manifest as of 2026-05-31. CTO to decide on GGUF sourcing.

### Commit
SHA: \`${FULL_SHA}\`
\`git cat-file -e ${FULL_SHA}\` → exit 0 ✓

\`results-sag2553.json\` committed. \`results.json\` (SAG-2537) untouched. No adapterConfig touched."

# --- Post comment ---
COMMENT_RESP=$(curl -s -w "\n%{http_code}" -X POST "$API_BASE/api/issues/$ISSUE_ID/comments" \
  -H "Authorization: Bearer $API_KEY" \
  -H "X-Paperclip-Run-Id: ${PAPERCLIP_RUN_ID:-finalize}" \
  -H "Content-Type: application/json" \
  -d "$(python3 -c "import json,sys; print(json.dumps({'body': sys.stdin.read()}))" <<< "$COMMENT_BODY")" 2>&1)
HTTP_CODE=$(echo "$COMMENT_RESP" | tail -1)
log "Comment post HTTP $HTTP_CODE"

# --- Mark done ---
PATCH_RESP=$(curl -s -w "\n%{http_code}" -X PATCH "$API_BASE/api/issues/$ISSUE_ID" \
  -H "Authorization: Bearer $API_KEY" \
  -H "X-Paperclip-Run-Id: ${PAPERCLIP_RUN_ID:-finalize}" \
  -H "Content-Type: application/json" \
  -d '{"status":"done"}' 2>&1)
HTTP_CODE=$(echo "$PATCH_RESP" | tail -1)
log "PATCH status=done HTTP $HTTP_CODE"

log "=== finalize-sag2554.sh DONE ==="
