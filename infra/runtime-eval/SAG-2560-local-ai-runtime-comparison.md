# SAG-2560 — Local-AI Serving Runtime Evaluation: Ollama vs. Alternatives (incl. MLC-LLM / Jan / KoboldCpp)

**Author:** CTO (Opus 4.8) · **Date:** 2026-05-31 · **Type:** Research + recommendation (no migration)
**Board question (SAG-2558 "Ollama vs Others"):** *Is there a better-fit application than Ollama for serving local AI models on our hardware?*

> **Duplication notice (read first).** SAG-2558 and **SAG-2557** are two copies of the **same** board question, both children of **SAG-2519 "Local AI"**. SAG-2557 was already answered in full by **SAG-2559** (`infra/runtime-eval/SAG-2559-local-ai-runtime-comparison.md`, status `in_review`), whose optional-pilot go/no-go sits in board confirmation **`33f913f2`**. This document re-states that answer so SAG-2560 stands on its own **and extends it** to the three candidates SAG-2560 named that SAG-2559 only noted in passing — **MLC-LLM, Jan, KoboldCpp**. **The board should decide once** (on `33f913f2`), not twice. No second confirmation is filed from this issue.

**Verdict (one line):** **Keep Ollama** as the fleet serving runtime. Optionally scope a narrow, board-gated **llama.cpp/llama-server (Vulkan)** micro-pilot for one hot-path model to quantify perf-tuning upside. **Reject vLLM/SGLang/TGI/LM Studio/LocalAI/MLC-LLM/Jan/KoboldCpp** for the fleet-serving role today; **re-evaluate vLLM when ROCm 8.0 ships first-class gfx1151 (~mid-2026)**.

---

## 1. Our real constraints (what actually decides this)

| Constraint | Reality for Sage |
|---|---|
| **Hardware** | AMD Strix Halo / Ryzen AI Max+ 395, **gfx1151**, **128 GB unified memory**. An **APU**, not a CDNA datacenter card. |
| **ROCm status of gfx1151** | **Preview only** in ROCm's compute matrix. **Vulkan is the recommended + top-performing backend** on this box (Phoronix ROCm 7.0 Strix-Halo review). CUDA-only runtimes are dead on arrival. |
| **Workload shape** | **Heterogeneous fleet of 5+ different models on ONE box** (SAG-2553), behind **LiteLLM `:4000`**, driven by agents firing **mostly serially / low concurrency**. Not one model to thousands of concurrent users. |
| **#1 metric** | **Tool-call fidelity** — structured `tool_calls`, not narrated JSON/XML (SAG-2537). Benchable via DIRECT OpenAI-compatible `/v1/chat/completions` on `infra/bench/tool-call/`. |
| **Known pain** | Ollama **serial model-swap / co-load exhaustion** (SAG-2554): two big models can't be co-resident; cold-model swap-in latency. |
| **Gate** | **No new prod tool until SAG-683 manifest clears** (AGENT_OPERATIONS_GUIDE §8). This evaluation introduces nothing. |

**Decisive insight:** our bottleneck is *"many models available on demand on one box, low concurrency,"* **not** *"max throughput for one pinned model under heavy concurrency."* That single fact inverts the usual "vLLM beats Ollama" narrative for our environment (§4).

---

## 2. Comparison table (runtime × our axes)

Legend: ✅ strong · ⚠️ partial/conditional · ❌ blocker · n/m = not measured on our box.

| Runtime | gfx1151 / ROCm-Vulkan fit | Tool-call fidelity (structured, benchable) | Throughput / concurrency vs Ollama | Model availability (GGUF + MoE) | OpenAI-compat for LiteLLM `:4000` | Ops cost / license / health | Fit verdict |
|---|---|---|---|---|---|---|---|
| **Ollama** (incumbent) | ✅ Runs today; llama.cpp (Vulkan/ROCm) under the hood | ✅ Benched DIRECT `/api/chat`; structured `tool_calls` proven (SAG-2537/2553) | ⚠️ **Auto-swaps models on demand** (fits our many-model box); serial swap latency + co-load exhaust is the cost | ✅ Best curated GGUF library; MoE one-command | ✅ Native `/v1` + LiteLLM adapter wired | ✅ Low burden, MIT, very healthy | **KEEP** |
| **llama.cpp / llama-server** | ✅ **Proven top performer** on Strix Halo via Vulkan; tunable (hipBLASLt, flash-attn, ROCWMMA) | ✅ Same engine family as Ollama; `/v1` tool-call parsing — benchable on our harness | ✅ Higher tok/s with tuning, but **bandwidth-bound → modest uplift**; no built-in multi-model auto-swap | ✅ Any GGUF incl. MoE (it *is* the loader) | ✅ `llama-server` exposes `/v1/chat/completions` | ✅ MIT, extremely healthy, upstream of Ollama | **PILOT (narrow, optional)** |
| **vLLM** | ❌ gfx1151 not in upstream supported list; needs TheRock-nightly ROCm source build + `amdsmi` mock. First-class gfx1151 ≈ **ROCm 8.0 mid-2026** | ⚠️ Strong `--tool-call-parser` *when it runs*; unbenched here | ✅ Best-in-class for **one pinned model under heavy concurrency** — a workload we don't have (§4) | ⚠️ HF/safetensors-first; GGUF secondary; MoE ok | ✅ First-class OpenAI server | ❌ High build/maint burden on our APU today; Apache-2.0; fragile on gfx1151 | **DEFER → re-eval @ ROCm 8.0** |
| **SGLang** | ❌ AMD support = **MI300X (CDNA) only**; no consumer-APU path (roadmap #23494, Q2 2026) | ⚠️ Good parsers when it runs; n/m | ✅ Peer to vLLM on datacenter GPUs | ⚠️ safetensors-first | ✅ OpenAI server | ❌ Won't run on gfx1151 today | **REJECT (hardware)** |
| **TGI (HF)** | ⚠️ ROCm exists but CUDA-first | n/m | ✅ Datacenter-class | ⚠️ safetensors-first | ✅ | ❌ **Maintenance mode since Dec 2025** — HF redirects to vLLM/SGLang | **REJECT (dying)** |
| **LM Studio** | ✅ Runs on Strix Halo via Vulkan; headless server mode | ✅ OpenAI `/v1` server, benchable | ⚠️ llama.cpp-based; perf ≈ llama.cpp; single-model/GUI-centric | ✅ GGUF + MoE via catalog | ✅ OpenAI-compatible server | ❌ **Closed-source / proprietary EULA** — fails open-source preference + SAG-683 §1 vetting for a server-tier tool | **REJECT (license/posture)** |
| **LocalAI** | ✅ Can drive llama.cpp Vulkan backend | ✅ OpenAI `/v1`, benchable | ⚠️ Wrapper over same engines; no perf edge; adds a layer atop what LiteLLM already gives us | ✅ Multi-backend | ✅ OpenAI drop-in | ⚠️ MIT/healthy, but **redundant** w/ LiteLLM + Ollama | **REJECT (redundant)** |
| **MLC-LLM** | ⚠️ Vulkan via TVM works on Strix Halo, but **each model needs an ahead-of-time TVM compile** per quant/target | ⚠️ `/v1` MLC-Serve exists; tool-call parsing immature, n/m | ⚠️ Can be fast once compiled, but compile step kills the "drop in a new GGUF" agility we rely on | ❌ **Not GGUF** — its own MLC weight format; our whole library is GGUF | ⚠️ MLC-Serve OpenAI-ish, partial | ⚠️ Apache-2.0, healthy, but **per-model compile = high op burden** for a 5+ model rotating fleet | **REJECT (format + compile burden)** |
| **Jan** | ✅ Bundles llama.cpp (Vulkan) | ✅ Inherits llama.cpp `/v1` tool-call | ⚠️ Perf ≈ llama.cpp; **desktop-GUI product**, server is secondary | ✅ GGUF | ⚠️ Local API server exists but GUI-first | ⚠️ AGPL-3.0 app shell; **desktop UX, not a headless fleet server** — no edge over raw llama.cpp | **REJECT (GUI-first, no edge)** |
| **KoboldCpp** | ✅ llama.cpp + Vulkan, single-binary | ⚠️ OpenAI-compat endpoint exists; **tuned for creative-writing/roleplay**, tool-call is not its focus, n/m | ⚠️ Perf ≈ llama.cpp | ✅ GGUF | ⚠️ Partial OpenAI-compat | ⚠️ AGPL-3.0; niche (storytelling) — wrong domain for an agent tool-router | **REJECT (wrong domain)** |
| **GPT4All** *(for completeness)* | ✅ Vulkan desktop | ⚠️ Limited tool-call; n/m | ⚠️ llama.cpp-class | ✅ GGUF | ⚠️ Local server limited | ⚠️ MIT app; **desktop GUI** — no headless-fleet edge | **REJECT (GUI, no edge)** |

**Pattern across the bottom seven:** every "alternative" is either (a) the **same llama.cpp engine** wrapped in a desktop GUI (LM Studio, Jan, KoboldCpp, GPT4All) — so no perf or fidelity edge over running llama.cpp directly, while adding license/GUI baggage; (b) **wrong hardware** (SGLang, vLLM-today); (c) **dying/redundant** (TGI, LocalAI); or (d) **wrong model format** (MLC-LLM). None beats "Ollama for the fleet + an optional bare llama.cpp pilot for one hot path."

---

## 3. Tool-call fidelity is model+template-bound, NOT runtime-bound

SAG-2553 bench: `qwen3-coder:30b` fails 2/50 multi-tool prompts by emitting `<function=get_weather><parameter=location>…` — an **XML-style template leak**, the *model's chat template*, not Ollama's serving layer. Swapping to llama.cpp / vLLM / LocalAI / MLC won't fix it (same model output; vLLM would need a matching `--tool-call-parser` that can mis-parse just as easily). **A runtime swap is not a lever on our #1 metric.** The lever is **model/template selection** (SAG-2553: qwen3:30b-a3b @ 100%/100%, qwen3:32b @ 100%/100% are the clean picks).

---

## 4. Why "vLLM is faster" doesn't apply to us

vLLM/SGLang/TGI win by **pinning one model in VRAM** and serving **many concurrent requests** (continuous batching, paged-attention) — a *serving-farm* workload. Ours is the opposite: **one box, 5+ models, low concurrency**. To get vLLM's throughput we'd pin one model and lose the others; **128 GB unified memory can't hold 5 large models at once**, so we'd be swapping again — except vLLM **doesn't auto-swap** (you'd hand-orchestrate N processes + memory reservation). **Ollama's on-demand auto-swap is the feature that fits a heterogeneous many-model box.** SAG-2554 co-load pain is the *cost* of that feature, not evidence we need a serving farm.

Throughput reference points:
- Our Ollama bench (SAG-2554): `qwen3-coder:30b` MoE **61.6 tok/s** vs `llama3.3:70b` **3.5 tok/s** — model choice dominates (~17.6× spread).
- Community Strix-Halo data: 70B dense ≈ **5 tok/s** (bandwidth-bound ~215 GB/s); Qwen MoE **63–98 t/s**. → **The win is the model, not the runtime.**

---

## 5. The one defensible pilot (optional, board-gated — already on SAG-2559 `33f913f2`)

**llama.cpp / llama-server (Vulkan) for a single hot-path model** (e.g. Tier-0 coder or digester runner): accept losing auto-swap for tuned max throughput (hipBLASLt + flash-attn + ROCWMMA). Upside likely modest (bandwidth-bound) but worth *quantifying* — same engine Ollama wraps, so risk is low and it's already implicitly vetted.

**Scope if approved (same discipline as SAG-2554):** build `llama-server` Vulkan in an isolated window → serve qwen3:30b-a3b on `/v1/chat/completions` → run existing `infra/bench/tool-call/` harness DIRECT (N=50, 9-tool array + 1-tool control) → compare tok/s + tool-call fidelity vs same model on Ollama → adopt for hot-path only if uplift clears a worth-it threshold; else close "Ollama confirmed." **Not in scope: no install, no prod swap, no manifest change.**

> This pilot is **already gated on the SAG-2559 board confirmation `33f913f2`** — do not file a second one from SAG-2560.

---

## 6. Watchpoint / re-evaluation triggers

- **ROCm 8.0 first-class gfx1151 (~mid-2026)** → re-open **vLLM** (installable without TheRock hacks; economics also flip *if* our workload ever shifts to high-concurrency single-model serving).
- **SGLang AMD roadmap (#23494, Q2 2026)** → re-check consumer-APU support.

---

## 7. Recommendation to board

1. **Keep Ollama** as the fleet serving runtime — best fit for our heterogeneous, low-concurrency, many-model, gfx1151-Vulkan box. **No migration.**
2. **Reject** vLLM (today), SGLang, TGI, LM Studio, LocalAI, **MLC-LLM, Jan, KoboldCpp, GPT4All** for the fleet-serving role, per the table.
3. **Optional:** the narrow llama.cpp Vulkan micro-pilot (§5) — **decide once on SAG-2559's `33f913f2`**; SAG-2558/SAG-2560 is the duplicate thread of the same question (both under SAG-2519).
4. **Tool-call fidelity stays a model/template question** (SAG-2553), independent of runtime.
5. **Process note:** SAG-2557+SAG-2559 and SAG-2558+SAG-2560 are duplicate forks under SAG-2519 — recommend the board close one pair to avoid double-spend on future re-evals.

*Sources: Phoronix ROCm 7.0 Strix-Halo review; kyuz0 amd-strix-halo backend benchmarks; Strix-Halo deployment guides; HF TGI maintenance-mode notice (Dec 2025); sgl-project/sglang #23494; AMD TheRock ROCm 8.0 gfx1151 target; MLC-LLM/Jan/KoboldCpp project docs. Internal: SAG-2537, SAG-2553, SAG-2554 bench data; SAG-2559 deliverable.*
