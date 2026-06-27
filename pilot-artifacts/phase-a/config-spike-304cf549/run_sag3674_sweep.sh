#!/usr/bin/env bash
# SAG-3674 corrected sweep runner + Paperclip results poster
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../../../" && pwd)"
ENRICH_DIR="$PROJECT_DIR/enrichment"
ARTIFACT_FILE="$SCRIPT_DIR/sag3674_corrected_results.json"
LOG="$SCRIPT_DIR/sag3674_sweep.log"

export DATABASE_URL="postgresql://paperclip:paperclip@localhost:54329/enrichment_db"
export LITELLM_BASE_URL="http://localhost:4000"
export LITELLM_API_KEY="sk-sage-local-only"
export OLLAMA_BASE_URL="http://localhost:11434"
export SPIKE_N=20

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] SAG-3674 corrected sweep START" | tee -a "$LOG"

# Run from project root so enrichment/ and pilot-artifacts/ imports resolve
# PYTHONUNBUFFERED=1 flushes stdout on every print so the log shows real-time progress
cd "$PROJECT_DIR"
PYTHONUNBUFFERED=1 python3 -u "$SCRIPT_DIR/sag3674_corrected_sweep.py" 2>&1 | tee -a "$LOG"
EXIT_CODE=${PIPESTATUS[0]}

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] sweep exit=$EXIT_CODE" | tee -a "$LOG"

if [ "$EXIT_CODE" -ne 0 ]; then
  echo "SWEEP FAILED exit=$EXIT_CODE" | tee -a "$LOG"
fi

# Post results back to Paperclip issue
if [ -z "${PAPERCLIP_API_URL:-}" ] || [ -z "${PAPERCLIP_API_KEY:-}" ] || [ -z "${PAPERCLIP_TASK_ID:-}" ]; then
  echo "Skipping Paperclip post — env vars not set" | tee -a "$LOG"
  exit $EXIT_CODE
fi

if [ ! -f "$ARTIFACT_FILE" ]; then
  echo "Artifact file missing — cannot post results" | tee -a "$LOG"
  exit 1
fi

# Build comment from artifact
python3 - <<PYEOF
import json, sys, os

with open("$ARTIFACT_FILE") as f:
    d = json.load(f)

meta = d["meta"]
probe = d["probe"]
A = d["A_baseline"]
B = d["B_full_combo"]
C = d["C_ablation"]

supp = probe.get("suppression_confirmed", False)
supp_str = "**CONFIRMED**" if supp else "**NOT CONFIRMED** — thinking still active"

def fmt_modes(m):
    return " ".join(f"{k}={v}" for k,v in m.items() if v)

lines = [
    "## SAG-3674 — corrected sweep results",
    "",
    "### Probe — think:false suppression at top level",
    f"- control (no think param): done_reason={probe['control']['done_reason']!r}  thinking_len={probe['control']['thinking_len']}  compl_tokens={probe['control']['compl_tokens']}",
    f"- think:false (top-level): done_reason={probe['think_false']['done_reason']!r}  thinking_len={probe['think_false']['thinking_len']}  compl_tokens={probe['think_false']['compl_tokens']}",
    f"- Suppression: {supp_str}",
    "",
    "### Per-config results at N=20",
    "",
    "| Config | Description | N | Primary schema-valid % |",
    "|--------|-------------|---|------------------------|",
    f"| A | Baseline (LiteLLM, max_tokens=2048) | {A['n']} | **{A['pct']:.0f}%** |",
    f"| B | json_object + max_tokens=4096 + temp=0 + think:false (top-level) | {B['n']} | **{B['pct']:.0f}%** |",
    f"| C | Ablation: same as B, no think:false | {C['n']} | **{C['pct']:.0f}%** |",
    "",
    "### Failure modes",
    f"- A: {fmt_modes(A['modes'])}",
    f"- B: {fmt_modes(B['modes'])}",
    f"- C: {fmt_modes(C['modes'])}",
    "",
    "### faff99c delta",
    "- Applied: availability→in_stock null repair active in parse path",
    f"- Without repair (L2 SAG-3673 baseline): ~0% (null availability → invalid)",
    f"- With repair (Config A): {A['pct']:.0f}% (residual failures are other issues)",
    "",
    f"### Verdict",
    f"Does config B move primary materially toward ≥85%? **{'YES' if B['pct'] >= 85 else 'NO'} ({B['pct']:.0f}%)**",
    "",
    f"### Raw artifacts",
    f"- Artifact: \`pilot-artifacts/phase-a/config-spike-304cf549/sag3674_corrected_results.json\`",
    f"- Log: \`pilot-artifacts/phase-a/config-spike-304cf549/sag3674_sweep.log\`",
    f"- N={meta['N']}, model={meta['model']}, ollama_model={meta['ollama_model']}",
    "",
    "[@CTO](agent://f3c48afc-c339-4e43-b47b-a42a0891229d) — results ready for your GO/NO-GO #3 synthesis on [SAG-3671](/SAG/issues/SAG-3671).",
]
print("\n".join(lines))
PYEOF
COMMENT_BODY=$(python3 - <<PYEOF
import json, sys

with open("$ARTIFACT_FILE") as f:
    d = json.load(f)

meta = d["meta"]
probe = d["probe"]
A = d["A_baseline"]
B = d["B_full_combo"]
C = d["C_ablation"]

supp = probe.get("suppression_confirmed", False)
supp_str = "**CONFIRMED**" if supp else "**NOT CONFIRMED** — thinking still active"

def fmt_modes(m):
    return " ".join(f"{k}={v}" for k,v in m.items() if v)

lines = [
    "## SAG-3674 — corrected sweep results",
    "",
    "### Probe — think:false suppression at top level",
    f"- control (no think param): done_reason={probe['control']['done_reason']!r}  thinking_len={probe['control']['thinking_len']}  compl_tokens={probe['control']['compl_tokens']}",
    f"- think:false (top-level): done_reason={probe['think_false']['done_reason']!r}  thinking_len={probe['think_false']['thinking_len']}  compl_tokens={probe['think_false']['compl_tokens']}",
    f"- Suppression: {supp_str}",
    "",
    "### Per-config results at N=20",
    "",
    "| Config | Description | N | Primary schema-valid % |",
    "|--------|-------------|---|------------------------|",
    f"| A | Baseline (LiteLLM, max_tokens=2048) | {A['n']} | **{A['pct']:.0f}%** |",
    f"| B | json_object + max_tokens=4096 + temp=0 + think:false (top-level) | {B['n']} | **{B['pct']:.0f}%** |",
    f"| C | Ablation: same as B, no think:false | {C['n']} | **{C['pct']:.0f}%** |",
    "",
    "### Failure modes",
    f"- A: {fmt_modes(A['modes'])}",
    f"- B: {fmt_modes(B['modes'])}",
    f"- C: {fmt_modes(C['modes'])}",
    "",
    "### faff99c delta",
    "- Applied: availability→in_stock null repair active in parse path",
    f"- Without repair (L2 SAG-3673 baseline): ~0% (null availability → invalid)",
    f"- With repair (Config A): {A['pct']:.0f}% (residual failures are other issues)",
    "",
    "### Verdict",
    f"Does config B move primary materially toward ≥85%? **{'YES' if B['pct'] >= 85 else 'NO'} ({B['pct']:.0f}%)**",
    "",
    "### Raw artifacts",
    f"- Artifact: \`pilot-artifacts/phase-a/config-spike-304cf549/sag3674_corrected_results.json\`",
    f"- Log: \`pilot-artifacts/phase-a/config-spike-304cf549/sag3674_sweep.log\`",
    f"- N={meta['N']}, model={meta['model']}, ollama_model={meta['ollama_model']}",
    "",
    "[@CTO](agent://f3c48afc-c339-4e43-b47b-a42a0891229d) — results ready for your GO/NO-GO #3 synthesis on [SAG-3671](/SAG/issues/SAG-3671).",
]
print("\n".join(lines))
PYEOF
)

JSON_BODY=$(jq -n --arg body "$COMMENT_BODY" '{"body": $body}')
HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
  -X POST \
  -H "Authorization: Bearer ${PAPERCLIP_API_KEY}" \
  -H "Content-Type: application/json" \
  -H "X-Paperclip-Run-Id: ${PAPERCLIP_RUN_ID:-run-unknown}" \
  -d "$JSON_BODY" \
  "${PAPERCLIP_API_URL}/api/issues/${PAPERCLIP_TASK_ID}/comments")

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Paperclip comment HTTP $HTTP_STATUS" | tee -a "$LOG"

# Update issue status to in_review (CTO is the reviewer)
PATCH_BODY=$(jq -n --arg status "in_review" --arg comment "Sweep complete. Results posted. Awaiting CTO GO/NO-GO #3 synthesis on SAG-3671." \
  '{"status": $status, "comment": $comment}')
curl -s -o /dev/null -w "" \
  -X PATCH \
  -H "Authorization: Bearer ${PAPERCLIP_API_KEY}" \
  -H "Content-Type: application/json" \
  -H "X-Paperclip-Run-Id: ${PAPERCLIP_RUN_ID:-run-unknown}" \
  -d "$PATCH_BODY" \
  "${PAPERCLIP_API_URL}/api/issues/${PAPERCLIP_TASK_ID}"

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Issue status updated to in_review" | tee -a "$LOG"
exit $EXIT_CODE
