# Failover Monitor — Allowlist / Pairing Map

## What is PAIRING-MAP.json?

`PAIRING-MAP.json` is the **CEO-authored, board-approved IC failover allowlist**. It encodes:

1. **`pairings[]`** — the set of cloud agents eligible for auto-failover and their mapped local understudies.
2. **`never_failover[]`** — an explicit deny-list of C-suite and Director agents whose work must never be auto-delegated.

Only agents present in `pairings[].cloud_agent_id` will ever be failed over. Any agent absent from that list — including all of `never_failover[]` — is silently skipped by the Monitor (tier gate, Step 2 Gate A in `INSTRUCTIONS.md`).

## How the Monitor reads it

On each tick (Step 0 of the algorithm):

```python
import json, os

# Repo root is the project working directory
with open("failover-monitor/PAIRING-MAP.json") as f:
    config = json.load(f)

pairings = config["pairings"]
ALLOWED_CLOUD_IDS = {p["cloud_agent_id"] for p in pairings}
UNDERSTUDY_BY_CLOUD_ID = {p["cloud_agent_id"]: p for p in pairings}
```

The Monitor then checks `issue.assigneeAgentId in ALLOWED_CLOUD_IDS` as the first gate. If not in the set, skip. If yes, look up `UNDERSTUDY_BY_CLOUD_ID[issue.assigneeAgentId]` to get the understudy ID, name, and notes.

The `notes` field in each pairing entry provides human-readable context about what the understudy can/cannot do — it is also surfaced in the FAILOVER-HANDOFF comment if the Monitor chooses to include it.

## Changing the pairing map

**Who can change it:** CEO + board approval. This is an org decision (per `SAG-4573-local-ai-failover-coverage.md`), not a technical one.

**How to change it:** Edit `PAIRING-MAP.json`, commit to the repo, and update `_meta.last_updated`. The Monitor reads the file fresh every tick — no restart needed.

**What triggers a required update:**
- New cloud IC agent hired → CEO adds a pairing entry
- Cloud IC agent retired → remove the entry
- Local understudy replaced → update `understudy_agent_id`
- Agent IDs change (rare) → update both sides

## Sentinel markers (comment-based, no schema change needed)

The Monitor uses comment body text as lightweight markers. These are plain strings that appear at the start of a comment body:

| Sentinel | Posted by | Meaning |
|---|---|---|
| `FAILOVER-HANDOFF` | Failover Monitor | Active handoff in progress; understudy is the owner |
| `FAILOVER-RETURN` | Understudy agent | Understudy finished; cloud agent should review and close |
| `FAILOVER-ESCALATION` | Failover Monitor | Sensitive issue blocked from failover; CEO notified |
| `NO-FAILOVER` | Issue author / any agent | Opt-out: this issue must never be auto-delegated |

Any agent can add a `NO-FAILOVER` comment to opt out. The Monitor's Gate C checks for it (and checks the title/description as well). Default-deny: if classification is uncertain, the Monitor escalates to CEO rather than failing over.

## Security notes

- The FAILOVER-HANDOFF comment template (in `INSTRUCTIONS.md`) contains **no secrets, credentials, or PII** — only agent IDs, names, and work-context summaries.
- The pairing map itself contains only agent UUIDs and names — no credentials.
- The Monitor never reads issue attachments, never writes to file systems, and never posts sensitive issue content into its handoff comments.
- The understudy inherits only the issue thread context — no new credential/path access is granted by reassignment.
