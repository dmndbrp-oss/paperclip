#!/usr/bin/env bash
# SAG-2509: Direct 5-run reliability measurement for digester task-delegation path.
#
# Design:
#   - Creates a digest task for the summarizer worker (11d0b5de) via Paperclip API
#   - Polls until done (or timeout)
#   - Validates the KB output file written by the worker with knowledge-cli validate
#   - Records per-run: pass/fail, latency, task id
#
# Why not use digester-smoke.ts:
#   The smoke test harvests a raw YAML comment (task_id: in comment body) from the
#   child issue. But the worker (Knowledge Digester, claude_local) runs the full
#   digestion pipeline itself and writes YAML directly to the production KB, posting
#   a human-readable summary comment instead. The comment harvesting fails with
#   "no YAML comment found". The real KB output IS valid. This script measures the
#   actual end-to-end path.
#
# Usage:
#   PAPERCLIP_API_KEY=<key> PAPERCLIP_API_URL=http://localhost:3100 \
#     bash scripts/digester-delegation-measure.sh

set -euo pipefail

API_URL="${PAPERCLIP_API_URL:-http://localhost:3100}"
API_KEY="${PAPERCLIP_API_KEY:?PAPERCLIP_API_KEY is required}"
COMPANY_ID="${PAPERCLIP_COMPANY_ID:-1dc911ed-ff05-4072-b2ae-a3e3177e3873}"
SUMMARIZER_AGENT_ID="11d0b5de-44a9-4f05-b130-846804e29955"
RUNNER_AGENT_ID="${PAPERCLIP_AGENT_ID:-b214c191-e56f-4d62-80ef-1c12af0788f6}"
KB_DIR="/home/gus-pinsoneault/.paperclip/instances/default/companies/${COMPANY_ID}/knowledge"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

POLL_INTERVAL=10  # seconds
TIMEOUT=300       # seconds (5 min max per task)
RUNS=5

# Use a single well-known historical issue for all 5 runs.
# SAG-2152: a completed SSI Director issue with known good output.
ISSUE_ID="9d26f9c1-de67-48c1-9da0-364806e19129"
ISSUE_IDENTIFIER="SAG-2152"
EXPECTED_FILE="${KB_DIR}/tasks/2026/05/${ISSUE_IDENTIFIER}.yaml"

# ─── Confirm ANTHROPIC_API_KEY is unset ──────────────────────────────────────
if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
  echo "ERROR: ANTHROPIC_API_KEY is set — AC requires it to be unset"
  exit 1
fi
echo "✓ ANTHROPIC_API_KEY unset"

# ─── Confirm runner ≠ summarizer worker ──────────────────────────────────────
if [ "$RUNNER_AGENT_ID" = "$SUMMARIZER_AGENT_ID" ]; then
  echo "ERROR: runner ($RUNNER_AGENT_ID) = summarizer worker — deadlock risk"
  exit 1
fi
echo "✓ Runner ($RUNNER_AGENT_ID) ≠ summarizer worker ($SUMMARIZER_AGENT_ID)"
echo ""

# ─── Helpers ─────────────────────────────────────────────────────────────────
create_task() {
  curl -s -X POST \
    -H "Authorization: Bearer ${API_KEY}" \
    -H "Content-Type: application/json" \
    "${API_URL}/api/companies/${COMPANY_ID}/issues" \
    -d "{
      \"title\": \"digest:${ISSUE_IDENTIFIER} [measure-run-${1}]\",
      \"description\": \"SAG-2509 measurement run ${1}/5. Digest ${ISSUE_IDENTIFIER} (${ISSUE_ID}) and write a validated knowledge entry to the KB. Post a comment with the written file path when done.\",
      \"assigneeAgentId\": \"${SUMMARIZER_AGENT_ID}\",
      \"status\": \"todo\",
      \"priority\": \"medium\"
    }" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('id','ERROR'))"
}

poll_task() {
  local task_id="$1"
  curl -s \
    -H "Authorization: Bearer ${API_KEY}" \
    "${API_URL}/api/issues/${task_id}" \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('status','unknown'))"
}

validate_kb_file() {
  if [ ! -f "$EXPECTED_FILE" ]; then
    echo "MISSING"
    return
  fi
  local result
  result=$(cat "$EXPECTED_FILE" | (cd "$PROJECT_DIR" && npx tsx scripts/knowledge-cli.ts validate 2>&1)) || true
  echo "$result"
}

# ─── Main 5-run loop ─────────────────────────────────────────────────────────
PASS=0
FAIL=0
declare -a RESULTS

echo "═══ SAG-2509: 5-run digester task-delegation measurement ═══"
echo "Issue: ${ISSUE_IDENTIFIER} (${ISSUE_ID})"
echo "Summarizer worker: ${SUMMARIZER_AGENT_ID}"
echo "KB output: ${EXPECTED_FILE}"
echo ""

for RUN in $(seq 1 $RUNS); do
  echo "── RUN ${RUN}/${RUNS} ──"
  RUN_START=$(date +%s)
  RUN_START_ISO=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  echo "  Start: $RUN_START_ISO"

  # Create the digest task
  TASK_ID=$(create_task "$RUN")
  if [[ "$TASK_ID" == "ERROR" || -z "$TASK_ID" ]]; then
    echo "  ✗ FAIL: could not create task"
    FAIL=$((FAIL+1))
    RESULTS+=("RUN${RUN}: FAIL (task_create_error)")
    continue
  fi
  echo "  Task: ${TASK_ID}"

  # Poll until done or timeout
  DEADLINE=$((RUN_START + TIMEOUT))
  STATUS="todo"
  while [ "$(date +%s)" -lt "$DEADLINE" ]; do
    sleep "$POLL_INTERVAL"
    STATUS=$(poll_task "$TASK_ID")
    echo "  Status: $STATUS ($(( $(date +%s) - RUN_START ))s elapsed)"
    if [[ "$STATUS" == "done" ]]; then
      break
    elif [[ "$STATUS" == "cancelled" || "$STATUS" == "blocked" || "$STATUS" == "failed" || "$STATUS" == "archived" ]]; then
      echo "  ✗ FAIL: terminal state $STATUS"
      break
    fi
  done

  RUN_END=$(date +%s)
  LATENCY=$((RUN_END - RUN_START))

  if [[ "$STATUS" != "done" ]]; then
    echo "  ✗ FAIL: ended with status=$STATUS, latency=${LATENCY}s"
    FAIL=$((FAIL+1))
    RESULTS+=("RUN${RUN}: FAIL (status=$STATUS, latency=${LATENCY}s)")
    continue
  fi

  # Validate the KB output file
  VALIDATION=$(validate_kb_file)
  if [[ "$VALIDATION" == "valid" ]]; then
    echo "  ✓ PASS: task done, KB file validated (latency=${LATENCY}s)"
    PASS=$((PASS+1))
    RESULTS+=("RUN${RUN}: PASS (latency=${LATENCY}s, task=${TASK_ID})")
  else
    echo "  ✗ FAIL: task done but validation=$VALIDATION (latency=${LATENCY}s)"
    FAIL=$((FAIL+1))
    RESULTS+=("RUN${RUN}: FAIL (validation='${VALIDATION}', latency=${LATENCY}s, task=${TASK_ID})")
  fi

  echo ""
done

# ─── Final verdict ────────────────────────────────────────────────────────────
echo "═══ MEASUREMENT VERDICT ═══"
echo "Runs: ${PASS}/${RUNS} PASS | ${FAIL}/${RUNS} FAIL"
echo ""
for r in "${RESULTS[@]}"; do
  echo "  ${r}"
done
echo ""

if [ "$PASS" -ge 4 ]; then
  echo "VERDICT: PASS (${PASS}/5 ≥ 4/5 threshold — task-delegation path is reliable)"
  exit 0
else
  echo "VERDICT: FAIL (${PASS}/5 < 4/5 threshold — reliability insufficient)"
  exit 1
fi
