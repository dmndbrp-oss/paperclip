# SAG-4590 — Cloud→Local Failover + Review-on-Recovery: Mechanism Design

**Author:** CTO (Opus 4.8) · **Date:** 2026-06-22 · **Status:** DESIGN-FIRST — gated on CEO review → board approval. No build yet.
**Parent:** SAG-4573 (board directive). Pairing map authored by CEO in `/SAG-4573-local-ai-failover-coverage.md`.
**Confidence:** 8/10.

---

## 0. TL;DR / Verdict

- **Native support? NO.** Paperclip does not auto-reassign on a failure subtype. On `claude_transient_upstream` (session/usage limit) it re-queues the **same** cloud agent with bounded backoff and waits for reset. That is exactly the gap the board flagged.
- **But two existing primitives make a clean failover possible with ZERO core code change:**
  1. Changing an issue's `assigneeAgentId` **immediately wakes the new assignee** (`server/src/routes/issues.ts:5617-5641`, reason `issue_assigned`).
  2. If issue ownership changes while a same-agent retry is pending, that pending retry is **auto-suppressed** (`server/src/services/heartbeat.ts:5795-5807`, errorCode `issue_reassigned`). No double-execution.
- **Recommended mechanism (Option A): a cron Routine that wakes a lightweight "Failover Monitor" agent every ~5 min** to scan for limit-stalled IC issues, apply the tier rule, reassign to the mapped local understudy with a structured handoff comment, and route back to the cloud agent for mandatory review on recovery. All actions are ordinary API calls the monitor agent already has rights to make.
- Option B (a small server-side observer in the recovery service) is more robust/lower-latency but is a change to **shared control-plane core** → heavier approval, more risk. Documented as the fallback if polling proves inadequate.
- **Gate:** present to board before building either option. Even Option A's monitor agent + routine is a new standing capability and should be board-approved per GOVERNANCE.md / AGENT_OPERATIONS_GUIDE §1.

---

## 1. Investigation — native behavior (source-verified)

All paths under `/home/gus-pinsoneault/paperclip`.

### 1.1 How a limit failure is classified
- `packages/adapters/claude-local/src/server/execute.ts:835-846` — the claude-local adapter emits errorCode `claude_transient_upstream` on upstream session/rate-limit, with a `retryNotBefore` timestamp hint (the "resets 9pm").
- `server/src/services/heartbeat.ts:313-317,339-347` — maps `claude_transient_upstream`/`codex_transient_upstream` to errorFamily `transient_upstream`.

### 1.2 What happens today = same-agent retry, no failover
- `server/src/services/recovery/service.ts:179-184` — `TRANSIENT_INFRA_CONTINUATION_ERROR_CODES` includes `claude_transient_upstream`.
- `service.ts:195-225` — transient infra failures get up to **3** retries, **60s** base backoff. A new run is created with status `scheduled_retry`, `retryOfRunId` → original, and **the same `agentId` is retained** (`heartbeat.ts:~6400`). Wake reason `transient_failure_retry` (`heartbeat.ts:253`).
- **No fallback/understudy/failover path exists.** Confirmed absent: no `failover`, no `reassignmentPolicy`, no run-failure event hook.

### 1.3 The two primitives we exploit
- **Assignment auto-wakes the new owner** — `server/src/routes/issues.ts:5617-5641`:
  ```ts
  } else if (assigneeChanged && issue.assigneeAgentId && issue.status !== "backlog") {
    addWakeup(issue.assigneeAgentId, { source:"assignment", reason:"issue_assigned",
      payload:{ issueId: issue.id, mutation:"update" }, ... });
  }
  ```
  (Same wakeup used by routine dispatch: `services/issue-assignment-wakeup.ts:21-48`.)
- **Ownership change cancels the stuck retry** — `server/src/services/heartbeat.ts:5795-5807`:
  ```ts
  if (issue.assigneeAgentId !== run.agentId) {
    return { allowed:false, reason:"Scheduled retry suppressed because issue ownership changed",
      errorCode:"issue_reassigned", ... };
  }
  ```

### 1.4 Routines = prompt delivery, not server logic
- `packages/shared/src/types/routine.ts:51-77` + `server/src/services/routines.ts:1158-1378`. A routine fires on cron/webhook/manual, interpolates `title`/`description` template variables, **creates an issue assigned to a target agent, and wakes that agent**. It has **no `script`/`handler`/conditional logic** and cannot itself query run state.
- Implication: the *scheduling* is native; the *decision logic* must live in an **agent** that the routine wakes. That agent reads run/issue state via the API and performs the reassignment. This is the basis of Option A.

### 1.5 Cloud vs local distinction exists
- `packages/shared/src/constants.ts:30-43` adapter types; `recovery/service.ts:75-83` `SESSIONED_LOCAL_ADAPTERS`. `claude_local` = cloud Anthropic models (limit-prone). `opencode_local`/`ollama` (Qwen3.6) = on-box, no cloud cap. The monitor keys off `adapterType` + model to decide who is "limit-prone cloud" vs "local understudy."

---

## 2. Design — Option A (RECOMMENDED): Routine + Failover Monitor agent (no core code)

### 2.1 Components
1. **Failover Monitor agent** — a single lightweight agent (local Qwen3.6 to keep it free + always-available; it does only API calls + rule evaluation, no heavy reasoning). Scoped read of issues/runs + reassign rights within the company.
2. **Cron Routine** — fires every ~5 min, `concurrencyPolicy: skip_if_active`, wakes the Monitor with a fixed instruction prompt (the algorithm below).
3. **Pairing map** — the CEO table in `/SAG-4573-local-ai-failover-coverage.md`, encoded as a config block the Monitor reads (cloud agentId → understudy agentId). The map is the **allowlist**: only agents present in it are eligible for auto-failover.

### 2.2 Failover algorithm (Monitor, each tick)
For each in-flight issue whose **latest run** failed with errorCode `claude_transient_upstream` (or family `transient_upstream`):
1. **Tier gate.** If the current assignee is C-suite or a Director (CEO/CTO/CFO/directors), **STOP — do not failover.** Leave the native retry to wait; if it exhausts, escalate (comment + @-mention manager). Only assignees present in the pairing map proceed.
2. **Idempotency gate.** If the issue already carries an active `FAILOVER-HANDOFF` marker (see §2.4), skip — already handed off; avoids loops.
3. **Sensitivity gate.** If the issue carries a `NO-FAILOVER` marker (credential/security/PII-sensitive, set by author), skip and escalate instead. (Default-deny for flagged-sensitive work — §4.)
4. **Reassign.** PATCH `assigneeAgentId` → mapped understudy. This (a) auto-wakes the understudy (`issue_assigned`) and (b) auto-suppresses the cloud agent's pending retry (`issue_reassigned`). No restart, no run cancellation needed.
5. **Structured handoff comment** (GOVERNANCE.md §1) — post before/at reassign:
   ```
   FAILOVER-HANDOFF
   from: <cloud agent name/id>  (paused: claude_transient_upstream, resets ~<retryNotBefore>)
   to:   <local understudy name/id>
   tier: IC
   context: <1-3 line summary of where the work stands + the next concrete step>
   constraints: continue ONLY bounded IC sub-work; do NOT make director-level calls;
                operate under the original issue's data scope (AGENT_OPERATIONS_GUIDE §1);
                never log/echo secrets; if work needs a decision above IC, mark blocked + escalate.
   review-on-recovery: REQUIRED — original cloud agent reviews before acceptance.
   ```
   The comment is the handoff payload (Paperclip has no separate context-transfer primitive; the issue thread IS the shared context the new assignee receives on `issue_assigned`).

### 2.3 Review-on-recovery
- **Marker/state:** the `FAILOVER-HANDOFF` comment records the original cloud assignee. The understudy, when it finishes its bounded sub-work, sets issue status → `in_review` and **reassigns `assigneeAgentId` back to the original cloud agent**, posting a `FAILOVER-RETURN` comment ("local understudy completed X; cloud agent: review before accept").
- **Trigger:** reassigning back auto-wakes the cloud agent (`issue_assigned`). If the cloud agent is still limited, its run transient-fails and rides the **native** retry/backoff until reset — i.e. the platform already handles "not back yet" correctly; we don't need to poll for recovery time ourselves. When it does wake, its standing instructions say: *if woken on an `in_review` issue carrying `FAILOVER-HANDOFF`/`FAILOVER-RETURN` and assigned back to you, REVIEW the understudy's output (correctness + the §4 quality gate for the lower-capability local model) before moving to `done`.*
- This makes review-on-recovery **mandatory** (board ask #3) and doubles as the quality gate for Qwen-vs-Opus capability gap.

### 2.4 Marker mechanism
Markers are **structured comment sentinels** (`FAILOVER-HANDOFF`, `FAILOVER-RETURN`, `NO-FAILOVER`) — no schema change needed; Paperclip has no native label primitive exposed for this. The Monitor greps the issue thread for them. (If the board prefers a first-class field, that becomes a small Option-B add-on.)

### 2.5 Why Option A is the lightest viable mechanism
- Zero change to control-plane core. Uses only: cron routine (native), assignment-wakeup (native), retry-suppression-on-reassign (native), comments + PATCH (native API).
- Fully reversible: delete the routine + monitor agent and behavior reverts to native same-agent retry.
- Naturally approval-gated and auditable: every failover is a visible reassignment + comment in the issue thread.

### 2.6 Costs / limits of Option A
- **Latency:** up to one poll interval (~5 min) before failover fires. Acceptable for the stated problem (limits last hours).
- **Monitor run cost:** one local-agent run per tick. Mitigate with `skip_if_active` + only acting when matches exist. Local Qwen3.6 = no cloud token cost.
- **Relies on agent discipline** for the review step (standing instruction), vs. an enforced state machine. The review marker + `in_review` status make non-review visible.

---

## 3. Design — Option B (FALLBACK): server-side recovery-observer (core code)

Add, in `server/src/services/recovery/service.ts` (where transient classification already lives, §1.2), a branch: when a continuation failure is `claude_transient_upstream` AND the assignee is in a configured `failoverPairings` map AND the issue is IC-tier AND not flagged sensitive → instead of scheduling a same-agent retry, set `assigneeAgentId` = understudy (reusing the existing wakeup path) and write the handoff marker; record `failoverOriginAgentId` on the issue for deterministic review-on-recovery.

- **Pros:** instant (no poll latency), deterministic, enforced (not discipline-based), no standing monitor agent/run cost.
- **Cons:** modifies **shared control-plane core** → higher blast radius, must not regress the existing retry path for non-paired agents, requires QA on the recovery service + a config surface for the pairing map, board approval for a core change. A new first-class `failoverOriginAgentId` field = a DB/schema migration.
- **Recommendation:** only if Option A's polling latency or discipline-reliance proves inadequate in practice. Start with A; B is a known upgrade path.

---

## 4. Governance & security implications

- **Tier rule enforced (CEO doc §Tier rule):** IC/production → auto-failover OK. **C-suite & Director decisions → NEVER auto-delegate to local;** they wait/escalate. Implemented as the §2.2 step-1 allowlist (only pairing-map agents fail over).
- **Local continuing cloud work — data sensitivity (AGENT_OPERATIONS_GUIDE §1):** the understudy inherits the issue's existing scope; it gains no new credential/path access. Handoff comment carries no secrets (§5 of my charter: never log/echo/paste secrets). Default-deny: any issue flagged `NO-FAILOVER` (security/credential/PII) escalates instead of failing over.
- **Same-department only:** understudies in the CEO map are within the same functional department — failover never crosses department/company boundaries.
- **Capability gap → mandatory review:** lower-capability local model output is never accepted without cloud review-on-recovery (board ask #3 = also the quality gate).
- **Loop/runaway safety:** idempotency marker (§2.2 step 2) prevents re-failover ping-pong; `skip_if_active` prevents overlapping monitor runs.
- **New standing capability:** the Monitor agent + routine is itself a new autonomous capability → board approval required before standing it up (no autonomous new-capability install per §5 / AGENT_OPERATIONS_GUIDE §1).

---

## 5. Open questions for CEO / board

1. **Approve Option A** (routine + Failover Monitor agent, no core code) as the build target? Or direct Option B (core change)?
2. **Failover Monitor agent** — approve hiring one lightweight local agent for this, or assign the monitor duty to an existing local agent via routine?
3. **Poll interval** — 5 min acceptable, or tighter?
4. **Sensitivity default** — confirm default-deny on `NO-FAILOVER`-flagged issues; should any department be excluded entirely?
5. Build is **gated**: nothing is stood up until approval.

---

## 6. Disposition
- Design + feasibility verdict: **complete**. Native failover does not exist; lightest viable mechanism (Option A) is feasible with zero core code change using existing assignment-wakeup + retry-suppression primitives.
- **Gated on CEO review → board approval before any implementation.** On approval, decompose into child issues: (a) hire/assign Monitor agent, (b) author cron routine + algorithm prompt, (c) encode pairing-map config, (d) add standing review-on-recovery instruction to cloud agents, (e) canary on one IC pairing before fleet-wide.
