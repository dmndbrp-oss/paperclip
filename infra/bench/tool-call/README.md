# Uniform Tool-Call Re-Bench (SAG-2537)

Removes the asterisk from the SAG-2521/SAG-2522 results: identical N, fixed multi-tool array, one committed run.

## How to run

```
python3 infra/bench/tool-call/run.py
```

Output is written to `infra/bench/tool-call/results.json`.

## Constraints

- **DIRECT Ollama `/api/chat` only** (per [SAG-2459](/SAG/issues/SAG-2459)) — never `opencode_local`. Ollama at `http://localhost:11434`.
- **Fixed N=50** prompts (all weather-intent; correct tool is always `get_weather`).
- **9-tool array** for the multi-tool mode; `[get_weather]` only for the single-tool control.
- Rejected-for-fit models (Llama 4 Scout — OOM on current hardware) are excluded by design.

## Models tested

- `qwen3:30b-a3b` — deployed Tier-0 winner
- `qwen3:32b` — deployed Tier-1
- `gemma3-tc:27b` — SAG-2529 template-patched reconsider candidate
- `gemma4:26b-a4b-it-q4_K_M` — reconsider candidate

## Pass criterion

Model emits a `get_weather` tool call with a `location` argument. Selecting any other tool or narrating = fail.

## SAG-2553 fleet-alignment rows

`results-sag2553.json` — same harness, env-selected models (`BENCH_MODELS` / `BENCH_OUT`): `llama3.3:70b-instruct-q4_K_M` + `qwen3-coder:30b`; GLM-5.1 is `unobtainable-on-ollama` (page exists, no published GGUF manifest as of 2026-05-31).
