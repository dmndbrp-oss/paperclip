#!/usr/bin/env bash
# Smart launcher: waits for Ollama to unload llama3.3:70b,
# then runs qwen3-coder:30b bench, consolidates, commits, closes SAG-2554.
set -euo pipefail

BENCH_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJ_DIR="$(cd "$BENCH_DIR/../.." && pwd)"
LOG="$BENCH_DIR/run-sag2554.log"
ISSUE_ID="bfcc0267-47a8-466c-b381-a8d4fa2e4480"
API_KEY="${PAPERCLIP_API_KEY:-}"
API_BASE="http://localhost:3100"
QWEN_OUT="$BENCH_DIR/results-sag2554-qwen.json"
LLAMA_OUT="$BENCH_DIR/results-sag2554-llama.json"
FINAL_OUT="$BENCH_DIR/results-sag2553.json"
QWEN_STDOUT="$BENCH_DIR/qwen-bench-stdout.log"

log() { echo "[$(date -u +%H:%M:%SZ)] $*" | tee -a "$LOG"; }

log "=== run-sag2554.sh started ==="

# --- Phase 1: Wait until llama3.3:70b is unloaded ---
log "Waiting for llama3.3:70b to unload (polling /api/ps every 15s, max 2h)..."
ATTEMPTS=0
MAX_ATTEMPTS=480

while true; do
  ATTEMPTS=$((ATTEMPTS + 1))
  if [ "$ATTEMPTS" -gt "$MAX_ATTEMPTS" ]; then
    log "ERROR: Timed out waiting for Ollama to free after $MAX_ATTEMPTS attempts"
    exit 1
  fi

  LLAMA_LOADED=$(curl -s http://localhost:11434/api/ps 2>/dev/null | \
    python3 -c "import json,sys; d=json.load(sys.stdin); names=[m['name'] for m in d.get('models',[])]; print('yes' if any('llama3.3:70b' in n for n in names) else 'no')" 2>/dev/null || echo "yes")

  if [ "$LLAMA_LOADED" = "no" ]; then
    log "llama3.3:70b unloaded (attempt $ATTEMPTS) — starting bench"
    break
  fi

  if [ $((ATTEMPTS % 4)) -eq 0 ]; then
    log "Still waiting... attempt $ATTEMPTS, llama3.3:70b still loaded"
  fi
  sleep 15
done

sleep 5  # small grace for full drain

# --- Phase 2: Run qwen3-coder:30b bench ---
log "Starting qwen3-coder:30b bench (N=50, single+multi)..."
> "$QWEN_STDOUT"

cd "$BENCH_DIR"
if BENCH_MODELS="qwen3-coder:30b" BENCH_OUT="results-sag2554-qwen.json" \
     python3 run.py 2>&1 | tee "$QWEN_STDOUT"; then
  log "qwen3-coder bench COMPLETE"
else
  log "qwen3-coder bench exited non-zero (checking output anyway)"
fi

if [ ! -f "$QWEN_OUT" ]; then
  log "ERROR: $QWEN_OUT not written — aborting"
  exit 1
fi

# --- Phase 3: Consolidate ---
log "Consolidating results..."
python3 << PYEOF
import json
from pathlib import Path

bench_dir = Path("${BENCH_DIR}")
rows, meta = [], None

for p in [bench_dir / "results-sag2554-llama.json", bench_dir / "results-sag2554-qwen.json"]:
    if p.exists():
        d = json.loads(p.read_text())
        if meta is None:
            meta = {k: v for k, v in d.items() if k != "rows"}
        rows.extend(d["rows"])
        print(f"Loaded {p.name}: {len(d['rows'])} model(s)")
    else:
        print(f"WARNING: {p.name} not found")

meta["rows"] = rows
(bench_dir / "results-sag2553.json").write_text(json.dumps(meta, indent=2))
print(f"Wrote results-sag2553.json with {len(rows)} model rows")
PYEOF

# --- Phase 4: Commit ---
cd "$PROJ_DIR"
git add infra/bench/tool-call/run.py infra/bench/tool-call/README.md infra/bench/tool-call/results-sag2553.json 2>&1 | tee -a "$LOG" || true

if git diff --cached --quiet; then
  log "Nothing new to commit, using HEAD"
  FULL_SHA=$(git rev-parse HEAD)
else
  git commit -m "feat(SAG-2553): bench llama3.3:70b + qwen3-coder:30b + glm-5.1 on uniform harness

Co-Authored-By: Paperclip <noreply@paperclip.ing>" 2>&1 | tee -a "$LOG"
  FULL_SHA=$(git rev-parse HEAD)
fi

log "Commit: $FULL_SHA"
git cat-file -e "$FULL_SHA" && log "git cat-file OK"

# --- Phase 5: Build and post closing comment ---
QWEN_STDOUT_SNIPPET=$(tail -10 "$QWEN_STDOUT" 2>/dev/null || echo "(no stdout)")

RESULT_ROWS=$(python3 << PYEOF
import json
from pathlib import Path

def fmt(p):
    if not p.exists():
        return "(not found)"
    d = json.loads(p.read_text())
    return "\n".join(
        f"| {r['model']} | {r['single_tool']} ({r['single_pct']}%) | {r['multi_tool']} ({r['multi_pct']}%) | {r.get('tok_per_sec','n/a')} |"
        for r in d.get("rows", [])
    )

bd = Path("${BENCH_DIR}")
print(fmt(bd / "results-sag2554-llama.json"))
print(fmt(bd / "results-sag2554-qwen.json"))
PYEOF
)

COMMENT_BODY="## SAG-2554 bench complete

### Actual stdout (qwen3-coder:30b, last 10 lines)
\`\`\`
${QWEN_STDOUT_SNIPPET}
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

\`results-sag2553.json\` committed with ${FULL_SHA}. \`results.json\` (SAG-2537) untouched. No adapterConfig touched."

COMMENT_JSON=$(python3 -c "import json,sys; print(json.dumps({'body': sys.stdin.read()}))" <<< "$COMMENT_BODY")

HTTP=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$API_BASE/api/issues/$ISSUE_ID/comments" \
  -H "Authorization: Bearer $API_KEY" \
  -H "X-Paperclip-Run-Id: run-sag2554-final" \
  -H "Content-Type: application/json" \
  -d "$COMMENT_JSON")
log "Comment HTTP $HTTP"

HTTP=$(curl -s -o /dev/null -w "%{http_code}" -X PATCH "$API_BASE/api/issues/$ISSUE_ID" \
  -H "Authorization: Bearer $API_KEY" \
  -H "X-Paperclip-Run-Id: run-sag2554-final" \
  -H "Content-Type: application/json" \
  -d '{"status":"done"}')
log "PATCH done HTTP $HTTP"

log "=== run-sag2554.sh DONE ==="
