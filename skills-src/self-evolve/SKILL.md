# self-evolve — Prompt/Skill Improvement Loop

> Invocation token: **`self-evolve`** (lowercase-kebab `name:` slug).

## Overview

Bounded, gated Skill/Prompt Improvement Loop. Targets the agents' own SKILL.md files,
prompts, and playbooks — improving them as artifacts, not just product code they produce.
Layered on improve-loop patterns (worktree isolation, adversarial review, no auto-merge).

5-step flow: **Collect → Critique → Propose → Evaluate → Gate**.

---

## Naming rationale

`self-evolve` signals that the agent's own documentation artifacts are the improvement target.
Distinct from `improve-loop` (generic code quality) and `improve-loop-hybrid` (hybrid runtime).

---

## Why this exists

Prompts and SKILL.md files accumulate technical debt: unclear sections, missing examples,
inconsistent structure. Unlike product code, they are hard for agents to self-edit objectively.
This loop externalizes that objection: a bounded loop that proposes refinements in isolated
worktrees for human review.

## When to Use

- A SKILL.md, AGENTS.md, or playbook feels stale, inconsistent, or unclear.
- You want to improve the prompt/structural quality of one skill without changing its behavior.
- You need improvement proposals that a human will approve before any changes land.

## Do NOT invoke when

- The task is a specific agent behavior bug (use `systematic-debugging`).
- You want a one-shot review (use `workflow-find-verify-synthesize`).
- You need a fleet-wide rollout (canary only, per §24).

---

## Runtimes: cloud vs hybrid

### Cloud engine (`self-evolve.workflow.js`) — default

- **All stages on Claude:** Collect, Critique, Propose, Evaluate, Gate all run as Claude agents.
- **Token cost:** Higher per round (Claude review gate is non-negotiable for self-targeting).
- **When to use:** Quality-critical skill refinement or first canary runs.

```js
Workflow({ scriptPath: 'skills-src/self-evolve/templates/self-evolve.workflow.js',
           args: { targetSkill: 'para-memory-files', maxIterations: 3 } })
```

### Hybrid engine (`self-evolve-hybrid.workflow.js`) — low-cost

- **Collect, Critique, Propose on local model** (Ollama). Generation is local.
- **Evaluate + Gate on Claude.** The adversarial review gate is the safety value — always cloud.
- **Fail fast:** If Ollama is unreachable or the model is absent, the run stops immediately.

```js
Workflow({ scriptPath: 'skills-src/self-evolve/templates/self-evolve-hybrid.workflow.js',
           args: { targetSkill: 'para-memory-files', maxIterations: 3,
                   ollamaUrl: 'http://127.0.0.1:11434', localModel: 'qwen3.6:latest' } })
```

### Config args

| Arg | Type | Default | Description |
|---|---|---|--|
| `targetSkill` | string | *(required)* | Name of the skill/role to evolve (e.g. `'para-memory-files'`, `'internal-comms'`) |
| `maxIterations` | number | `3` | Canaries default to 3; higher for non-canary runs |
| `dryStreakToStop` | number | `2` | Stop after this many consecutive non-accepted rounds |
| `budgetFloor` | number | `20000` | Stop when `budget.remaining() <= budgetFloor` tokens |
| `canaryProtocol` | boolean | `false` | Enable canary-specific gating and run tracking |
| `rubric` | object | `{clarity: 25, safety: 25, traceability: 25, completeness: 25}` | Eval rubric weights (must sum to 100) |

---

## The 5 phases — each "round" = one full Collect→Critique→Propose→Evaluate→Gate cycle

```
Round N
 ├── COLLECT  — Assemble the current skill's source (SKILL.md + all referenced artifacts)
 │              + assemble a held-out eval set from past agent outputs (para-memory/traces).
 │              Exclude infra-error rows (timeouts, wall_s:0 — SAG-4340 lesson).
 ├── CRITIQUE — Score the current skill against the rubric:
 │              - clarity: 25% — Is it unambiguous? Would a new agent execute it correctly?
 │              - safety:  25% — Are guardrails, red flags, scope limits clearly stated?
 │              - traceability: 25% — Can a reader trace the logic from invocation → action?
 │              - completeness: 25% — Are all phases, edge cases, constraints covered?
 ├── PROPOSE  — Generate a candidate refinement to the skill's source
 │              (SKILL.md edits — NOT behavioral changes to the skill itself).
 │              Applied in isolated worktree. Never touches governance/security files.
 ├── EVALUATE — A/B comparison: candidate vs current on held-out eval set.
 │              Compute delta score: candidate_score - current_score.
 │              Also check for regressions: any rubric dimension going down?
 └── GATE     — Decision:
                - delta > 0 AND no dimension regressed → ACCEPT (candidate stored for review)
                - delta <= 0 OR any regression → REJECT (with specific deficit noted)
                - dry streak >= dryStreakToStop → STOP (loop converges)
```

---

## Review lenses (adapted for self-targeting bias)

The adversarial review panel runs on Claude. Default-to-refute.

| Lens | Focus | Default |
|---|---|--|
| `no-self-bias` | Is the reviewer applying the same standard they would to another agent's work? Reject if the review is softer than it would be for external work. | accept:false |
| `measurable-delta` | Is the A/B delta quantified, not anecdotal? Reject if the evaluator can't state a numeric delta for each rubric dimension. | accept:false |
| `scope-bounded` | Does this stay within one skill's scope? Does not change the skill's behavioral contract — only its documentation quality? Reject scope creep. | accept:false |

---

## Hard guardrails (non-negotiable)

1. **No auto-merge, no catalog import.** The workflow returns proposed changes. A human or
   Director must approve and import via `POST /skills/import`. Nothing in this script merges
   or imports.
2. **Worktree isolation.** The PROPOSE agent runs in an isolated git worktree. Changes never
   touch main or the live working tree.
3. **Bounded.** `maxIterations`, `dryStreakToStop`, and budget guard are all enforced.
4. **Documentation quality only.** Changes improve the SKILL.md/prompt itself, not the
   behavior it describes. The skill's functional contract is fixed.
5. **Governance/security out of scope.** Never edit governance, security, or identity files.
6. **No silent truncation.** Every rejected proposal is logged with specific deficit.
7. **Self-bias check.** If the target skill is the agent's own, the review panel includes
   an explicit anti-self-bias lens. The agent reviewing its own work must apply the same
   standard it would apply to any other agent.

---

## Canary protocol (SAG-144 style, 3-run)

When `canaryProtocol: true`:

1. **Run 1:** Prove the loop works end-to-end on the target skill. Acceptance doesn't gate.
2. **Run 2:** Confirm Run 1 was reproducible (not a fluke). If Run 2 accepts the same
   improvement, it is validated.
3. **Run 3:** Explore a second improvement dimension on the same target.

**Before fleet rollout:** Director approval required. Nothing fleet-wide without a second go.

---

## Post-loop verification checklist

1. **It stopped on purpose.** `stopReason` is `dry-streak`, `iteration cap`, or `budget floor`.
2. **Nothing was merged or imported.** `noAutoMerge` is present.
3. **Each accepted proposal has A/B delta.** Every entry in `accepted` includes `evalDelta`.
4. **No dimension regressed.** The `evalDelta` breakdown shows no rubric dimension going down.
5. **Rejections are explained.** Every `rejected` entry carries a `reason` with the specific
   deficit (delta value or regression).
6. **Canary protocol honored.** If canary mode was enabled, all 3 runs completed with logs.

---

## Red flags

- **A catalog import appeared.** This skill never imports. If a skill appeared in the live
  catalog without human gate, a different process did it.
- **Able to accept changes that alter the skill's behavior.** Only documentation quality
  changes are valid. Behavioral changes = defect.
- **delta <= 0 but accepted anyway.** The GATE phase should never accept without positive
  delta AND no regressions.
- **Rubric scores are missing or unweighted.** The A/B eval must score all 4 rubric dimensions.
- **Scope creep into governance/security files.** Hard stop — these are out of scope.
- **The loop produces no rejections on a clearly flawed target.** The adversarial lenses
  may be too lenient against self-targeted work.

---

## Output

```js
{
  targetSkill: string,
  stopReason: 'dry-streak (N consecutive)' | 'iteration cap (N)' | 'budget floor',
  rounds: number,
  currentScore: { clarity: N, safety: N, traceability: N, completeness: N, total: N },
  accepted: [{ title, rubric, branch, iter, evalDelta }],
  rejected: [{ title, rubric, reason, iter }],
  noAutoMerge: string,
  canaryProtocol: { runs: number, runLogs: string[], allRunsComplete: boolean },
}
```

---

## See also

- Parent skill: `skills-src/improve-loop/SKILL.md` (structural ancestor)
- Cloud engine: `skills-src/self-evolve/templates/self-evolve.workflow.js`
- Hybrid engine: `skills-src/self-evolve/templates/self-evolve-hybrid.workflow.js`
- Parent issues: [SAG-4787](/SAG/issues/SAG-4787) (assessment), [SAG-4802](/SAG/issues/SAG-4802) (design: Tier A), [SAG-4804](/SAG/issues/SAG-4804) (this spike)
- Related skills: `improve-loop`, `workflow-find-verify-synthesize`, `using-git-worktrees`, `para-memory-files`
- Governing standard: `LOOP_ENGINEERING_STANDARD.md` (LES v1.0, SAG-3578)
