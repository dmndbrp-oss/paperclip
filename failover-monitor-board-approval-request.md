# Failover Monitor — Board Approval Request

**Issue:** SAG-4809
**Status:** PENDING BOARD APPROVAL
**Date:** 2026-06-23
**Hired by:** CTO (Opus 4.8)

---

## Executive Summary

This request builds **SAG-4613**: a local-AI failover mechanism for when Claude cloud agent sessions hit usage limits (e.g., "resets 9pm").

**The problem:** Agents stall for hours when cloud limits are hit. Board requirements from SAG-4573 Part 2/3:
1. Every department has ≥1 local AI agent (ALREADY MET)
2. Auto-assign stalled work to local understudy (NOT YET BUILT)
3. Cloud agent reviews work before acceptance when back online (NOT YET BUILT)

**The solution:** A lightweight **Failover Monitor** agent (local Qwen3.6, free) that scans every 5 minutes for stalled issues and auto-failovers to local understudies based on a CEO-approved pairing map.

---

## Board Approval Items

### 1. Approve the Failover Monitor agent
- **Type:** opencode_local (Qwen3.6) — no cloud token cost
- **Report to:** CTO (Opus 4.8)
- **Scope:** Read issues/runs within company, PATCH `assigneeAgentId`, POST comments
- **Safety:** Tier gate blocks C-suite/Director auto-failover; sensitivity gate blocks NO-FAILOVER flagged issues
- **See:** `failover-monitor-hire-request.json`

### 2. Approve the pairing map
- Maps cloud agents to local understudies (IC-tier work only)
- Derived from CEO's SAG-4573 departmental coverage audit
- Includes tier exceptions (C-suite/Director = never auto-failover)
- **See:** `failover-pairing-map.yaml`

### 3. Approve the 5-minute cron routine
- Schedule: `*/5 * * * *` (every 5 minutes)
- Policy: skip_if_active (no overlapping ticks)
- Wakes Failover Monitor agent to execute algorithm
- **See:** `failover-monitor-cron-routine-spec.md`

### 4. Confirm 5-minute interval
- Cloud limits last hours → 5-min latency is acceptable
- Tighter intervals (1-2 min) increase cost without meaningful benefit

### 5. Approve canary rollout
- Start with ONE pairing (e.g., Senior SWE ↔ Senior SWE Local)
- Verify end-to-end (handoff, understudy work, reassign-to-review, cloud review → done)
- Then add remaining pairings

---

## Why This is Safe

| Risk | Mitigation |
|---|---|
| Wrong-tier failover (C-suite decisions delegated) | Tier gate: only pairing-map agents (IC) are eligible |
| Sensitive work exposed to local agent | Sensitivity gate: NO-FAILOVER flagged issues are excluded |
| Infinite failover loop | Idempotency gate: FAILOVER-HANDOFF marker checked; skip_if_active prevents overlapping ticks |
| Wrong work assigned | Pairing map is the **allowlist**; only exact matches are processed |
| Monitor agent abuse | Minimal permissions (READ issues/runs, PATCH assignee, POST comments); no external reach; no secrets |
| Audit trail | Every failover posts structured FAILOVER-HANDOFF comment; every action visible in issue thread |
| Reversibility | Delete agent + routine → behavior reverts to native same-agent retry |

---

## Dependencies

- **SAG-4573 (Part 1):** Departmental local AI coverage — ALREADY MET ✓
- **SAG-4573 (Part 2/3):** Failover pairing map — CEO-authored, encoded in `failover-pairing-map.yaml`
- **SAG-4590:** Mechanism design (Option A) — reviewed and approved in concept
- **AGENT-AG-4578:** Cloud→Local failover readiness — blocked on this approval

---

## Approval Request Format

Please respond with:

```
APPROVED: [1] [2] [3] [4] [5]
NAMES: [comma-separated if any item rejected]
CONDITIONS: [if any conditions on approval]
```

Or:

```
CONDITIONAL_APPROVED: [items approved]
REQUIREMENTS: [items needed before approval]
```

Or:

```
NOT_APPROVED: [items]
REASON: [why]
```

---

## Artifacts

All files built this heartbeat are in SAG-4809 workspace:
- `failover-monitor-hire-request.json` — Agent hire config
- `failover-monitor-agents.md` — Agent standing instructions (algorithm)
- `failover-pairing-map.yaml` — Pairing map from SAG-4573
- `failover-monitor-cron-routine-spec.md` — 5-min cron routine spec

All will be deployed upon board approval refresh.
