# SAG-4787 — Hermes Self-Improvement: Feasibility + §4.3 Tier-3 Vetting (design-only, gated)

Owner: CTO (Opus 4.8) · Parent: SAG-4787 (board ask, CEO rollup) · Child issue: bca4c531
Status: DESIGN/RESEARCH ONLY. Nothing in this doc is cloned-to-prod, run, enabled, or trained.
Date: 2026-06-22

---

## 0. TL;DR for the board

- **The flagship repo is Tier A, not Tier B.** `hermes-agent-self-evolution` is explicitly **"No GPU training required… ~$2-10 per optimization run,"** API-only prompt/skill/tool/code evolution via DSPy + GEPA, gated by tests and producing a PR. This is the same shape as our existing `improve-loop` + `para-memory` + GOVERNANCE §24. **The near-term self-improvement win needs no new infra and no training hardware.**
- **Tier B (real weight training) stays infeasible on our box** and is a capital decision. `atropos` (RL environments) and `DisTrO` (distributed training) are genuine training stacks. Our local stack is inference-only (single Framework desktop, gfx1151, no vLLM until ROCm 8.0, MAX_LOADED_MODELS=1, qwen35moe serializes). **Recommendation: NO-GO on local RL self-evolution; revisit only post-ROCm-8.0 with dedicated capacity + budget.**
- **Two supply-chain flags.** (1) `hermes-paperclip-adapter` is real and genuinely runs a full external agent (shell + 30+ tools + MCP + 8 inference providers/egress) inside a Paperclip company — **highest operational/egress risk, and not required for the Tier A goal.** (2) `NousResearch/agent-governance-toolkit` is a mirror whose README/badges/PyPI all point to **`microsoft/agent-governance-toolkit`** — prefer the canonical Microsoft source, not the Nous mirror.
- **Two license blockers.** `hermes-agent-self-evolution` and `DisTrO` carry **no license** (= all-rights-reserved). We may study the *pattern* but **cannot legally adopt their code.** Our Tier A proposal therefore copies the *technique* on top of our own `improve-loop`, not their code.

**Net recommendation:** Pursue Tier A as a bounded, §24-guarded **self-eval → prompt/skill-refinement loop built on our existing improve-loop**, pattern-inspired by GEPA. Do not clone or run any of the eight repos into prod this round. Defer Tier B. Confidence: **8/10.**

---

## 1. §4.3 Tier-3 supply-chain vetting (all eight repos)

Provenance verified via authoritative GitHub REST API (`api.github.com/repos/{owner}/{repo}`), which returns 404 for non-existent repos — stronger than a web-page HTTP 200. READMEs read; **no code cloned or executed.**

| Repo | Owner (type) | Created | Last push | Stars | License | Risk | What it actually is |
|---|---|---|---|---|---|---|---|
| hermes-agent-self-evolution | NousResearch (org) | 2026-03-09 | 2026-06-17 | 4278 | **NONE** ⚠ | MED | DSPy+GEPA prompt/skill/tool/code evolution. **API-only, no GPU. Tier A.** |
| hermes-paperclip-adapter | NousResearch (org) | 2026-03-11 | 2026-04-04 | 1633 | MIT | **HIGH** ⚠ | Runs Hermes Agent as a managed employee in a Paperclip company. Full agent: shell, 30+ tools, MCP, 8 inference/egress providers. |
| Hermes-Function-Calling | NousResearch (org) | 2024-02-24 | 2025-12-22 | 1396 | MIT | LOW | Function-calling format + inference-side parsing. Mature. Tangential to self-improvement. |
| atropos | NousResearch (org) | 2025-04-29 | 2026-06-22 | 1302 | MIT | MED | RL **Environments** framework for collecting/evaluating LLM trajectories. **Tier B (training).** |
| DisTrO | NousResearch (org) | 2024-08-26 | 2025-10-14 | 1040 | **NONE** ⚠ | MED | Distributed Training Over-The-Internet (training optimizer). **Tier B (training).** |
| Open-Reasoning-Tasks | NousResearch (org) | 2024-07-22 | 2024-09-27 (stale) | 494 | Apache-2.0 | LOW | Reasoning-task dataset/list. Could seed eval sets. Tier A-adjacent (data). |
| agent-governance-toolkit | NousResearch (org) | 2026-05-10 | 2026-05-11 | 32 | MIT | MED ⚠ | **Mirror/fork of `microsoft/agent-governance-toolkit`** — README, badges, docs site, PyPI all point to microsoft/. Prefer canonical source. |
| NVIDIA/skills | NVIDIA (org) | 2026-02-25 | 2026-06-22 | 1724 | **NOASSERTION** ⚠ | LOW-MED | Agent skills published by NVIDIA. License unclear (NOASSERTION) — read LICENSE before adopting any skill. Active. |

**Headline vetting conclusions**
1. All eight exist and are owned by the claimed orgs (NousResearch / NVIDIA). No typosquat at the org level.
2. **`hermes-paperclip-adapter` is real, not a fabrication** — it targets the public Paperclip product (paperclip.ing), not specifically *our* internal instance. But it is the single highest-risk artifact: cloning/running it stands up a full third-party agent with shell + broad MCP + multi-provider egress inside a company. That intersects directly with our Photon-relay egress concerns. **It is not needed to achieve Tier A self-improvement of our Qwen agents and should remain unrun this round.**
3. **`agent-governance-toolkit` provenance mismatch:** the artifact is under `NousResearch/` but its entire README/CI/PyPI/npm/NuGet identity is `microsoft/agent-governance-toolkit`. Treat the **Microsoft** repo as canonical; do not adopt the Nous mirror.
4. **License blockers:** `hermes-agent-self-evolution` and `DisTrO` have no license file = all-rights-reserved. Code is study-only; adopt the technique, not the source.

---

## 2. Tier mapping

- **Tier A (in-context / skill / memory — adoptable, no weight change):** hermes-agent-self-evolution (pattern), Open-Reasoning-Tasks (eval data), NVIDIA/skills (skill content), Hermes-Function-Calling (tool-call reliability, tangential), agent-governance-toolkit→use microsoft canonical (governance guardrails).
- **Tier B (weight-level training — infeasible on current hardware):** atropos, DisTrO.

The board's intuition that "self-evolution = training" holds for atropos/DisTrO, but **not** for the headline self-evolution repo, which is squarely Tier A.

---

## 3. Tier A — concrete adoption proposal (the near-term win)

**Goal:** a bounded, gated **self-eval → prompt/skill-refinement loop** for local Qwen agents, layered on what we already run — `improve-loop` (catalog id bf63415c), `para-memory-files`, GOVERNANCE §24 — and pattern-inspired by GEPA (reflective trace analysis → candidate variants → constraint-gated selection → PR).

**Design (no new infra, no spend beyond local inference):**
1. **Capture traces** from existing heartbeat runs (we already persist run logs / transcripts). No new collection infra.
2. **Reflect:** a local agent reads recent failure/low-quality traces for a *single* target artifact (one skill file, one prompt/playbook section) and proposes a refined variant — text only. This is the GEPA "understand *why* it failed" step, done with a local Qwen call.
3. **Constraint-gate (the §24 guardrails):** the variant must pass our existing checks before it can be a candidate — improve-loop's review pass, a small fixed eval set (seedable from Open-Reasoning-Tasks, Apache-2.0), size limits, and the existing skill-lint. **No auto-merge.**
4. **Human/Director approval:** winning variant → PR on a worktree branch (never main), routed to Director/CTO review, exactly like improve-loop today. Board/Director sign-off before merge.
5. **Scope to skills/prompts/playbooks only** — never weights, never tool-execution policy, never governance text without a separate governance change.

**Why this is safe & cheap:** it is `improve-loop` aimed at the agents' *own* prompts/skills instead of product code, with the same no-auto-merge gate. No clone of unlicensed code; we implement the technique ourselves. Cost is local inference time (and, if we ever want a cloud reviewer, the existing SAG-4558 hybrid review path).

**Build path (if board approves — NOT this round):** decompose to DoE → Coder as a child issue: a scoped `self-evolve` mode/variant of improve-loop targeting a single skill, with a fixed eval set and PR-only output. Canary on one local agent + one skill first (3-run protocol, SAG-144). Nothing fleet-wide without a second board go.

---

## 4. Tier B — honest feasibility + go/no-go

**What real self-evolution (weight training) needs:** atropos (RL environments to generate/score trajectories) + a trainer (DisTrO or standard) + GPU training capacity + a serving path for the new weights.

**Current hardware reality:**
- Single Framework desktop, AMD gfx1151 (Strix Halo iGPU class), shared with the board's *live* agents.
- No vLLM (blocked on ROCm 8.0, ~mid-2026); Ollama only; qwen35moe serializes to 1 slot.
- MAX_LOADED_MODELS=1 — a training job would evict the live serving model.
- No training framework, no fine-tune pipeline, no eval/rollback harness, no second box.

**Verdict: NO-GO on local RL self-evolution.** It is not an inference tweak — it is standing up a training program on a box whose day job is serving the company's agents. It requires dedicated GPU/training capacity, real spend, and ongoing maintenance (data curation, eval harness, regression gating, model promotion). That is a **board-level capital decision**, not something to bootstrap on the live box.

**Re-evaluation trigger:** revisit only after (a) ROCm 8.0 ships official gfx1151 support (tracked alongside SAG-4428), AND (b) we have dedicated, non-serving training capacity, AND (c) the board funds it. Even then, start with LoRA/adapter-level tuning on a separate box, never full-weight on the live desktop.

---

## 5. Risks & guardrails (all gated)

- **Do not clone or run `hermes-paperclip-adapter`** — full external agent + shell + multi-provider egress; intersects Photon-relay egress risk; unnecessary for the goal.
- **Do not adopt unlicensed code** (`hermes-agent-self-evolution`, `DisTrO`) — pattern-only.
- **Prefer `microsoft/agent-governance-toolkit`** over the Nous mirror; vet its license/scope separately if we want it.
- **NVIDIA/skills** — read each skill's LICENSE (NOASSERTION at repo level) before importing any single skill.
- Tier A build is §24-gated: worktree branches, no auto-merge, Director/human approval, canary-first.

---

## 6. Recommendation & next step

**Adopt Tier A** as a bounded self-eval → prompt/skill-refinement loop on top of improve-loop (technique-only, §24-gated, canary-first). **Defer Tier B** (NO-GO on current hardware; capital + ROCm-8.0 gated). **Run none of the eight repos this round.**

Gate: build of the Tier A loop is decomposed to DoE/Coder **only after CEO + board sign-off**. CTO confidence: **8/10** (−1 for unlicensed-code constraint forcing reimplementation; −1 for the Tier A loop being designed-not-yet-proven on a local agent).
