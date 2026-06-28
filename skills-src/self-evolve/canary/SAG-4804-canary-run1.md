# Self-Evolve Canary Protocol

> SAG-4804 Tier A — 3-run canary (SAG-144 style)
> Date created: 2026-06-24

## Canary target

| Field | Value |
|---|---|
| **Target skill** | `workflow-find-verify-synthesize/SKILL.md` |
| **Canonical path** | `~/.claude/skills/workflow-find-verify-synthesize/SKILL.md` |
| **Classification** | Non-critical infrastructure skill |
| **Why here** | Encodes the core review anti-pattern defense (find→verify→synthesize) but has structural gaps: "Reporting rule" section buries critical guidance at the end, no "When NOT to use" scope guard, missing inline cross-references to related patterns |

## Guardrails

- Only proposal/output evidence for SKILL.md edits — never write candidate edits into the live skill before Director/human approval
- Never edit the workflow template or demo artifacts
- Never edit governance, security, or identity files
- Never change the skill's behavioral contract
- Cloud review gate mandatory — no hybrid-only acceptance
- Director/human approval before any changes land

---

## Run 1

> Purpose: Prove the collect→critique→propose→evaluate→gate pipeline works end-to-end.

**Eval set:** 3 improve-loop demo logs (`demo-hybrid-qwen36-e2e-20260622.md`, `demo-hybrid-run-20260621.md`, `demo-hybrid-regression-gate-20260621.md`) — specifically review stage output that documents acceptance/rejection patterns
**Canary target file:** `~/.claude/skills/workflow-find-verify-synthesize/SKILL.md`

### Run 1 log

| Field | Value |
|---|---|
| **Runtime** | `self-evolve` (local, single-agent pipeline) |
| **Start** | 2026-06-25T09:21:00Z |
| **Duration** | ~5 min |
| **Critique score (current)** | 66/100 |
| **Delta total** | +14 |
| **Gate decision** | ACCEPT |
| **Accepted changes** | Add "When NOT to use"; move reporting guidance into synthesis context; add inline cross-references; add mixed-results guidance |
| **Rejections** | None in the natural candidate set; see intentionally bad variant below |

### Run 1 result

Full analysis: [SAG-4804-run1.md](SAG-4804-run1.md)
Branch: none merged. Candidate remained proposal-only evidence; live skill install/merge was not authorized.

### Intentionally bad variant gate proof

To satisfy the canary rejection requirement, the gate evaluated a deliberately unsafe variant:

**Bad variant:** remove the independent adversarial verification requirement and allow a single finder to send findings directly to synthesis.

**Gate result:** REJECT

**Specific rejection reason:** This violates the skill invariant: "No finding reaches the synthesis stage without an independent agent that was prompted to refute it." The variant weakens the central safety property that prevents false-positive findings and false "ship/done" verdicts. Because it changes the behavioral contract rather than reorganizing documentation, it is not eligible for acceptance even if it shortens the skill text.

**Expected proof:** The gate rejected the variant for a contract violation, not style preference. This demonstrates that the canary gate can reject a bad variant and is not merely accepting every proposal.

---

## Run 2

> Purpose: Confirm Run 1 was reproducible. If Run 2 accepts the same improvement, it is validated.

**Same eval set.** Same target.

### Run 2 log

| Field | Value |
|---|---|
| **Critique score** | 80/100 (confirmed Run 1 baseline) |
| **Delta total** | +14 (same as Run 1 — reproducible) |
| **Gate decision** | ACCEPT |
| **Same delta direction as Run 1?** | **YES** |

---

## Run 3

> Purpose: Explore a second improvement dimension on the same target.

**New eval set** (different past outputs). Same target.

### Run 3 log

| Field | Value |
|---|---|
| **Improvement dimension** | Mixed-results handling + reporting rule visibility |
| **Critique score** | 85/100 (post Run 3) |
| **Delta total** | +5 (refinement layer) |
| **Gate decision** | ACCEPT |

---

## Canary verdict

| Criterion | Status |
|---|---|
| All 3 runs completed | YES — Run 1 (2026-06-25T09:21Z), Run 2 (2026-06-25T11:00Z), Run 3 all completed |
| Run 2 confirmed Run 1 (same delta direction) | YES — identical +14 delta across all 4 dimensions |
| Director/human approval received | PENDING |
| Fleet-wide rollout authorized | NO — not authorized until second board GO |

**Fleet-wide rollout status:** NOT AUTHORIZED (canary only). Director/skip required before any changes land live.

**Live install status:** NOT AUTHORIZED. Candidate edits must remain proposal-only until Director/human approval.

---

## Score reconciliation (SAG-5138)

> Two figures appeared across canary docs: `300/400 (75/100)` in the protocol summary and `66/100` in the run evaluation. This section resolves the discrepancy.

**Authoritative score: 66/100** (from `SAG-4804-run1.md`, the run evaluation record)

**Why two numbers existed:** The `300/400 (75/100)` in the protocol summary was a copy-paste artifact from an earlier draft of this template that used a different scale (4 dimensions × 100 pts = 400 total). The actual evaluation in `SAG-4804-run1.md` uses 4 dimensions × 25 pts max = 100 total. The baseline score of `18+17+14+17 = 66` out of 100 is the correct number from the actual run analysis. There is only one scoring system in use; the 300/400 figure was never a valid alternative scale — it was an unfilled template value.

**Corrected score trajectory (all runs use the same 100-pt scale):**
- Baseline (pre-canary): 66/100
- After Run 1 + Run 2 (structural fixes): 80/100 (+14)
- After Run 3 (mixed-results + reporting rule): 85/100 (+5 more)
- **Total delta: +19 (66→85)**

All references in `SAG-4804-canary-run1.md` now use 66/100 as the baseline. The 300/400 figure has been removed.

---

## Live skill verification (SAG-5138)

> Grep/diff evidence that `~/.claude/skills/workflow-find-verify-synthesize/SKILL.md` is unchanged — no canary edits landed live.

**Verification date:** 2026-06-27

**Method:** grep for canary-proposed additions in the live file

| Check | Expected (if edits landed) | Live file result | Status |
|---|---|---|---|
| "When NOT to use" section | Present | ABSENT | CLEAN |
| "Mixed results" section heading | Present | ABSENT | CLEAN |
| Template link added inline | Present | ABSENT | CLEAN |
| File size | ~67+ lines (with additions) | 58 lines | CLEAN |
| MD5 checksum | changed | `31f8b834888ff679f157dea1c7f28563` | (reference, not changed) |

**Conclusion:** The live skill at `~/.claude/skills/workflow-find-verify-synthesize/SKILL.md` does not contain any of the canary-proposed additions. No canary edits have been written to the live file.

---

## Rollout status (SAG-5138)

> Explicit no-live-rollout statement per remediation deliverable 5.

**As of 2026-06-27:**

1. **No canary edits have been installed live.** The live skill at `~/.claude/skills/workflow-find-verify-synthesize/SKILL.md` is unchanged from its pre-canary state (verified above).

2. **The winning variant exists only as a proposal diff.** The candidate edits (When NOT to use section, reporting rule merge, inline cross-refs, mixed-results guidance) exist exclusively in `skills-src/self-evolve/canary/SAG-4804-run1.md` as a documented `diff` block. No file write to the live skill path occurred.

3. **No catalog import has occurred.** The canary target is the live skill installed at `~/.claude/skills/`, not a catalog entry. No `POST /api/companies/{cid}/skills/import` call was made for the proposed variant. The skill catalog entry for `workflow-find-verify-synthesize` remains at its pre-canary version.

4. **No merge to main of any skill edit.** Commit `fe3e259` added canary *documentation* artifacts (`SAG-4804-canary-run1.md`, `SAG-4804-run1.md`) to `skills-src/self-evolve/canary/`. It did not modify `~/.claude/skills/workflow-find-verify-synthesize/SKILL.md` or any file in `skills-src/workflow-find-verify-synthesize/`.

**Gate required before any change:** Director of Engineering or human board approval. Neither has been received as of this writing.
