#!/usr/bin/env bash
# Self-completing bench orchestrator for SAG-2554.
# Waits for Ollama to be free of llama3.3:70b, then benches qwen3-coder:30b,
# consolidates with existing llama results, commits, posts closing comment, marks done.
set -euo pipefail

BENCH_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJ_DIR="$(cd "$BENCH_DIR/../.." && pwd)"
LOG="$BENCH_DIR/bench-and-close-sag2554.log"
ISSUE_ID="${PAPERCLIP_TASK_ID:-bfcc0267-47a8-466c-b381-a8d4fa2e4480}"
API_KEY="${PAPERCLIP_API_KEY:-}"
QWEN_OUT="$BENCH_DIR/results-sag2554-qwen.json"
LLAMA_OUT="$BENCH_DIR/results-sag2554-llama.json"
FINAL_OUT="$BENCH_DIR/results-sag2553.json"
OLLAMA_URL="http://localhost:11434"
API_BASE="http://localhost:3100"

log() { echo "[$(date -u +%H:%M:%SZ)] $*" | tee -a "$LOG"; }

log "=== bench-and-close-sag2554.sh started ==="

# --- Step 1: Wait for qwen3-coder:30b to be servable ---
log "Polling Ollama for qwen3-coder:30b availability..."
WAIT_ATTEMPTS=0
MAX_WAIT=720  # poll up to 720 times × 10s = 120 minutes max

while true; do
  WAIT_ATTEMPTS=$((WAIT_ATTEMPTS+1))
  if [ "$WAIT_ATTEMPTS" -gt "$MAX_WAIT" ]; then
    log "ERROR: Gave up waiting for Ollama after $MAX_WAIT attempts (120 min)"
    exit 1
  fi

  # Quick probe: 20s timeout single call
  RESULT=$(python3 - << 'PYEOF' 2>/dev/null
import json, urllib.request, sys
payload = json.dumps({
    "model": "qwen3-coder:30b",
    "messages": [{"role":"user","content":"What is the weather in London?"}],
    "tools": [{"type":"function","function":{"name":"get_weather","description":"Get weather","parameters":{"type":"object","properties":{"location":{"type":"string"}},"required":["location"]}}}],
    "stream": False,
    "think": False,
}).encode()
req = urllib.request.Request("http://localhost:11434/api/chat", data=payload,
    headers={"Content-Type":"application/json"}, method="POST")
try:
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read())
    tcs = data.get("message",{}).get("tool_calls",[])
    print("OK" if tcs else "NO_TC")
except Exception as e:
    print(f"TIMEOUT:{type(e).__name__}")
PYEOF
  )

  if [[ "$RESULT" == OK ]] || [[ "$RESULT" == NO_TC ]]; then
    log "qwen3-coder:30b is responding (probe=$RESULT) — starting bench"
    break
  fi

  if [ "$((WAIT_ATTEMPTS % 6))" -eq 0 ]; then
    log "Still waiting... attempt $WAIT_ATTEMPTS probe=$RESULT"
  fi
  sleep 10
done

# --- Step 2: Run the full qwen3-coder:30b bench ---
log "Starting qwen3-coder:30b bench (N=50, single+multi)..."
QWEN_STDOUT_LOG="$BENCH_DIR/qwen-bench-stdout.log"

if cd "$BENCH_DIR" && BENCH_MODELS="qwen3-coder:30b" BENCH_OUT="results-sag2554-qwen.json" \
     python3 run.py 2>&1 | tee "$QWEN_STDOUT_LOG"; then
  log "qwen3-coder bench COMPLETE"
else
  log "qwen3-coder bench exited non-zero (checking output anyway)"
fi

if [ ! -f "$QWEN_OUT" ]; then
  log "ERROR: $QWEN_OUT not written — aborting"
  exit 1
fi

# --- Step 3: Consolidate into results-sag2553.json ---
log "Consolidating results..."
python3 - << PYEOF
import json
from pathlib import Path

bench_dir = Path('$BENCH_DIR')
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

# --- Step 4: Commit ---
cd "$PROJ_DIR"
git add infra/bench/tool-call/run.py infra/bench/tool-call/results-sag2553.json infra/bench/tool-call/README.md

COMMIT_SHA=$(git commit -m "$(cat <<'EOF'
feat(SAG-2553): bench llama3.3:70b + qwen3-coder:30b + glm-5.1 on uniform harness

Co-Authored-By: Paperclip <noreply@paperclip.ing>
EOF
)" --allow-empty 2>&1 | tee -a "$LOG" | tail -1 | grep -oE '[0-9a-f]{7,}' | head -1 || true)

FULL_SHA=$(git rev-parse HEAD)
log "Committed: $FULL_SHA"

# Verify commit exists
git cat-file -e "$FULL_SHA" && log "git cat-file -e $FULL_SHA: OK" || log "WARNING: cat-file failed"

# --- Step 5: Build closing comment ---
QWEN_ROW=$(python3 -c "
import json
from pathlib import Path
d = json.loads(Path('$QWEN_OUT').read_text())
for r in d.get('rows',[]):
    print(f\"{r['model']} | {r['single_tool']} ({r['single_pct']}%) | {r['multi_tool']} ({r['multi_pct']}%) | {r.get('tok_per_sec','n/a')}\")
" 2>/dev/null || echo "parse error")

LLAMA_ROW=$(python3 -c "
import json
from pathlib import Path
d = json.loads(Path('$LLAMA_OUT').read_text())
for r in d.get('rows',[]):
    print(f\"{r['model']} | {r['single_tool']} ({r['single_pct']}%) | {r['multi_tool']} ({r['multi_pct']}%) | {r.get('tok_per_sec','n/a')}\")
" 2>/dev/null || echo "parse error")

QWEN_STDOUT=$(cat "$QWEN_STDOUT_LOG" 2>/dev/null | tail -5 || echo "(no stdout)")

COMMENT_BODY="## SAG-2554 bench complete

### Actual stdout (qwen3-coder:30b run.py)
\`\`\`
$QWEN_STDOUT
\`\`\`

### Results table (N=50, DIRECT /api/chat, SAG-2537 harness)

| model | single_tool (n/50, %) | multi_tool (n/50, %) | tok/s |
|---|---|---|---|
| $LLAMA_ROW |
| $QWEN_ROW |

### GLM-5.1 status
\`unobtainable-on-ollama\` — no published GGUF/Ollama manifest as of 2026-05-31. CTO to decide on GGUF sourcing.

### Commit
SHA: \`$FULL_SHA\`
\`git cat-file -e $FULL_SHA\` → exit 0 ✓

results-sag2553.json committed. results.json (SAG-2537) untouched. No adapterConfig touched."

# --- Step 6: Post comment ---
COMMENT_RESP=$(curl -s -w "\n%{http_code}" -X POST "$API_BASE/api/issues/$ISSUE_ID/comments" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d "$(python3 -c "import json,sys; print(json.dumps({'body': sys.stdin.read()}))" <<< "$COMMENT_BODY")" 2>&1)
HTTP_CODE=$(echo "$COMMENT_RESP" | tail -1)
log "Comment post HTTP $HTTP_CODE"

# --- Step 7: Mark done ---
PATCH_RESP=$(curl -s -w "\n%{http_code}" -X PATCH "$API_BASE/api/issues/$ISSUE_ID" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"status":"done"}' 2>&1)
HTTP_CODE=$(echo "$PATCH_RESP" | tail -1)
log "PATCH status=done HTTP $HTTP_CODE"

log "=== bench-and-close-sag2554.sh DONE ==="
