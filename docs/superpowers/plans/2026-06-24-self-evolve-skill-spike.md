# self-evolve — Prompt/Skill Improvement Loop

> Invocation token: **`self-evolve`** (lowercase-kebab `name:` slug).

**Goal:** Build a bounded, gated Skill/Prompt Improvement Loop that targets the agents' own SKILL.md files, prompts, and playbooks — extending the improve-loop pattern from product code to agent self-improvement.

**Architecture:** A new skill `self-evolve` in `skills-src/self-evolve/` mirroring improve-loop's structure: SKILL.md (invocation + guardrails), cloud engine (Claude only), and hybrid engine (local generation + Claude review gate). New canary subdirectory for 3-run protocol tracking.

---

## Context

SAG-4787 assessed the `hermes-agent-self-evolution` ecosystem and concluded Tier A (API-only prompt/skill evolution, ~$2-10/run) is viable on our primitives. SAG-4802 approved Tier A with these constraints:

- **Scope to ONE skill** (non-critical) as canary target
- **3-run canary protocol** (SAG-144 style) before any fleet-wide rollout
- **No auto-merge**: Director/human approval before any changes land
- **Governance/security files out of scope**
- **Cloud review on every candidate**
- **Isolated git worktrees only**, never on main or live tree

---

## File Structure

```
skills-src/self-evolve/
  SKILL.md                               — Skill definition, invocation, guardrails, canary protocol
  templates/
    self-evolve.workflow.js              — Cloud engine (all Claude)
    self-evolve-hybrid.workflow.js       — Hybrid engine (local gen + Claude review gate)
  canary/
    SAG-4804-canary-run1.md             — Canary run 1 log
    SAG-4804-canary-run2.md             — Canary run 2 log (created at run time)
    SAG-4804-canary-run3.md             — Canary run 3 log (created at run time)
  demo/
    demo-self-evolve-skillsrc.md         — Demo of self-evolve targeting skills-src/ directory
```

## Changes Summary

### 1. `skills-src/self-evolve/SKILL.md` (CREATE)
- Skill metadata (name, slug, license)
- Overview: extend improve-loop from product code → agent prompts/skills/playbooks
- Why: our prompts and SKILL.md files are code artifacts that accumulate technical debt
- Naming rationale for `self-evolve` (avoids collision with existing naming patterns)
- Runtimes: cloud vs hybrid (same model as improve-loop)
- 5-step flow: Collect → Critique → Propose → Evaluate → Gate
- Arguments (extends improve-loop args with canary-specific options)
- Canary protocol (3-run, SAG-144 style)
- Hard guardrails (extends improve-loop guardrails, adds self-targeting bias check)
- Output schema (adds eval delta column)
- Post-loop verification
- Red flags (adds self-aggrandizement and scope creep)
- See also section

### 2. `skills-src/self-evolve/templates/self-evolve.workflow.js` (CREATE)
- Cloud engine: `meta` block with 5 phases (Collect, Critique, Propose, Evaluate, Gate)
- Config args: extends improve-loop args + `targetSkill` (required), `canaryProtocol` (bool)
- **Collect phase**: Agent assembles eval set from past skill outputs
- **Critique phase**: Reviewer scores eval set against rubric (clarity, safety, traceability, completeness)
- **Propose phase**: Generate candidate refinement in worktree (same pattern as improve-loop CODE)
- **Evaluate phase**: A/B comparison — compute delta between candidate and baseline on eval set
- **Gate phase**: If delta > threshold → ACCEPT, else → REJECT with specific deficit
- Review panel adapts lenses for self-targeting:
  - `no-self-bias`: does this reviewer unfairly favor their own work style?
  - `measurable-delta`: is the improvement quantified, not anecdotal?
  - `scope-bounded`: does this stay within one skill's scope?
- Output adds `evalDelta` column: positive = improvement, negative = regression

### 3. `skills-src/self-evolve/templates/self-evolve-hybrid.workflow.js` (CREATE)
- Hybrid engine: local model generates critiques + proposals, cloud reviews
- Preflight (same as hybrid-improve-loop)
- Collect + Critique: local model produces scores against rubric
- Propose: local model generates SKILL.md diff in worktree
- Evaluate + Gate: Claude cloud performs A/B eval and gate decision
- REVIEW lens adaptations for self-targeting (same as cloud engine)

### 4. `skills-src/self-evolve/canary/SAG-4804-canary-run1.md` (CREATE)
- Canary protocol skeleton (empty, to be filled at run time)
- Structure: target skill, eval set, runs 1-3 logs, gate decision
- Pre-filled with canary target: `skills-src/improve-loop/SKILL.md` (non-critical infrastructure skill)

### 5. `skills-src/self-evolve/demo/demo-self-evolve-skillsrc.md` (CREATE)
- Demo simulating self-evolve on `skills-src/improve-loop/SKILL.md`
- Collect: past outputs = improvement proposals for improve-loop
- Critique: rubric scoring reveals "hard-to-find invocation examples" gap
- Propose: candidate adds invocation examples section to SKILL.md
- Evaluate: compare structured vs unstructured invocation data
- Gate: ACCEPT with 3-run canary protocol documentation

---

## Task Breakdown

### Task 1: SKILL.md — Create `skills-src/self-evolve/SKILL.md`
**Scope:** Core skill definition. Directly follows improve-loop's structure with self-evolve-specific adaptations.

### Task 2: Cloud Engine — Create `templates/self-evolve.workflow.js`
**Scope:** Full cloud engine with 5 phases. Reuses improve-loop workflow's review pattern with adapted lenses.

### Task 3: Hybrid Engine — Create `templates/self-evolve-hybrid.workflow.js`
**Scope:** Local generation + Claude review gate. Mirrors improve-loop-hybrid's architecture with self-evolve phases.

### Task 4: Canary Protocol — Create `canary/SAG-4804-canary-run1.md`
**Scope:** Pre-filled canary protocol targeting improve-loop/SKILL.md as the canary subject.

### Task 5: Demo — Create `demo/demo-self-evolve-skillsrc.md`
**Scope:** Complete demo of self-evolve run against improve-loop/SKILL.md.

### Task 6: Integration verification
**Scope:** Create a test YAML that can a valid self-evolve SKILL.md; verify no plan
