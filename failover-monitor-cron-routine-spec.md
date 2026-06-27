# Failover Monitor — 5-Minute Cron Routine Spec

**Source Issue:** SAG-4809 (parent: SAG-4613, grandparent: SAG-4573)
**Design Reference:** SAG-4590 §2.1 — Option A
**Status:** Ready for board approval + implementation

---

## Routine Configuration

```yaml
routine:
  name: "SAG-4613-Failover-Monitor"
  description: "5-minute cron that wakes the Failover Monitor agent to scan for cloud-limit-stalled issues and auto-failover to local understudies per the pairing map."
  type: cron
  schedule: "*/5 * * * *"          # Every 5 minutes
  concurrency_policy: skip_if_active  # Never run overlapping ticks
  enabled: true
  
  # Wake payload (what the Monitor receives as its prompt)
  wake_instructions: |
    FAIL-OVER TICK:
    
    Run your monitoring algorithm (see AGENTS.md).
    
    Steps:
    1. Load pairing map from your instructions bundle
    2. Scan for in-flight issues with claude_transient_upstream
    3. Apply tier gate, idempotency gate, sensitivity gate
    4. Failover matching issues to local understudies
    5. Log results on SAG-4809
    
    Report results as a structured tick log (see AGENTS.md "Done" section).
```

## Why 5 Minutes

- Cloud session limits last **hours** (e.g., "resets 9pm")
- 5-minute latency is acceptable for the stated problem
- Keeps Monitor run cost low (one local-agent run per tick, which is essentially free)
- Tighter intervals (1-2 min) would increase cost without meaningful benefit for hourly-reset limits

## Concurrency Policy: skip_if_active

- Prevents overlapping monitor ticks (which could cause double-failovers or race conditions)
- If a previous tick is still running, the next is skipped silently
- The next tick after the previous completes picks up any missed issues (the scan is always current)

## Routine → Agent Linkage

When this cron fires, it wakes the **Failover Monitor** agent (not the CTO, not any cloud agent).

- **Agent:** Failover Monitor (opencode_local, Qwen3.6)
- **Status:** PENDING HIRE (see `failover-monitor-hire-request.json`)
- **Reports to:** CTO (Opus 4.8)

The routine's wake payload instructs the Monitor to execute its full algorithm. No additional logic lives in the routine itself—the routine is purely a scheduler.

## Board Approval Items

1. **Approve the routine** (schedule, concurrency policy, wake instructions)
2. **Approve hiring the Failover Monitor agent** (see `failover-monitor-hire-request.json`)
3. **Approve the pairing map** (see `failover-pairing-map.yaml`, pending actual agent IDs)
4. **Confirm 5-minute interval** or adjust
5. **Canary rollout:** approve starting with ONE pairing (e.g., Senior SWE ↔ Senior SWE Local) before fleet-wide

## Rollout Plan

1. **Phase 1 (canary):** Hire Monitor, encode 1 pairing from the map, start cron
2. **Phase 2:** Verify failover works end-to-end (handoff comment posted, understudy wakes, completes, reassigns back via `in_review`)
3. **Phase 3:** Add remaining pairings from the map
4. **Phase 4:** Monitor for any issues (loops, wrong-tier failovers, sensitivity violations)
5. **Phase 5:** Fleet-wide activation + board sign-off on operational viability

---

## Appendix A: Algorithm Reference

See `failover-monitor-agents.md` for the full algorithm (Steps 0-6).

## Appendix B: Idempotency Guarantees

1. **skip_if_active:** No overlapping ticks
2. **FAILOVER-HANDOFF marker:** Monitor checks for existing markers before re-failover'ing
3. **Idempotent PATCH:** Reassigning to the same agent is a no-op (API-level)
4. **NO-FAILOVER marker:** Sensitive issues are excluded from all failover logic

## Appendix C: Governance

- Tier gate enforced: C-suite/Director = never auto-failover
- Sensitivity gate enforced: agents flagged `NO-FAILOVER` = never auto-failover
- Review-on-recovery is mandatory (board requirement): understudy sets `in_review` after completing work; cloud agent reviews before `done`
- No capability gap: Monitor only makes deterministic API calls + rule evaluation
