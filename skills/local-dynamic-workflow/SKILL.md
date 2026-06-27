---
name: local-dynamic-workflow
description: >
  Run a local "dynamic workflow" on the Sage Surfaces Strix Halo box — fan a
  batch of N prompts/tasks out across ONE local Ollama model in parallel, with
  the worker count (2-4) auto-sized to the box's currently available room
  (free memory + load average + what's already loaded). Use whenever you want a
  bounded, on-box, zero-cloud-token parallel sweep over local models: tool-call
  benchmarks, classification/triage over many items, model A/B comparisons, or
  any "run this prompt over a list" job. Built from SAG-3075. Pairs with the
  Ollama service governor (OLLAMA_MAX_LOADED_MODELS=1) which already serializes
  + queues different models so the box never overcrowds.
---

# Local Dynamic Workflow

A **local-native dynamic workflow**: a deterministic orchestrator fans out
parallel HTTP calls to local Ollama at an auto-sized width. No cloud tokens, no
Workflow-tool subagents — all work is local inference.

## When to use
- You have a **list of tasks** (prompts) to run through a **local** model and want them done in parallel.
- Examples: tool-call benchmark, triage/classify many tickets, A/B two local models, sweep a prompt over many inputs.
- Do **not** use this for cloud-Claude orchestration (that's the `Workflow` tool) or for work that needs Claude-level reasoning per task.

## The two hard rules (measured in SAG-3075)
1. **Fan out ONE model at a time.** Same-model requests batch on the single iGPU at ~full speed up to ~4 workers. Different models do **not** co-reside — the service runs `OLLAMA_MAX_LOADED_MODELS=1`, so Ollama swaps + queues them. To compare models, run one pass per model (sequentially).
2. **Let the room decide the width.** 4 workers only when the box is quiet; drop to 2 (or 1) when enrichment / the Digester / Ticket-Health are loading it. `room_check.py` does this for you.

## Usage

Check available room (what width would deploy right now):
```
python3 skills/local-dynamic-workflow/scripts/room_check.py --model qwen3:30b-a3b --json
```
Returns `recommended_concurrency` (1-4) from free memory, load-per-core, and resident model.

Run a fan-out (auto-sized width):
```
python3 skills/local-dynamic-workflow/scripts/fanout.py --tasks tasks.json --model qwen3:30b-a3b --out results.json
```
`tasks.json` is either a list of prompt strings, or:
```json
{ "model": "qwen3:30b-a3b",
  "system": "optional system prompt",
  "tools": [ /* optional Ollama tool schema -> enables tool-calling */ ],
  "tasks": [ {"id": "t1", "prompt": "..."}, {"id": "t2", "prompt": "..."} ] }
```
Pin the width with `--concurrency N` (or `BENCH_CONCURRENCY=N`) to override room_check.

Compare two models (sequential — never concurrent):
```
for m in qwen3:30b-a3b gemma4:26b-a4b-it-q4_K_M; do
  python3 .../fanout.py --tasks tasks.json --model "$m" --out "results-$m.json"
done
```

## How room_check sizes width
Starts at the measured ceiling (4) and backs off by load-per-core:
`<0.25 →4`, `<0.45 →3`, `<0.70 →2`, else `1`. If the target model isn't resident
and free memory is tight, it forces serial and lets Ollama's queue absorb the swap.

## Operational notes
- Long runs: launch detached (`setsid`) — writes results to a file, survives session recycling.
- The Ollama request queue (`OLLAMA_MAX_QUEUE`, default 512) is the safety net: excess concurrent calls queue rather than fail.
- Per-stream speed ≈ 53-57 tok/s on the 30B-class models; absolute wall-time is nondeterministic under shared load — size by room, or schedule off-peak.
- Reference implementation / origin: `infra/bench/tool-call/run_dynamic.py` (the tool-call bench this generalizes).
