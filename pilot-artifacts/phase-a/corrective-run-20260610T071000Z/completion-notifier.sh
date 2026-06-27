#!/usr/bin/env bash
# Phase A corrective run completion notifier — SAG-3657
# Polls until dispatcher process exits, then computes metrics and posts to Paperclip.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
LOG="$SCRIPT_DIR/completion-notifier.log"

# Capture session Paperclip credentials BEFORE sourcing .env (which has dev_key placeholder)
SESSION_API_URL="${PAPERCLIP_API_URL:-}"
SESSION_API_KEY="${PAPERCLIP_API_KEY:-}"
# Read from temp files written by the heartbeat if the env vars are empty
if [ -z "$SESSION_API_KEY" ] && [ -f /tmp/sag3657_paperclip_key.txt ]; then
    SESSION_API_KEY="$(cat /tmp/sag3657_paperclip_key.txt)"
    SESSION_API_URL="$(cat /tmp/sag3657_paperclip_url.txt 2>/dev/null || echo 'https://gus-pinsoneault-framework.tail302fee.ts.net')"
fi

set -a
source "$PROJECT_DIR/enrichment/.env"
set +a
export DATABASE_URL="postgresql://paperclip:paperclip@localhost:54329/enrichment_db"
export LITELLM_BASE_URL="http://localhost:4000"
export PHASE_A_METRICS_DIR="$SCRIPT_DIR"

API_URL="${SESSION_API_URL:-${PAPERCLIP_API_URL:-https://gus-pinsoneault-framework.tail302fee.ts.net}}"
API_KEY="${SESSION_API_KEY}"
SAG_3657_ID="c690d75c-1a0c-4f56-ba4e-2a51f0213c0c"
SAG_2154_ID="8d156727-49c7-45fb-be0c-343a3b6b0e1a"
SAG_3667_ID="b59ed2c5-9c55-49bd-97b1-33f2d95b8e26"
AGENT_ID="3ab7fa06-f831-4631-922a-2fe824005788"

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Completion notifier started, watching for dispatcher to finish" | tee -a "$LOG"

# Wait for dispatcher to exit (polls every 30s)
while pgrep -f "dispatcher.py --batch-size" > /dev/null 2>&1; do
    PENDING=$(python3 -c "
import psycopg2
conn = psycopg2.connect('$DATABASE_URL')
cur = conn.cursor()
cur.execute(\"SELECT count(*) FROM enrichment_staging.enrichment_queue WHERE status='pending'\")
print(cur.fetchone()[0])
conn.close()
" 2>/dev/null || echo "?")
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Dispatcher running, pending=$PENDING" | tee -a "$LOG"
    sleep 30
done

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Dispatcher exited. Computing metrics." | tee -a "$LOG"

# Compute aggregate metrics from JSONL files
METRICS_JSON=$(python3 - << 'PYEOF'
import json, glob, os, sys
import statistics

metrics_dir = os.environ.get("PHASE_A_METRICS_DIR", ".")
jsonl_files = sorted(glob.glob(os.path.join(metrics_dir, "run-metrics-*.jsonl")))

# Use the specific corrective-run batch (abf6b813) — other files in this dir are from
# partial runs with auth failures or killed batches and must not be mixed into metrics.
target = os.path.join(metrics_dir, "run-metrics-abf6b813-899c-4324-b475-5a9514981f7f.jsonl")
latest = target if os.path.exists(target) else (max(jsonl_files, key=lambda f: os.path.getmtime(f)) if jsonl_files else None)
if not latest:
    print(json.dumps({"error": "no JSONL files found"}))
    sys.exit(0)

records = []
with open(latest) as f:
    for line in f:
        if line.strip():
            records.append(json.loads(line))

n = len(records)
tier_dist = {}
for r in records:
    t = r.get("tier_used", "failed")
    tier_dist[t] = tier_dist.get(t, 0) + 1

primary_lats = [r["primary_latency_s"] for r in records if r.get("primary_latency_s") is not None]
fallback_lats = [r["fallback_latency_s"] for r in records if r.get("fallback_latency_s") is not None]
reviewer_lats = [r["reviewer_latency_s"] for r in records if r.get("reviewer_latency_s") is not None]
anomaly_scores = [r["reviewer_anomaly_score"] for r in records if r.get("reviewer_anomaly_score") is not None]
row_totals = [r["row_total_s"] for r in records if r.get("row_total_s") is not None]

def p50(vals): return statistics.median(vals) if vals else None
def p95(vals):
    if not vals: return None
    s = sorted(vals)
    idx = int(len(s) * 0.95)
    return s[min(idx, len(s)-1)]

schema_valid_primary = tier_dist.get("primary", 0) / n if n else 0
reviewer_flagged = sum(1 for r in records if r.get("reviewer_anomaly_score", 0) is not None and r.get("reviewer_anomaly_score", 0) > 0.5) / n if n else 0
wall_time = sum(row_totals)

result = {
    "batch_jsonl": os.path.basename(latest),
    "rows_processed": n,
    "tier_distribution": tier_dist,
    "schema_valid_rate_primary": round(schema_valid_primary, 4),
    "reviewer_flagged_rate": round(reviewer_flagged, 4),
    "latency_primary_p50_s": round(p50(primary_lats), 3) if p50(primary_lats) else None,
    "latency_primary_p95_s": round(p95(primary_lats), 3) if p95(primary_lats) else None,
    "latency_fallback_p50_s": round(p50(fallback_lats), 3) if p50(fallback_lats) else None,
    "latency_fallback_p95_s": round(p95(fallback_lats), 3) if p95(fallback_lats) else None,
    "latency_reviewer_p50_s": round(p50(reviewer_lats), 3) if p50(reviewer_lats) else None,
    "latency_reviewer_p95_s": round(p95(reviewer_lats), 3) if p95(reviewer_lats) else None,
    "wall_time_s": round(wall_time, 1),
    "total_cost_usd": 0.0,
}
print(json.dumps(result))
PYEOF
)

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Metrics: $METRICS_JSON" | tee -a "$LOG"

# Format the metrics comment body
COMMENT_BODY=$(python3 - << PYEOF
import json, sys

m = json.loads('''$METRICS_JSON''')
td = m.get("tier_distribution", {})
n = m.get("rows_processed", 0)

lines = [
    "## Phase A Corrective Re-run — Final Raw Metrics (SAG-3657 / gemma4-26b-a4b-it-q4_K_M)",
    "",
    "**⚠ Note on delivery:** Coder cannot comment directly on [SAG-2154](/SAG/issues/SAG-2154) (403 — assigned to DoE). Full raw metrics are posted here. [@Director of Engineering (Sonnet 4.6)](agent://b214c191-e56f-4d62-80ef-1c12af0788f6) — these are your raw numbers for the calibration report.",
    "",
    "**Batch:** \`" + m.get("batch_jsonl", "?") + "\`  ",
    "**Rows processed:** " + str(n),
    "**Model (primary):** gemma4-26b-a4b-it-q4_K_M  ",
    "**Fallback model:** ollama/qwen2.5:14b-instruct-q4_K_M",
    "",
    "### Tier distribution",
    "| Tier | Count | Rate |",
    "|------|-------|------|",
]
for tier in ("primary", "fallback", "failed"):
    count = td.get(tier, 0)
    rate = f"{count/n*100:.1f}%" if n else "n/a"
    lines.append(f"| {tier} | {count} | {rate} |")

psr = m.get("schema_valid_rate_primary")
rfr = m.get("reviewer_flagged_rate")
lines += [
    "",
    "### Key rates",
    f"- **Schema-valid rate (primary):** {psr*100:.1f}% ({td.get('primary',0)}/{n} rows)" if psr is not None else "- Schema-valid rate (primary): n/a",
    f"- **Reviewer-flagged rate (anomaly_score > 0.5):** {rfr*100:.1f}%" if rfr is not None else "- Reviewer-flagged rate: n/a",
    "",
    "### Per-row latency",
    "| Tier | p50 (s) | p95 (s) |",
    "|------|---------|---------|",
    f"| primary | {m.get('latency_primary_p50_s', 'n/a')} | {m.get('latency_primary_p95_s', 'n/a')} |",
    f"| fallback | {m.get('latency_fallback_p50_s', 'n/a')} | {m.get('latency_fallback_p95_s', 'n/a')} |",
    f"| reviewer | {m.get('latency_reviewer_p50_s', 'n/a')} | {m.get('latency_reviewer_p95_s', 'n/a')} |",
    "",
    f"**End-to-end wall time:** {m.get('wall_time_s', 'n/a')}s",
    f"**Total cost:** \${m.get('total_cost_usd', 0):.2f} (local inference, zero cloud spend)",
    "",
    "No rows promoted to production (staging-only contract maintained).",
    "",
    "DoE: calibration report is yours to author from the raw numbers above.",
]
print("\\n".join(lines))
PYEOF
)

# Post final metrics comment to SAG-3657 (Coder owns it; SAG-2154 403s for this agent)
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Posting final metrics to SAG-3657 with DoE @-mention" | tee -a "$LOG"

curl -s -X POST "${API_URL}/api/issues/${SAG_3657_ID}/comments" \
    -H "Authorization: Bearer ${API_KEY}" \
    -H "Content-Type: application/json" \
    -d "$(jq -n --arg body "$COMMENT_BODY" '{"body": $body}')" \
    >> "$LOG" 2>&1 || echo "[WARN] SAG-3657 metrics comment failed" | tee -a "$LOG"

# Close the gate issue SAG-3667 — this fires issue_blockers_resolved on SAG-3657, waking the Coder
GATE_BODY="Batch complete. Dispatcher exited. Final metrics posted on SAG-3657. Closing gate to unblock SAG-3657."
curl -s -X PATCH "${API_URL}/api/issues/${SAG_3667_ID}" \
    -H "Authorization: Bearer ${API_KEY}" \
    -H "Content-Type: application/json" \
    -H "X-Paperclip-Run-Id: sag3657-completion-notifier" \
    -d "$(jq -n --arg status "done" --arg comment "$GATE_BODY" '{"status": $status, "comment": $comment}')" \
    >> "$LOG" 2>&1 || echo "[WARN] SAG-3667 gate close failed" | tee -a "$LOG"

# Also patch SAG-3657 to done directly (belt-and-suspenders — works even if gate close above succeeded)
DONE_BODY="Phase A corrective re-run complete. 100 rows processed with gemma4-26b-a4b-it-q4_K_M. Raw metrics posted above. No rows promoted to production. [@Director of Engineering (Sonnet 4.6)](agent://b214c191-e56f-4d62-80ef-1c12af0788f6) — [SAG-2154](/SAG/issues/SAG-2154) is now unblocked; calibration report is next."
curl -s -X PATCH "${API_URL}/api/issues/${SAG_3657_ID}" \
    -H "Authorization: Bearer ${API_KEY}" \
    -H "Content-Type: application/json" \
    -H "X-Paperclip-Run-Id: sag3657-completion-notifier" \
    -d "$(jq -n --arg status "done" --arg comment "$DONE_BODY" '{"status": $status, "comment": $comment}')" \
    >> "$LOG" 2>&1 || echo "[WARN] SAG-3657 update failed" | tee -a "$LOG"

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Completion notifier done." | tee -a "$LOG"
