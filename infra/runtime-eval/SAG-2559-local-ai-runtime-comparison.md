# SAG-2559 — Local-AI Serving Runtime Evaluation: Ollama vs. Alternatives

**Author:** CTO (Opus 4.8) · **Date:** 2026-05-31 · **Type:** Research + recommendation (no migration)
**Board question (SAG-2557):** *Is there another application other than Ollama that gives us access to local AI models that may be as good or a better fit?*

**Verdict (one line):** **Keep Ollama** as the fleet serving runtime. Optionally scope a narrow, board-gated **llama.cpp/llama-server (Vulkan)** micro-pilot for one hot-path model to quantify perf-tuning upside. **Reject vLLM/SGLang/TGI for now** (gfx1151 not production-ready / wrong workload fit / dying project); **re-evaluate vLLM when ROCm 8.0 ships first-class gfx1151 (~mid-2026)**.

---

## 1. Our real constraints (what actually decides this)

| Constraint | Reality for Sage |
|---|---|
| **Hardware** | AMD Strix Halo / Ryzen AI Max+ 395, **gfx1151**, **128 GB unified memory**. This is an **APU**, not a CDNA datacenter card. |
| **ROCm status of gfx1151** | **Preview only** in ROCm's compute matrix, scoped to PyTorch on Linux. **Vulkan is the recommended + top-performing backend** on this box (confirmed by Phoronix ROCm 7.0 review and multiple Strix-Halo guides). CUDA-only runtimes are dead on arrival. |
| **Workload shape** | **Heterogeneous fleet of 5+ different models on ONE box** (SAG-2553), served behind **LiteLLM `:4000`**, driven by agents that fire **mostly serially / low concurrency**. We are *not* serving one model to thousands of concurrent users. |
| **#1 metric** | **Tool-call fidelity** — structured `tool_calls`, not narrated JSON / XML (SAG-2537). Benchable via DIRECT OpenAI-compatible `/v1/chat/completions` on `infra/bench/tool-call/`. |
| **Known pain** | Ollama **serial model-swap / co-load exhaustion** (SAG-2554): two big models can't be co-resident; swap-in latency on cold model. |
| **Gate** | **No new prod tool until SAG-683 manifest clears** (AGENT_OPERATIONS_GUIDE §8). This evaluation introduces nothing. |

**The decisive insight:** our bottleneck is *"many models available on demand on one box, low concurrency,"* **not** *"max throughput for one pinned model under heavy concurrency."* That single fact inverts the usual "vLLM beats Ollama" narrative for our environment — see §4.

---

## 2. Comparison table (runtime × our axes)

Legend: ✅ strong · ⚠️ partial/conditional · ❌ blocker · n/m = not measured on our box.

| Runtime | gfx1151 / ROCm-Vulkan on our box | Tool-call fidelity (benchable, structured) | Throughput / concurrency vs Ollama | Model availability (GGUF + MoE) | OpenAI-compat for LiteLLM `:4000` | Ops cost / license / health | Fit verdict |
|---|---|---|---|---|---|---|---|
| **Ollama** (incumbent) | ✅ Runs today; uses llama.cpp (Vulkan/ROCm) under the hood | ✅ Already benched DIRECT `/api/chat`; structured `tool_calls` proven (SAG-2537/2553) | ⚠️ Auto-swaps models on demand (**fits our many-model box**); serial swap latency + co-load exhaust is the cost | ✅ Best curated GGUF library; MoE (qwen3-coder:30b, qwen3:30b-a3b) one-command | ✅ Native `/v1` + LiteLLM adapter already wired | ✅ Low burden, MIT, very healthy | **KEEP** |
| **llama.cpp / llama-server** | ✅ **Proven top performer** on Strix Halo via **Vulkan (AMDVLK)**; tunable (hipBLASLt, flash-attn, ROCWMMA) | ✅ Same engine family as Ollama; OpenAI `/v1` server with tool-call parsing — **benchable on our harness** | ✅ Higher tok/s with tuning flags; **but bandwidth-bound** so uplift is modest, not transformational. No built-in multi-model auto-swap | ✅ Any GGUF incl. MoE (it *is* the loader) | ✅ `llama-server` exposes `/v1/chat/completions` | ✅ MIT, extremely healthy, upstream of Ollama | **PILOT (narrow, optional)** |
| **vLLM** | ❌ gfx1151 **not in upstream supported list**; requires **source build vs TheRock nightly ROCm + `amdsmi` mock patch**. First-class gfx1151 expected **ROCm 8.0 ~mid-2026** | ⚠️ Strong `--tool-call-parser` support *when it runs*; unbenched on our box | ✅ Best-in-class **for one pinned model under heavy concurrency** — a workload we don't have (§4) | ⚠️ HF/safetensors-first; GGUF support exists but secondary; MoE supported | ✅ First-class OpenAI server | ❌ High build/maintenance burden on our APU today; Apache-2.0; healthy project but fragile on gfx1151 | **DEFER → re-eval @ ROCm 8.0** |
| **SGLang** | ❌ AMD support = **MI300X (CDNA) only**; **no consumer APU** path (AMD roadmap issue open for Q2 2026) | ⚠️ Good parsers when it runs; n/m | ✅ Peer to vLLM on datacenter GPUs | ⚠️ safetensors-first | ✅ OpenAI server | ❌ Won't run on gfx1151 today | **REJECT (hardware)** |
| **TGI (HF)** | ⚠️ ROCm exists but CUDA-first | n/m | ✅ Datacenter-class | ⚠️ safetensors-first | ✅ | ❌ **Maintenance mode since Dec 2025** — HF redirects new deploys to vLLM/SGLang | **REJECT (dying)** |
| **LM Studio** | ✅ Runs on Strix Halo via **Vulkan**; headless server mode exists | ✅ OpenAI `/v1` server, benchable | ⚠️ llama.cpp-based; perf ≈ llama.cpp; single-model focus, GUI-centric | ✅ GGUF + MoE via its catalog | ✅ OpenAI-compatible server | ⚠️ **Closed-source / proprietary EULA** — fails our open-source-preference + SAG-683 vetting posture for a server-tier tool | **REJECT (license/posture)** |
| **LocalAI** | ✅ Can drive llama.cpp Vulkan backend on our box | ✅ OpenAI `/v1`, benchable | ⚠️ Wrapper over same engines; no perf edge; adds an abstraction layer atop what LiteLLM already gives us | ✅ Multi-backend | ✅ OpenAI drop-in | ⚠️ MIT/healthy, but **redundant** — duplicates LiteLLM's gateway role + Ollama's model mgmt | **REJECT (redundant)** |
| **MLC-LLM / KoboldCpp / Jan / GPT4All** | Vulkan-capable variously | n/m | No edge for headless server + tool-call | mixed | partial | MLC needs per-model compile; Kobold = creative-writing; Jan/GPT4All = desktop GUI | **NOTE-ONLY (no edge)** |

---

## 3. Tool-call fidelity is model+template-bound, NOT runtime-bound

Our SAG-2553 bench shows `qwen3-coder:30b` failing 2/50 multi-tool prompts by emitting:

```
<function=get_weather><parameter=location>Moscow</parameter>...
```

— an **XML-style template leak**, not structured `tool_calls`. This is the *model's chat template*, not Ollama's serving layer. **Moving to llama.cpp / vLLM / LocalAI will not fix it** (they parse the same model output; vLLM would need a matching `--tool-call-parser` and could just as easily mis-parse). Corollary: a runtime swap is **not** a lever on our #1 metric. The lever is **model/template selection** (already owned by SAG-2553: qwen3:30b-a3b @ 100%/100%, qwen3:32b @ 100%/100% are the clean picks).

---

## 4. Why the "vLLM is faster" narrative doesn't apply to us

vLLM/SGLang/TGI win by **pinning one model in VRAM** and serving it to **many concurrent requests** (continuous batching, paged-attention). That is a *serving-farm* workload.

Ours is the opposite: **one box, 5+ different models, low concurrency**. To get vLLM's throughput we'd pin one model and lose the others — and **128 GB unified memory cannot hold 5 large models simultaneously**, so we'd be back to swapping, except vLLM **doesn't auto-swap** (you'd orchestrate N processes + memory reservation by hand). **Ollama's on-demand model auto-swap is the feature that fits a heterogeneous many-model box.** The SAG-2554 co-load pain is the *cost* of that feature, not evidence we need a serving farm. Net: vLLM's headline advantage is **largely irrelevant** to our actual traffic, and its single-model pinning is a **worse** fit for the fleet today.

Throughput reference points (measured/observed):
- Our Ollama bench (SAG-2554): `qwen3-coder:30b` MoE **61.6 tok/s**, `llama3.3:70b` **3.5 tok/s** — the MoE/model choice dominates, ~17.6× spread.
- Community Strix-Halo data: 70B dense ≈ **5 tok/s** (bandwidth-bound, ~215 GB/s); Qwen MoE **63–98 t/s**. Consistent with ours → **the win is the model, not the runtime.**

---

## 5. The one defensible pilot (optional, board-gated)

**llama.cpp / llama-server (Vulkan) for a single hot-path model** — e.g. the Tier-0 coder or the digester runner — where we'd accept losing auto-swap in exchange for tuned max throughput (hipBLASLt + flash-attn + ROCWMMA). Expected upside: modest (bandwidth-bound), but worth *quantifying* since it's the same engine Ollama wraps, so risk is low and it's already implicitly "vetted."

**Scope if approved (separate child, same discipline as SAG-2554):**
1. Build `llama-server` with Vulkan + tuning flags on the box (isolated window).
2. Serve one model (qwen3:30b-a3b) on `/v1/chat/completions`.
3. Run the **existing `infra/bench/tool-call/` harness** DIRECT against it — N=50, 9-tool array + 1-tool control.
4. Compare tok/s + tool-call fidelity head-to-head vs the same model on Ollama.
5. Verdict: adopt for hot-path only if tok/s uplift ≥ a threshold worth the lost auto-swap convenience; otherwise close as "Ollama confirmed."

**Not in scope here** (per issue + SAG-683 §8): no install, no prod swap, no manifest change.

---

## 6. Watchpoint / re-evaluation trigger

- **ROCm 8.0 first-class gfx1151 (~mid-2026)** → re-open **vLLM** evaluation. At that point vLLM becomes installable without TheRock-nightly hacks, *and* if our workload ever shifts to high-concurrency single-model serving (e.g., a high-volume batch pipeline), vLLM's economics flip. Until then it fails the §8 manifest gate and the gfx1151 production filter.
- **SGLang AMD roadmap (Q2 2026 issue #23494)** → re-check consumer-APU support then.

---

## 7. Recommendation to board

1. **Keep Ollama** as the fleet serving runtime — it is the best fit for our heterogeneous, low-concurrency, many-model, gfx1151-Vulkan box. No migration.
2. **Reject** vLLM (today), SGLang, TGI, LM Studio, LocalAI for the reasons tabled above.
3. **Optional:** authorize a narrow llama.cpp/llama-server Vulkan micro-pilot (§5) to quantify hot-path tuning upside — go/no-go is the board's via the `request_confirmation` on this issue.
4. **Tool-call fidelity remains a model/template question** (SAG-2553), independent of runtime.

*Sources: Phoronix ROCm 7.0 Strix Halo review; kyuz0 amd-strix-halo vLLM/backend benchmarks; hyperion-consulting & smarttechlabs Strix-Halo guides; HF TGI maintenance-mode notice (Dec 2025); sgl-project/sglang #23494 AMD roadmap; AMD TheRock ROCm 8.0 gfx1151 target. Internal: SAG-2537, SAG-2553, SAG-2554 bench data (`infra/bench/tool-call/results-sag2553.json`).*
