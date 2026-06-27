#!/usr/bin/env python3
"""fanout — run a local DYNAMIC WORKFLOW: fan N tasks across one local Ollama
model at an auto-sized width (2-4), decided by room_check.

This is the local-native flavor of a Dynamic Workflow (SAG-3075): a deterministic
orchestrator that fans out parallel HTTP calls to Ollama. ZERO cloud tokens.

  - Concurrency is chosen by room_check (or pinned with --concurrency / BENCH_CONCURRENCY).
  - ONE model per pass; pass --model again for another (run sequentially — the
    service is OLLAMA_MAX_LOADED_MODELS=1, so different models swap + queue anyway).
  - Each task returns content + any tool_calls + per-call timing.

Input: a JSON file (--tasks FILE) shaped {"model": "...", "tasks": [{"id":..,"prompt":..}],
        "system": "..."(optional), "tools": [...](optional, enables tool-calling)}
        or just a list of prompt strings.
Output: JSON to --out FILE (default fanout-results.json) and a summary to stdout.

Usage:
  python3 fanout.py --tasks tasks.json [--model NAME] [--concurrency N] [--out results.json]
"""
import json, os, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).parent
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
sys.path.insert(0, str(HERE))
from room_check import recommend  # noqa: E402


def call(model, prompt, system=None, tools=None, timeout=600):
    msgs = ([{"role": "system", "content": system}] if system else []) + \
           [{"role": "user", "content": prompt}]
    body = {"model": model, "messages": msgs, "stream": False,
            "think": False, "keep_alive": "20m"}
    if tools:
        body["tools"] = tools
    req = urllib.request.Request(f"{OLLAMA_URL}/api/chat",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.load(r)
    wall = time.time() - t0
    msg = d.get("message", {})
    ec, ed = d.get("eval_count", 0) or 0, d.get("eval_duration", 0) or 0
    return {"content": msg.get("content", ""), "tool_calls": msg.get("tool_calls"),
            "wall_s": round(wall, 2), "tok_s": round(ec / ed * 1e9, 1) if ed else None}


def load_tasks(path):
    raw = json.loads(Path(path).read_text())
    if isinstance(raw, list):
        return {"model": None, "system": None, "tools": None,
                "tasks": [{"id": i, "prompt": p} for i, p in enumerate(raw)]}
    raw.setdefault("system", None)
    raw.setdefault("tools", None)
    raw["tasks"] = [{"id": t.get("id", i), "prompt": t["prompt"]}
                    for i, t in enumerate(raw["tasks"])]
    return raw


def fan_out(model, tasks, system, tools, concurrency):
    results = [None] * len(tasks)
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        futs = {ex.submit(call, model, t["prompt"], system, tools): i
                for i, t in enumerate(tasks)}
        done = 0
        for fut in as_completed(futs):
            i = futs[fut]
            try:
                results[i] = {"id": tasks[i]["id"], **fut.result()}
            except Exception as e:
                results[i] = {"id": tasks[i]["id"], "error": str(e)}
            done += 1
            if done % max(1, concurrency) == 0 or done == len(tasks):
                print(f"    {done}/{len(tasks)} done ({time.time()-t0:.0f}s)", flush=True)
    return results, round(time.time() - t0, 1)


def main():
    a = sys.argv[1:]
    def opt(flag, default=None):
        return a[a.index(flag) + 1] if flag in a else default
    spec = load_tasks(opt("--tasks"))
    model = opt("--model", spec.get("model")) or os.environ.get("FANOUT_MODEL")
    if not model:
        print("ERROR: no model (pass --model or set it in the tasks file)", file=sys.stderr)
        return 2
    out_file = opt("--out", "fanout-results.json")

    pinned = opt("--concurrency", os.environ.get("BENCH_CONCURRENCY"))
    if pinned:
        concurrency, why = int(pinned), "pinned"
    else:
        rec = recommend(model)
        concurrency, why = rec["recommended_concurrency"], rec["note"]
        print(f"[room_check] -> {concurrency} worker(s) "
              f"(load/core {rec['load_per_core']}, {rec['mem_available_gb']}GB free): {why}", flush=True)

    print(f"[fanout] {model}: {len(spec['tasks'])} tasks @ concurrency={concurrency}", flush=True)
    results, wall = fan_out(model, spec["tasks"], spec["system"], spec["tools"], concurrency)
    ok = sum(1 for r in results if r and "error" not in r)
    out = {"model": model, "concurrency": concurrency, "concurrency_reason": why,
           "n": len(results), "ok": ok, "wall_s": wall, "results": results}
    Path(out_file).write_text(json.dumps(out, indent=2))
    print(f"[fanout] {ok}/{len(results)} ok in {wall}s -> {out_file}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
