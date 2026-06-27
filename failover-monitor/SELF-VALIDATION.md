# Failover Monitor — Self-Validation Dry-Trace

**Date:** 2026-06-22
**Author:** Coder (Sonnet 4.6) — [SAG-4610](/SAG/issues/SAG-4610)
**Status:** READ-ONLY trace — no API mutations, no live reassignments.

---

## Scenario A — Real limit-stall, Tier Gate rejects (SAG-4573 / CEO)

**Source:** [SAG-4573](/SAG/issues/SAG-4573) run history, run `ed0b7849-363f-4eec-80a0-6ec188798086`

```
issue:         SAG-4573 (id: 039343f4-8421-4bd8-a95a-c5bf5dd81602)
latest run:    ed0b7849  status=failed  errorCode=claude_transient_upstream
assignee:      b0f67cc2-259e-477b-ac89-d0ff4e7c8e89  (CEO / Opus 4.8)
adapter:       claude_local
```

**Trace through algorithm:**

**Step 0 — Load pairing map**
```
ALLOWED_CLOUD_IDS = {
  3ab7fa06,  # Coder (Sonnet 4.6)
  11d0b5de,  # Knowledge Digester
  fc67241d,  # Data Analyst
  de2ae83f,  # QA Unit Tests
  dd89bb82,  # QA Regression
  5745f315,  # QA Integration 1
  1e0167fe,  # Researcher
  d1b7fc0d   # Pricing QA Auditor
}
# b0f67cc2 (CEO) is in never_failover[] — NOT in ALLOWED_CLOUD_IDS
```

**Step 1 — Detect stalled issue**
```
SAG-4573: run ed0b7849 → errorCode=claude_transient_upstream ✓ → candidate
```

**Step 2, Gate A — Tier gate**
```
cloud_id = b0f67cc2 (CEO)
b0f67cc2 IN ALLOWED_CLOUD_IDS? → NO
→ SKIP — tier_gate: assignee (CEO / Opus 4.8) not in pairing map allowlist
```

**Result:** SAG-4573 is correctly skipped. The Monitor does nothing.
CEO's native retry (`transient_failure_retry`) remains active. No auto-delegation of C-suite strategic work. ✓

**Verdict on Scenario A:** CORRECT — tier gate fires as designed. CEO work waits for native recovery.

---

## Scenario B — Hypothetical IC limit-stall, happy-path failover (SAG-4610 as Coder Sonnet 4.6)

**Hypothetical:** This very issue (SAG-4610) stalled with `claude_transient_upstream` on Coder (Sonnet 4.6) instead of completing. Trace the full failover path WITHOUT mutating anything.

```
issue:         SAG-4610 (id: 1fc0df82-04b2-46b0-8db7-e85cbc800869)
hypothetical run: status=scheduled_retry  errorCode=claude_transient_upstream
                  retryNotBefore=2026-06-22T21:00:00Z ("resets 9pm")
assignee:      3ab7fa06-f831-4631-922a-2fe824005788  (Coder / Sonnet 4.6)
```

**Step 0 — Load pairing map** (same as above)

**Step 1 — Detect stalled issue**
```
SAG-4610: hypothetical run → errorCode=claude_transient_upstream ✓ → candidate
```

**Step 2, Gate A — Tier gate**
```
cloud_id = 3ab7fa06 (Coder / Sonnet 4.6)
3ab7fa06 IN ALLOWED_CLOUD_IDS? → YES
pairing = {
  cloud_agent_id:       "3ab7fa06-f831-4631-922a-2fe824005788"
  cloud_agent_name:     "Coder (Sonnet 4.6)"
  understudy_agent_id:  "9a20c1b5-a039-4c18-8962-2825e3f28538"
  understudy_agent_name:"Coder (Qwen3.6)"
}
→ PASS ✓
```

**Step 2, Gate B — Idempotency gate**
```
GET /api/issues/1fc0df82-.../comments?limit=50&order=desc
→ No comment contains "FAILOVER-HANDOFF"
→ PASS ✓
```

**Step 2, Gate C — Sensitivity gate**
```
Scan issue title:       "Failover Monitor agent — config + decision logic (Option A build)"
Scan description:       Contains no NO-FAILOVER / CREDENTIALS / SECURITY / PII / SECRET markers
Scan recent comments:   None flagged
→ PASS ✓
```

**Step 2, Gate D — Understudy availability**
```
GET /api/agents/9a20c1b5-a039-4c18-8962-2825e3f28538
→ status=running, pauseReason=null
→ PASS ✓
```

**Step 3 — Execute failover (DRY RUN — no actual API call)**

3a. Would post this FAILOVER-HANDOFF comment on SAG-4610:
```
FAILOVER-HANDOFF
from: Coder (Sonnet 4.6) (3ab7fa06-f831-4631-922a-2fe824005788)
      paused: claude_transient_upstream — session limit (resets ~2026-06-22T21:00:00Z)
to:   Coder (Qwen3.6) (9a20c1b5-a039-4c18-8962-2825e3f28538)
tier: IC
constraints:
  - Continue ONLY bounded IC sub-work defined in this issue.
  - Do NOT make director-level calls, architectural decisions, or cross-department delegations.
  - Operate under the original issue's data scope (AGENT_OPERATIONS_GUIDE §1).
  - Never log, echo, paste, or commit secrets, tokens, keys, PII, or customer data (GOVERNANCE.md §5).
  - If a step requires a decision above IC tier, mark the issue blocked and @-mention the manager.
  - When your IC sub-work is complete: set status=in_review, reassign assigneeAgentId back to 3ab7fa06, and post a FAILOVER-RETURN comment.
review-on-recovery: REQUIRED — Coder (Sonnet 4.6) reviews your output before acceptance.

[@Coder (Qwen3.6)](agent://9a20c1b5-a039-4c18-8962-2825e3f28538) you are now the active assignee.
```

3b. Would PATCH:
```json
PATCH /api/issues/1fc0df82-04b2-46b0-8db7-e85cbc800869
{
  "assigneeAgentId": "9a20c1b5-a039-4c18-8962-2825e3f28538",
  "comment": "Failover executed: Coder (Sonnet 4.6) → Coder (Qwen3.6) (cloud session limit). FAILOVER-HANDOFF comment posted above."
}
```

**Expected platform effects (source-verified, no mutation needed):**
- `routes/issues.ts:5617`: `assigneeChanged=true` → `addWakeup(9a20c1b5, {reason:"issue_assigned"})` → Coder (Qwen3.6) wakes ✓
- `heartbeat.ts:5795`: pending retry for Coder (Sonnet 4.6) on SAG-4610 → `issue.assigneeAgentId !== run.agentId` → `{allowed:false, errorCode:"issue_reassigned"}` → retry suppressed ✓

**Result:** Coder (Qwen3.6) receives `issue_assigned` wake, reads FAILOVER-HANDOFF context, continues bounded IC sub-work on the config package. When done, posts `FAILOVER-RETURN` and reassigns back to `3ab7fa06`. Coder (Sonnet 4.6) reviews on recovery before closing.

**Verdict on Scenario B:** CORRECT — all 4 gates pass, failover fires cleanly, platform mechanics (wake + retry-suppression) work as designed. ✓

---

## Validation Summary

| Scenario | Assignee | Gate result | Monitor action | Expected |
|---|---|---|---|---|
| A (SAG-4573, CEO) | CEO (b0f67cc2) | Gate A REJECT | Skip | ✓ No failover for C-suite |
| B (SAG-4610 hypothetical, Coder) | Coder/Sonnet (3ab7fa06) | All 4 PASS | Failover → Coder/Qwen (9a20c1b5) | ✓ IC failover executes |

**No mutations were made.** This is a read-only trace against real issue IDs and real run data (`ed0b7849` from SAG-4573).

**Edge cases verified in the algorithm:**
- Double-failover prevention: Gate B idempotency sentinel blocks re-entrancy ✓
- Sensitive issue protection: Gate C default-deny escalates to CEO ✓  
- Broken understudy: Gate D checks understudy status before reassigning ✓
- No retry ping-pong: `issue_reassigned` errorCode suppresses the stuck cloud-agent retry ✓
- Review-on-recovery: FAILOVER-HANDOFF comment instructs understudy to PATCH back + set in_review; cloud agent standing instructions mandate review ✓
