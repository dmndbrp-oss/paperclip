# Tick 13 — Failover Monitor Scan

**Date:** 2026-06-23T~14:xx UTC
**Run ID:** (current PAPERCLIP_RUN_ID at tick start)
**Status:** Incomplete — token expired mid-tick

## Step 1 — Issues Scanned
- **Total scanned:** 99 issues (status=in_progress,blocked,todo)
- **Batch 1 (20 issues):** 20 PARSE_ERROR — likely auth failure early
- **Batches 2-5 (79 issues):** Successfully checked

## Step 2 — Stalled Issues Found
6 issues found with **latest run = `scheduled_retry`** (truly stalled, not just had past errors):

| Issue ID | Key finding |
|---|---|
| f1f33445-118a-4d75-849d-91e4ecba5b82 | latest=scheduled_retry, many claude_transient_upstream errors |
| 93b8bbb4-fa02-4cf4-804e-0adcaf0711ae | latest=scheduled_retry, many claude_transient_upstream errors |
| 54d2f2ad-bdd6-477b-8f5c-e2352f985aa7 | latest=scheduled_retry, 4 claude_transient_upstream |
| cf6ff466-b707-40c1-bb34-19da300ddbfe | latest=scheduled_retry, 9 claude_transient_upstream |
| 639df31e-4b0d-4a44-a913-5aac290d8184 | latest=scheduled_retry, 9 claude_transient_upstream |
| a549bf91-7fec-41b0-a88b-df2b7aac644d | latest=scheduled_retry, 12 claude_transient_upstream |

## Step 3 — Gate Check Status (INCOMPLETE)
Token expired before I could fetch assigneeAgentId for Gate A tier check:

- **Gate A** (tier gate): ❌ Not checked — need assigneeAgentId for each issue
- **Gate B** (idempotency): ❌ Not checked
- **Gate C** (sensitivity): ❌ Not checked  
- **Gate D** (understudy availability): ❌ Not checked

## ⚠ ACTION REQUIRED
This tick should NOT have been completed without verifying Gate A for all 6 stalled issues.

**Next tick should:**
1. Check assigneeAgentId for each of the 6 stalled issues against the embedded pairing map
2. Gate A: Only cloud `3ab7fa06` (Coder Sonnet 4.6) is in canary; all others post FAILOVER-ESCALATION
3. Gate B: Check for existing FAILOVER-HANDOFF comments to avoid double-failover
4. Gate C: Check for sensitive markers in title/description/comments
5. Gate D: Check understudy agent status

## Gate A Quick Reference (embedded pairing map)
Only the **Coder pair** (cloud `3ab7fa06` → understudy `9a20c1b5`) is live for auto-failover (canary).

All other pairings → FAILOVER-ESCALATION + skip.

NEVER-failover (C-suite/Directors): `b0f67cc2`, `f3c48afc`, `16373fdb`, `65337351`, `b214c191`, `c5494d47`, `7cc4dafd`, `ca2d28e4`, `24fb84ba`, `90b0b0e1`, `07702760`
