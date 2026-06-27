# Tick 13 — PENDING Completion Protocol
**For the next failover monitor tick when auth works**

## Found (tick 13 scan, before auth expired)

6 stalled issues (latest run = scheduled_retry with claude_transient_upstream):

| # | Issue ID |
|---|---|
| 1 | `f1f33445-118a-4d75-849d-91e4ecba5b82` |
| 2 | `93b8bbb4-fa02-4cf4-804e-0adcaf0711ae` |
| 3 | `54d2f2ad-bdd6-477b-8f5c-e2352f985aa7` |
| 4 | `cf6ff466-b707-40c1-bb34-19da300ddbfe` |
| 5 | `639df31e-4b0d-4a44-a913-5aac290d8184` |
| 6 | `a549bf91-7fec-41b0-a88b-df2b7aac644d` |

## What the next tick must do (resume from here)

### For each of the 6 stalled issues:

**STEP 1: Fetch assigneeAgentId**
```
GET /api/issues/{id}
```

**STEP 2: Gate A — Tier gate**
Check `assigneeAgentId` against:

**Allowed for canary failover (ONLY):** `3ab7fa06-f831-4631-922a-2fe824005788` → understudy `9a20c1b5-a039-4c18-8962-2825e3f28538`

**Allowed for all-pairing failover (NEEDS 2ND BOARD GO):**
- `11d0b5de` → `927d3e75`
- `fc67241d` → `dd1f4e1f`
- `de2ae83f` → `0acb5c9f`
- `dd89bb82` → `0acb5c9f`
- `5745f315` → `a93362ad`
- `1e0167fe` → `1bb6be46`
- `d1b7fc0d` → `fafc8d36`

**NEVER-failover (C-suite/Directors — skip + native retry):**
`b0f67cc2`, `f3c48afc`, `16373fdb`, `65337351`, `b214c191`, `c5494d47`, `7cc4dafd`, `ca2d28e4`, `24fb84ba`, `90b0b0e1`, `07702760`

**Outcome:**
- If assignee == `3ab7fa06` → proceed to Gates B-D (canary failover eligible)
- If in ALLOWED_CLOUD_IDS but NOT canary → post FAILOVER-ESCALATION + skip
- If NOT in ALLOWED_CLOUD_IDS or is C-suite → skip (native retry)

**STEP 3: Gate B — Idempotency**
```
GET /api/issues/{id}/comments?limit=50&order=desc
```
If body contains `FAILOVER-HANDOFF` without `FAILOVER-RETURN` → skip (already handoffed)

**STEP 4: Gate C — Sensitivity**
Check issue title, description, any comments for:
`NO-FAILOVER`, `[SENSITIVE]`, `[CREDENTIALS]`, `[SECURITY]`, `[PII]`, `[SECRET]`
If found → post FAILOVER-ESCALATION + skip

**STEP 5: Gate D — Understudy availability**
Check if understudy agent is healthy (not paused/error):
```
GET /api/companies/{companyId}/agents/{understudyAgentId}
```

### If all 4 gates pass:
1. POST FAILOVER-HANDOFF comment (template in AGENTS.md)
2. PATCH reasssign to understudy

## Tick 13 Summary (to be finalized next tick)
- Issues scanned: 99
- Stalled found: 6
- To be processed on next auth-healthy tick
