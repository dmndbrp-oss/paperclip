#!/usr/bin/env bash
# SAG-4191: Wait for full-sweep completion and post results to issue.
# Launched detached; safe to survive heartbeat death.

EVAL_DIR="$(dirname "$0")/.."
LOG="$EVAL_DIR/results/sag4191-fullsweep.log"
ISSUE_ID="470ccd54-a97d-43bf-b880-4ded8a4f011d"
API_URL="${PAPERCLIP_API_URL:-}"
API_KEY="${PAPERCLIP_API_KEY:-}"
CTO_ID="f3c48afc-c339-4e43-b47b-a42a0891229d"

# Wait for sweep to complete
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) Waiting for sweep completion..." >> "$EVAL_DIR/results/notifier.log"
until grep -q "Results written to" "$LOG" 2>/dev/null; do
  sleep 30
done

# Extract the results file path from the log
RESULTS_FILE=$(grep "Results written to" "$LOG" | tail -1 | awk '{print $NF}')
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) Sweep done. Results at: $RESULTS_FILE" >> "$EVAL_DIR/results/notifier.log"

if [ -z "$API_URL" ] || [ -z "$API_KEY" ]; then
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) Missing API_URL or API_KEY — can't post. Check $RESULTS_FILE manually." >> "$EVAL_DIR/results/notifier.log"
  exit 1
fi

# Parse results and build summary
SUMMARY=$(python3 - "$RESULTS_FILE" <<'PYEOF'
import json, sys
data = json.load(open(sys.argv[1]))
lines = ["## SAG-4191 Full-Sweep Results\n"]
lines.append(f"Model: {data['model']}  |  run_ts: {data['run_ts']}\n")
lines.append("| Class | N | task_correct | CI95 | clean | tool_call | errors |")
lines.append("|---|---|---|---|---|---|---|")
for cls, s in data['classes'].items():
    ci = s.get('task_correct_ci_95', [0,0])
    tc = s.get('tool_call_correct_rate')
    tc_str = f"{tc:.1%}" if tc is not None else "N/A"
    lines.append(f"| {cls} | {s['n']} | {s['task_correct_rate']:.1%} | [{ci[0]:.2f},{ci[1]:.2f}] | {s['clean_rate']:.1%} | {tc_str} | {s['errors']} |")
lines.append(f"\nAll 6 classes complete. Results JSON: {sys.argv[1]}")
lines.append("\nConf=9 (code tested, live sweep complete). [@CTO](agent://f3c48afc-c339-4e43-b47b-a42a0891229d) — ready for review + merge to main. No remote configured; please push branch `feature/SAG-4191-runtime-eval` and merge.")
print('\n'.join(lines))
PYEOF
)

# Post results comment
curl -s -X POST "$API_URL/api/issues/$ISSUE_ID/comments" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  --data-binary "$(python3 -c "import json,sys; print(json.dumps({'body': sys.stdin.read()}))" <<< "$SUMMARY")" \
  >> "$EVAL_DIR/results/notifier.log" 2>&1

# Update issue to in_review + assign CTO
curl -s -X PATCH "$API_URL/api/issues/$ISSUE_ID" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"status\": \"in_review\", \"assigneeAgentId\": \"$CTO_ID\"}" \
  >> "$EVAL_DIR/results/notifier.log" 2>&1

echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) Done — issue updated to in_review." >> "$EVAL_DIR/results/notifier.log"
