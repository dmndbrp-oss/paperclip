---
name: improve-loop
slug: improve-loop
license: Proprietary — Sage Surfaces internal
description: >
  Bounded, approval-gated code-quality improvement loop (plan→code→review, loop-until-dry).
  Runs until dry-streak or iteration cap. Quality-only: reuse/simplify/efficiency. All changes
  apply in isolated git worktrees — never on main, never auto-merged. Human/Director approval
  required before any merge. Use when you want a structured multi-round code quality sweep on a
  target directory or file.
  Triggers on: "improve-loop", "code quality loop", "improvement loop", "quality sweep",
  "loop until no more improvements", "iterative code improvement".
---

# improve-loop — Bounded Code-Quality Improvement Loop

> Invocation token: **`improve-loop`** (lowercase-kebab `name:` slug).

## Overview

Runs plan→code→review rounds until the codebase stops yielding improvements or the loop
hits a hard cap. Every change is isolated in its own git worktree branch. **No merge or PR is
ever created automatically** — the output is a proposal set for human review. It is the
realization of the board's "loop" request: a workflow that uses planning, coding, and
reviewing in a loop until the agents can no longer improve the code base.

---

## Naming rationale (COLLISION WARNING — read before renaming)

The board asked for a skill "called loop." That name cannot be used:

- The Paperclip harness ships a native **`loop`** skill + `/loop` slash command for recurring
  interval tasks (e.g. `/loop 5m /foo`). A company skill literally named `loop` would collide
  on trigger/invocation with that built-in.
- `terminal-bench-loop` is another reserved name in the harness.
- **Decision (CTO, SAG-4533):** name this skill `improve-loop`. It is distinct, descriptive,
  and unambiguous. The board's intent ("code-quality loop") is preserved.

**CEO/CTO: please confirm with the board that `improve-loop` satisfies their "loop" request.**
This is flagged here and in the SAG-4533 completion comment per the CTO's instruction.

---

## Runtimes: cloud vs hybrid

This skill ships two runtime flavors. Choose based on cost/latency tradeoff.

### Cloud engine (`improve-loop.workflow.js`) — default

- **All stages on Claude:** PLAN, CODE, and REVIEW all run as Workflow subagents (Claude).
- **Token cost:** Moderate per round (3–5 Claude agent calls).
- **Parallelism:** REVIEW panel is parallel; PLAN/CODE are sequential.
- **When to use:** Quality-critical sweep, short budget, or when Ollama is not available.

```js
Workflow({ scriptPath: 'skills-src/improve-loop/templates/improve-loop.workflow.js',
           args: { target: 'pilot-artifacts/validator.py', maxIterations: 3 } })
```

### Hybrid engine (`improve-loop-hybrid.workflow.js`) — low-cost

- **PLAN + CODE on local Qwen3.6** (Ollama, `http://127.0.0.1:11434`). Generation is local — ~zero cloud tokens for those stages.
- **REVIEW on Claude (UNCHANGED).** The adversarial 3-lens gate is the safety value and always runs on Claude.
- **Fail fast:** If Ollama is unreachable or the model is absent, the run stops immediately with a clear error. No silent cloud fallback (that would defeat the cost goal).
- **Token cost:** PLAN+CODE ≈ minimal Claude tokens (bridge coordination only). REVIEW ≈ same as cloud engine.
- **Parallelism:** REVIEW panel still parallelizes on Claude. PLAN/CODE serialize on Ollama (see caveat below).
- **When to use:** Cost-sensitive sweeps on the Sage Surfaces box, or to demonstrate local-first generation with a cloud safety gate.

```js
Workflow({ scriptPath: 'skills-src/improve-loop/templates/improve-loop-hybrid.workflow.js',
           args: { target: 'pilot-artifacts/validator.py', maxIterations: 3,
                   ollamaUrl: 'http://127.0.0.1:11434', localModel: 'qwen3.6:latest' } })
```

#### Hybrid-specific args

| Arg | Type | Default | Description |
|---|---|---|---|
| `ollamaUrl` | string | `'http://127.0.0.1:11434'` | Ollama base URL |
| `localModel` | string | `'qwen3.6:latest'` | Model tag for PLAN+CODE generation |

#### Known caveat: Qwen3.6 serializes on Ollama

Qwen3.6 uses the `qwen35moe` architecture. Ollama force-caps `qwen35moe` to **1 slot**
regardless of `OLLAMA_NUM_PARALLEL` (sched.go:423, upstream #14510/#4165). This means
PLAN and CODE calls always run one at a time — they cannot parallelize. **This is not a
hang.** It is expected serial scheduling. The REVIEW panel (Claude) still parallelizes.
Net effect: the hybrid is slower in wall-clock per round than the cloud engine but uses
far fewer cloud tokens for PLAN+CODE.

---

## Compliance with Loop Engineering Standard (LES v1.0, SAG-3578)

This is a **cloud loop** (Workflow tool, token-funded). LES §3 cloud requirements are met:

| LES §1 requirement | How this skill satisfies it |
|---|---|
| Goal / success criterion | `dryStreakToStop` consecutive rounds with no accepted improvement |
| Tools | Workflow agents: file readers, bash, Edit/Write — bounded by harness |
| Context management | `seen` set passed per-round; accepted/rejected tracked in workflow state |
| Termination logic | `dryStreak >= dryStreakToStop` OR `iter >= maxIterations` OR budget floor |
| Error handling | Unapplied/failed changes → `dryStreak++`, logged, loop continues cleanly |

---

## When to Use

- You want to systematically improve a file or subsystem for quality (reuse, simplify, efficiency).
- The task is open-ended ("make this cleaner") and you want a bounded, auditable loop.
- You need to deliver improvement proposals that a human will approve before merging.

**Do NOT invoke when:**
- The task is a specific bug fix (use `systematic-debugging`).
- You want a one-shot review (use `workflow-find-verify-synthesize`).
- You need to merge automatically (this skill intentionally cannot do that).

---

## Arguments

| Arg | Type | Default | Description |
|---|---|---|---|
| `target` | string | `'.'` | Glob, path, or subsystem to sweep (e.g. `'pilot-artifacts/validator.py'`) |
| `maxIterations` | number | `8` | Hard iteration cap — loop always stops here even with streak room |
| `dryStreakToStop` | number | `2` | Stop after this many consecutive non-accepted rounds |
| `budgetFloor` | number | `20000` | Stop when `budget.remaining() <= budgetFloor` tokens |

### Typical invocations

```js
// Quick demo: 3 rounds max, stop after 2 dry
Workflow({ scriptPath: 'skills-src/improve-loop/templates/improve-loop.workflow.js',
           args: { target: 'pilot-artifacts/validator.py', maxIterations: 3, dryStreakToStop: 2 } })

// Full sweep of a subsystem
Workflow({ scriptPath: 'skills-src/improve-loop/templates/improve-loop.workflow.js',
           args: { target: 'enrichment/', maxIterations: 8, dryStreakToStop: 2 } })
```

---

## What happens each round

```
Round N
 ├── PLAN  — one agent scans `target`, proposes the highest-value quality improvement
 │           not in the `seen` set. Schema-forced output. If no improvement, dry streak++.
 ├── CODE  — applies the change in an ISOLATED GIT WORKTREE (never on main/live tree).
 │           Runs `verifyCmd` inside the worktree. Returns diff + branch + verifyOutput.
 │           If apply fails or verify fails, dry streak++, worktree discarded.
 └── REVIEW — 3 adversarial skeptics, distinct lenses, default-to-refute:
              (a) real-improvement: is this genuine quality gain, not churn?
              (b) no-regression: does it preserve behavior (quality-only)?
              (c) verify-green: did verifyCmd actually pass meaningfully?
                  REJECTS syntax-only checks (ast.parse, etc.) when a test suite exists.
              Majority accept + verifyPassed → ACCEPTED (branch kept, dry streak = 0).
              Otherwise → REJECTED (branch kept for audit, dry streak++).
```

**verifyCmd discovery rule** (enforced by the PLAN prompt, SAG-4872):

The planner MUST check for a test suite before choosing `verifyCmd`:
- Python: look for `test_*.py` / `*_test.py` in the same dir or a sibling `test/` dir → use `pytest`
- JS/TS: look for `*.test.js` / `*.spec.js` or a `package.json` `"test"` script → use `npm test`
- **Fallback** to syntax-only (`ast.parse`, import, etc.) ONLY when no test file exists.

A syntax-only `verifyCmd` when a test suite is present will be rejected by the `verify-green`
reviewer lens. This rule exists because syntax-only checks miss behavioral regressions — the
SAG-4868 incident demonstrated this exactly (an accepted refactor broke `test_validator.py::test_invalid_application_enum_item`).

---

## Hard guardrails (non-negotiable)

1. **No auto-merge, no PR-create.** The workflow returns accepted branch names. A human or
   Director must approve and merge. Nothing in this script merges or creates PRs.
2. **Worktree isolation.** Every `CODE` agent runs with `isolation: 'worktree'`. Changes never
   touch main or the live working tree.
3. **Bounded.** `maxIterations`, `dryStreakToStop`, and budget guard are all enforced. The loop
   cannot run forever.
4. **Quality-only.** PLAN agents are instructed to avoid bug fixes and behavior changes. If a
   bug is discovered, it is surfaced in the output but NOT silently rewritten.
5. **No silent truncation.** Every dropped/skipped/rejected item is `log()`-ed and included in
   the final return object.
6. **Verify-before-accept.** A change is accepted ONLY if `verifyCmd` passed AND the review
   majority voted accept.

---

## Output

```js
{
  target: string,
  stopReason: 'dry-streak (N consecutive)' | 'iteration cap (N)' | 'budget floor',
  rounds: number,
  accepted: [{ title, kind, branch, iter, diffSummary }],
  rejected: [{ title, kind, reason, iter }],
  noAutoMerge: 'Accepted changes are in worktree branches. No merge or PR was created. Human approval required before any merge.',
}
```

---

## After the loop

1. Review `accepted` branches manually (`git log`, `git diff main...branch`).
2. Run full test suite on each.
3. Merge at your discretion — the skill does not do this.
4. Discard rejected branches: `git worktree remove --force <path>` or let them expire.

---

## Verification

Before trusting a run, confirm the guardrails actually held:

1. **It stopped on purpose.** The returned `stopReason` is one of `dry-streak`, `iteration cap`,
   or `budget floor` — never an unexplained halt.
2. **Nothing was merged.** `git branch` shows the `accepted` worktree branches still unmerged;
   `main` and your working tree are untouched. The `noAutoMerge` field is present in the output.
3. **Each accepted change passed its check.** Every entry in `accepted` corresponds to a round
   where `verifyCmd` returned green AND the review majority voted accept.
4. **Rejections are explained.** Every `rejected` entry carries a `reason`; nothing was silently
   dropped (cross-check the `log()` stream).
5. **Spot-check a diff.** Open one accepted branch and confirm the change is genuine quality gain
   (reuse/simplify/efficiency), not churn or a behavior change.

A reference run is captured under `demo/` (a bounded sweep that correctly rejected a planted
regression and then converged to a clean stop).

---

## Red Flags

Stop and investigate if you see any of these — they mean the loop is not behaving as designed:

- **A merge or PR appeared.** This skill never merges. If something landed on `main`, a different
  process did it — do not trust the result.
- **`accepted` changes alter behavior.** This is a quality-only loop; behavior changes or bug
  "fixes" should be *surfaced*, not silently applied. Treat behavior diffs as a defect.
- **An accepted change has no green `verifyCmd`.** Verify-before-accept was bypassed; reject it.
- **verifyCmd was `ast.parse` or syntax-only and a test file exists.** The PLAN stage picked a
  weak verifyCmd despite a discoverable test suite — the `verify-green` lens should have caught
  this. Audit whether the PLAN prompt was applied correctly and whether the review voted honestly.
- **The loop never reports a `stopReason`** or appears to run unbounded — the termination guard
  failed; kill it and report.
- **Every round is "dry" immediately** on a target that obviously has improvements — the PLAN
  stage or `target` glob is misconfigured.

---

## See also

- Cloud engine: `skills-src/improve-loop/templates/improve-loop.workflow.js`
- Hybrid engine: `skills-src/improve-loop/templates/improve-loop-hybrid.workflow.js`
- Demo artifacts: `skills-src/improve-loop/demo/`
- Parent issues: [SAG-4527](/SAG/issues/SAG-4527), [SAG-4529](/SAG/issues/SAG-4529), [SAG-4533](/SAG/issues/SAG-4533), [SAG-4558](/SAG/issues/SAG-4558)
- Related skills: `workflow-find-verify-synthesize`, `using-git-worktrees`, `verification-before-completion`, `local-dynamic-workflow`
- Governing standard: `LOOP_ENGINEERING_STANDARD.md` (LES v1.0, SAG-3578)
