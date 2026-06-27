# SAG-4802 — Hermes Self-Improvement for Local Agents: Feasibility + Tier-3 Vetting

**Author:** CTO (Opus 4.8) · **Date:** 2026-06-22 · **Status:** DESIGN/RESEARCH ONLY — gated on CEO review → board sign-off. **No clone, no run, no enable, no train.**
**Parent ask:** SAG-4787 (board) → routed to CTO via SAG-4802 (CEO).
**Confidence:** 8/10.

---

## 0. TL;DR / Verdict

- **Tier A (in-context / skill / prompt / memory self-improvement — NO weight changes): ADOPTABLE NOW, as a pattern built on our own primitives.** We already run improve-loop (catalog `bf63415c`), para-memory-files, and GOVERNANCE §24. The near-term win is to extend that bounded, gated loop from *product code* to *the agents' own prompts/skills/playbooks*. Recommend a **single canary spike** on one non-critical skill, fully gated (no auto-merge, Director/human approval). Do **not** clone `hermes-agent-self-evolution` into prod yet — see §3.
- **Tier B (weight-level RL self-evolution of local Qwen — `atropos` + `DisTrO`): REVISED 2026-06-23 → from NO-GO to FEASIBILITY-SPIKE (gated). See §4 ERRATA.** Board correction: the box already runs **8-way concurrent Qwen 3.6** on **ROCm 6.4** via Ollama (`OLLAMA_NUM_PARALLEL=8`), with **122 GB unified memory** — my original "inference-only / `qwen35moe` serializes / blocked on ROCm 8.0" premise was **wrong**. Concurrency and ROCm inference work *today*. The one genuinely-open question is narrower: whether **training (backprop) kernels** run on gfx1151 under ROCm 6.4 (PyTorch-ROCm) — an empirical unknown a bounded, idle-gated spike can settle. RL fine-tuning is still a real program (spend + MLOps + must not disrupt the live box), but it is **more feasible than I assessed** and warrants a gated feasibility spike, not a flat no-go.

> **~~Superseded original verdict:~~** ~~NO-GO; inference-only; qwen35moe serializes; blocked on ROCm 8.0.~~ Retained for audit; corrected by board first-hand evidence — see §4.
- **Provenance: all 8 repos are real and under genuine orgs — no typosquats, no fabrications.** One CEO premise is corrected below.

### ⚠️ Premise correction (must surface to board)
The ask flagged `NousResearch/hermes-paperclip-adapter` as implausible because it "claims to target our internal control plane." **That premise is false.** "Paperclip" is a **public** agent-orchestration product (`github.com/paperclipai/paperclip`, paperclip.ing) — the same platform our company runs on as a tenant. A public adapter for it is *expected*, not a supply-chain red flag. It is genuinely NousResearch, MIT-licensed. (It is still young — 14 commits — and spawns Hermes with full terminal/file/code-exec tools, so version-pin and sandbox before any adoption.)

### Security note
Vetting was done read-only via the authoritative GitHub REST API (`api.github.com`, genuine `x-github-request-id` headers) — not just AI-summarized scrapes, which can hallucinate content for 404s. During scraping of DisTrO content a **prompt-injection attempt** (a fake "available skills" block) was detected and ignored. No repo was cloned or executed.

---

## 1. Tier-3 Supply-Chain Vetting (§4.3) — per repo

Org identity confirmed authoritative: real NousResearch = `owner_id 134168893` (login `NousResearch`, created 2023-05-20, nousresearch.com). All seven NousResearch repos resolve to that exact `owner_id`. NVIDIA = `owner_id 1728152` (genuine). **No look-alikes / forks-masquerading / typosquats.**

| # | Repo | Exists? | Real owner | License | What it does | Activity | Tier | Risk |
|---|------|---------|-----------|---------|--------------|----------|------|------|
| 1 | hermes-agent-self-evolution | Yes | NousResearch (genuine) | **none** | Self-improvement of Hermes agents via DSPy + GEPA prompt/skill evolution (no GPU training) | pushed 2026-06-17, ~4.3k★, 8 commits, created 2026-03-09 | **A** | **MED** |
| 2 | hermes-paperclip-adapter | Yes | NousResearch (genuine) | MIT | Runs Hermes as a managed "employee" inside a (public) Paperclip company | pushed 2026-04-04, ~1.6k★, 14 commits | **A** | **MED** |
| 3 | Hermes-Function-Calling | Yes | NousResearch (genuine) | MIT | Reference code for Hermes function/tool-calling vs schemas | ~1.4k★, 111 commits, active | **A** | LOW |
| 4 | atropos | Yes | NousResearch (genuine) | MIT | RL Environments framework (collect/eval LLM trajectories) | pushed 2026-06-22, ~1.3k★, 1,606 commits, v0.4.0 | **B** | LOW |
| 5 | DisTrO | Yes | NousResearch (genuine) | **none** | Distributed-Training-Over-The-Internet optimizers (DeMo) | pushed 2025-10-14, ~1.0k★, 15 commits | **B** | LOW–MED |
| 6 | Open-Reasoning-Tasks | Yes | NousResearch (genuine) | Apache-2.0 | Repository of reasoning tasks (data/prompts) | ~494★, 142 commits, active | **A** | LOW |
| 7 | agent-governance-toolkit | Yes (**FORK** of `microsoft/`) | NousResearch fork | MIT | Policy enforcement / zero-trust identity / sandboxing for agents | created+pushed 2026-05-10/11 (1-day window), 32★ | **A** | **MED** |
| 8 | NVIDIA/skills | Yes | NVIDIA (genuine) | Apache-2.0 / CC-BY-4.0 | NVIDIA-verified agent "skills" (CUDA-X, Blueprints, platform tools) | ~1.7k★, 347 commits, daily sync | **A** | LOW |

**Per-repo caveats that matter for adoption:**
- **#1 hermes-agent-self-evolution** — on-target for our goal, but **no LICENSE** (legal/adoption blocker), very young, high stars vs tiny commit count, and "self-evolution" auto-rewrites agent prompts/code → must be sandboxed. **Take the *pattern* (DSPy/GEPA prompt optimization), not the code, for now.**
- **#5 DisTrO** — no license; research-grade drop, not a maintained product.
- **#7 agent-governance-toolkit** — **stale fork**; if we ever want this, use the canonical `microsoft/agent-governance-toolkit` upstream, not the NousResearch copy.

---

## 2. Tier Mapping

- **Tier A (inference / prompt / skill / scaffolding — adoptable now):** hermes-agent-self-evolution, hermes-paperclip-adapter, Hermes-Function-Calling, Open-Reasoning-Tasks, agent-governance-toolkit (use upstream), NVIDIA/skills.
- **Tier B (weight-level training infra — capital decision, not local):** atropos, DisTrO.

---

## 3. Tier A — Near-term adoption proposal (the recommended win)

**Principle:** We do **not** need to clone a third-party "self-evolution" repo to get Tier-A self-improvement. We already own every primitive. The proposal is to extend our existing bounded, gated loop from *product code* to *the agent's own prompts / skills / playbooks*, informed (conceptually) by the DSPy/GEPA optimization pattern in repo #1.

**Existing primitives we layer on:**
- `improve-loop` (catalog `bf63415c`) — bounded plan→code→review loop, runs in **isolated git worktrees, never on main, no auto-merge, Director/human approval before merge**. Today scoped to code quality.
- `para-memory-files` — durable cross-conversation memory (the eval/feedback substrate).
- GOVERNANCE §24 (HARD LIMIT) — all code goes through a bounded loop before finalized; local-AI default (plan/code local, review cloud).

**Proposed "Skill/Prompt Improvement Loop" (bounded + gated):**
1. **Collect** — assemble a small held-out eval set of past outputs for ONE target skill/role (from issue history + para-memory). *Exclude infra-error rows* (lesson SAG-4340 — timeouts/`wall_s:0` poison the denominator).
2. **Critique** — a reviewer agent (cloud, per §24 review tier) scores outputs against a fixed rubric.
3. **Propose** — generate a candidate refinement to the `SKILL.md` / prompt / playbook **in an isolated worktree** — never live, never touching governance/security files.
4. **A/B eval** — run candidate vs current on the held-out set; require a measurable, non-regressing improvement.
5. **Gate** — **Director/human approval before merge** (mirrors §24 / improve-loop's no-auto-merge invariant). Refined skill reaches agents **only** via the approved catalog import (`POST /skills/import`) — *a skill is not "delivered" until it is in the catalog* (lesson SAG-4527), and is trivially version-rolled-back (skills src committed, SAG-4567).

**Guardrails (all reuse existing §24 invariants):** isolated worktrees only; no auto-merge; governance/security/identity files are out of scope for any auto-proposed refinement; canary-first (3-run protocol SAG-144 style) before any fleet-wide rollout; cloud review on every candidate.

**Decomposition (gated — created only after CEO approval):** one spike issue to **Coder (local-first)** to prototype this loop on a single non-critical skill as a canary, supervised by DoE, with the doc's guardrails as acceptance criteria. No fleet rollout in the spike.

**On repo #1 specifically:** recommend we evaluate `hermes-agent-self-evolution`'s DSPy/GEPA approach as *design inspiration* only. **Do not clone-to-prod** until (a) it has a license, and (b) it is sandbox-vetted (it auto-rewrites prompts/code). Our own primitives already deliver the gated loop without taking on that dependency.

---

## 4. Tier B — Feasibility (weight-level RL self-evolution)

### ⚠️ ERRATA 2026-06-23 — board corrected my hardware premise (verified first-hand on the box)
My original §4 rested on three claims that are **WRONG**, corrected by board evidence + direct inspection:
| Original claim | Reality (verified `ollama ps` / `systemctl show ollama` / `rocm-smi` / `free`) |
|---|---|
| "inference-only; `qwen35moe` serializes to 1 slot" | `OLLAMA_NUM_PARALLEL=8` is configured; board runs **8 concurrent Qwen 3.6** today. Concurrency works. |
| "no vLLM → blocked on ROCm 8.0 (~mid-2026)" | **ROCm 6.4.0** is installed and drives gfx1151 at 100% GPU **now**. Inference is not ROCm-blocked; vLLM is not required for the concurrency. |
| "multiples of inference memory unavailable" | Strix Halo APU with **122 GB unified memory (~74 GB free)** — ample for LoRA/QLoRA-scale training and rollout batching. |

**What it actually is:** `atropos` (RL environments / rollout generation) + a trainer doing the weight updates. `DisTrO` (distributed optimizer) is for multi-node and is **not needed** for a single box. RL self-evolution = generate trajectories (inference — *helped directly by the 8-way concurrency*) → score against a reward → apply a gradient/LoRA update → repeat.

**What is now plausible (was understated):**
- **Rollout/eval generation** is exactly parallel inference — the corrected 8-way concurrency makes this side cheap and local.
- **LoRA / QLoRA fine-tuning** trains small adapter weights with the base model frozen; 122 GB unified memory is comfortably within budget for Qwen-scale adapters.

**The one genuinely-open question (empirical, not assertable):** do **training/backprop kernels** run on **gfx1151 under ROCm 6.4 + PyTorch-ROCm**? gfx1151 (RDNA 3.5 APU) training-path maturity (bf16 backward, attention backward, optimizer kernels) is unproven in our stack. This is the single thing a spike must settle — and it is a *test*, not a guess.

**Operational constraint that still stands:** the box **serves the board's live agents**. A sustained training run pins the GPU for hours and *would* contend with live inference → any Tier-B work must be **idle-gated / off-hours** (same discipline as the SAG-4434 bench) and must **never** swap production model weights without sign-off.

**Go/No-Go → REVISED: run a gated FEASIBILITY SPIKE (replaces flat NO-GO).** See §5.3 for the spike plan. Full production RL self-evolution remains a spend + MLOps commitment and a board capital decision, but the feasibility question is now worth answering empirically rather than dismissing.

---

## 5. Recommendation summary

1. **Tier A — GO (gated):** approve a single canary spike to build the Skill/Prompt Improvement Loop on our own primitives (improve-loop + para-memory + §24 guardrails). Director-supervised, no auto-merge, no fleet rollout in the spike.
2. **Repo #1 — pattern only:** use `hermes-agent-self-evolution` (DSPy/GEPA) as design inspiration; **no clone-to-prod** until licensed + sandboxed.
3. **Tier B — REVISED to gated FEASIBILITY SPIKE** (board directive 2026-06-23). Original NO-GO is superseded; my ROCm/concurrency premise was wrong (§4 ERRATA). The spike below answers the one open question (training-path on gfx1151/ROCm 6.4) without committing to a production program.
4. **Provenance:** all 8 repos genuine; correct the board on the (false) "internal Paperclip" premise; if `agent-governance-toolkit` is ever wanted, use the `microsoft/` upstream, not the fork.
5. **GATE:** nothing cloned-to-prod, run, enabled, or trained without CTO + board sign-off.

### 5.3 Tier-B feasibility spike (proposed, gated, idle-only)
Bounded, non-disruptive, no production weight changes — purely answers "can this box train?":
1. **Isolated PyTorch-ROCm env** (venv/container, no system changes) → verify `torch` sees gfx1151 and a **tiny backward pass** executes (HIP training kernels work at all).
2. **Minimal LoRA/QLoRA fine-tune** of a small Qwen on a toy dataset, a few hundred steps → measure memory, throughput, numerical stability. Validates the training path end-to-end.
3. **(Only if 1–2 pass) Minimal `atropos` rollout→reward→LoRA-update loop** on ONE task; adapter weights quarantined, **never merged** into any served model.
- **Guardrails:** runs **only when the box is idle** (idle-gate, off-hours); bounded wall-clock; no DisTrO (single box); all artifacts quarantined; HARD STOP + feasibility report (go/no-go + cost/MLOps estimate for a real program) before any production enablement.
- **Decompose:** one bounded child issue to a Coder under DoE; CTO reviews the report; production Tier-B program = separate board capital decision.
