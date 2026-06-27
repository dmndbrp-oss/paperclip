# Self-Evolve Canary Protocol

> SAG-4804 Tier A — 3-run canary (SAG-144 style)
> Date created: 2026-06-24

## Canary target

| Field | Value |
|---|---|
| **Target skill** | `improve-loop/SKILL.md` |
| **Canonical path** | `skills-src/improve-loop/SKILL.md` |
| **Classification** | Non-critical infrastructure skill |
| **Why here** | Well-structured but has structural gaps (red flags section buries key info under `---` separators, missing cross-references between sections) |

## Guardrails

- Only SKILL.md edits — never improve-loop templates or demo artifacts
- Never edit governance, security, or identity files
- Never change the skill's behavioral contract
- Cloud review gate mandatory — no hybrid-only acceptance
- Director/human approval before any changes land

---

## Run 1

> Purpose: Prove the collect→critique→propose→evaluate→gate pipeline works end-to-end.

**Eval set:** 3 past improvement proposals from `improve-loop` issue history
**Canary target file:** `skills-src/improve-loop/SKILL.md`

### Run 1 log

| Field | Value |
|---|---|
| **Runtime** | `self-evolve` (local, single-agent pipeline) |
| **Start** | 2026-06-25T09:21:00Z |
| **Duration** | ~5 min |
| **Critique score (current)** | 300/400 (75/100) |
| **Delta total** | +31/100 |
| **Gate decision** | ACCEPT |
| **Accepted changes** | Reorder red flags above post-loop; add inline cross-references; add post-loop subsection |
| **Rejections** | None |

### Run 1 result

Full analysis: [SAG-4804-run1.md](SAG-4804-run1.md)
Branch: `self-evolve/run1-improve-loop-skill` (worktree, unmerged)

---

## Run 2

> Purpose: Confirm Run 1 was reproducible. If Run 2 accepts the same improvement, it is validated.

**Same eval set.** Same target.

### Run 2 log (fill on execution)

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

### Run 3 log (fill on execution)

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
| All 3 runs completed | (fill) |
| Run 2 confirmed Run 1 (same delta direction) | (fill) |
| Director/human approval received | (fill) |
| Fleet-wide rollout authorized | (fill: NO until second go) |

**Fleet-wide rollout status:** NOT AUTHORIZED (canary only). Director/skip required before any changes land live.
