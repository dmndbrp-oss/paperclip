#!/usr/bin/env python3
"""Local DYNAMIC WORKFLOW bench (SAG-3075 experiment, applied to SAG-3086).

This is the "dynamic workflow" version of run.py. Instead of looping the N
prompts SERIALLY through one model, a deterministic orchestrator FANS THEM OUT
~CONCURRENCY-wide against ONE loaded model (Ollama batches same-model requests).

Key rules baked in from the SAG-3075 measurement:
  - CONCURRENCY default 4  -> measured full-speed ceiling on this single-iGPU box.
  - SAME model fanned out (never two different models at once -> that thrashes).
  - Models are evaluated SERIALLY; the previous model is evicted (keep_alive=0)
    before the next loads, so we never co-resident two big models.

Reuses prompts.json / tools.json / is_pass() semantics from run.py so results
are directly comparable to the SAG-2537/2554 serial benches.
"""
import json, os, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

OLLAMA_URL = "http://localhost:11434"
HERE = Path(__file__).parent
PROMPTS = json.loads((HERE / "prompts.json").read_text())["prompts"]
TOOLS = json.loads((HERE / "tools.json").read_text())
SINGLE_TOOLS = TOOLS["single"]

CONCURRENCY = int(os.environ.get("BENCH_CONCURRENCY", "4"))   # measured ceiling (idle box)
MODELS = [m.strip() for m in os.environ.get(
    "BENCH_MODELS", "qwen3:30b-a3b,gemma4:26b-a4b-it-q4_K_M").split(",") if m.strip()]
OUT_FILE = os.environ.get("BENCH_OUT", "results-sag3086-dynamic.json")
_n = int(os.environ.get("BENCH_N", "0"))   # 0 = all prompts; >0 = slice for a fast demo
if _n > 0:
    PROMPTS = PROMPTS[:_n]


def call(model, prompt, tools, keep_alive="5m"):
    payload = json.dumps({
        "model": model, "messages": [{"role": "user", "content": prompt}],
        "tools": tools, "stream": False, "think": False, "keep_alive": keep_alive,
    }).encode()
    req = urllib.request.Request(f"{OLLAMA_URL}/api/chat", data=payload,
                                 headers={"Content-Type": "application/json"}, method="POST")
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=600) as resp:
        data = json.loads(resp.read())
    data["_wall"] = time.time() - t0
    return data


def is_pass(data):
    tcs = data.get("message", {}).get("tool_calls", [])
    return any(
        tc.get("function", {}).get("name") == "get_weather"
        and isinstance(tc.get("function", {}).get("arguments", {}), dict)
        and "location" in tc.get("function", {}).get("arguments", {})
        for tc in tcs)


def unload(model):
    """Evict a model so the next one loads into a clear window (no co-residence)."""
    try:
        call(model, "bye", SINGLE_TOOLS, keep_alive="0")
    except Exception:
        pass
    for _ in range(40):  # wait up to ~60s for /api/ps to clear it
        ps = json.loads(urllib.request.urlopen(f"{OLLAMA_URL}/api/ps", timeout=10).read())
        if not any(model in m["name"] for m in ps.get("models", [])):
            return True
        time.sleep(1.5)
    return False


def bench_model(model):
    # warm (load + first-token) so the timed fan-out measures steady-state
    call(model, "warmup", SINGLE_TOOLS)
    results = [None] * len(PROMPTS)
    t0 = time.time()
    done = 0
    from concurrent.futures import as_completed
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as ex:
        futs = {ex.submit(call, model, p, SINGLE_TOOLS): i for i, p in enumerate(PROMPTS)}
        for fut in as_completed(futs):
            i = futs[fut]
            try:
                results[i] = fut.result()
            except Exception as e:
                results[i] = {"_error": str(e), "_wall": 0}
            done += 1
            if done % 4 == 0 or done == len(PROMPTS):
                print(f"    {done}/{len(PROMPTS)} done ({time.time()-t0:.0f}s elapsed)", flush=True)
    wall = time.time() - t0
    passed = sum(1 for r in results if r and "_error" not in r and is_pass(r))
    errors = sum(1 for r in results if r and "_error" in r)
    sum_call_wall = sum((r or {}).get("_wall", 0) for r in results)   # ~= serial wall
    ec = sum((r or {}).get("eval_count", 0) or 0 for r in results)
    ed = sum((r or {}).get("eval_duration", 0) or 0 for r in results)
    return {
        "model": model, "concurrency": CONCURRENCY, "N": len(PROMPTS),
        "passed": f"{passed}/{len(PROMPTS)}", "pass_pct": round(passed / len(PROMPTS) * 100),
        "errors": errors,
        "wall_parallel_s": round(wall, 1),
        "serial_equiv_s": round(sum_call_wall, 1),
        "speedup_x": round(sum_call_wall / wall, 2) if wall else None,
        "tok_per_sec": round(ec / ed * 1e9, 1) if ed else None,
    }


def main():
    out = {"bench": "SAG-3075 local dynamic-workflow demo on SAG-3086",
           "mode": "single-tool", "concurrency": CONCURRENCY, "rows": []}
    for idx, m in enumerate(MODELS):
        print(f"[{time.strftime('%H:%M:%S')}] fan-out {m} (concurrency={CONCURRENCY}) ...", flush=True)
        row = bench_model(m)
        out["rows"].append(row)
        print("  ->", json.dumps(row), flush=True)
        if idx < len(MODELS) - 1:
            print(f"  evicting {m} before next model ...", flush=True)
            unload(m)
    (HERE / OUT_FILE).write_text(json.dumps(out, indent=2))
    print("wrote", HERE / OUT_FILE, flush=True)


if __name__ == "__main__":
    main()
