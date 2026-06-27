#!/usr/bin/env python3
"""
SAG-4434 — Hermes concurrency benchmark.

Phase 1 (no root change needed):
  a. Solo qwen3.6 decode tok/s (clean baseline).
  b. Pull qwen3:30b-a3b, record arch + resident size.
  c. Solo qwen3:30b-a3b decode tok/s.
  d. Concurrency sweep on qwen3:30b-a3b ALONE (qwen3.6 evicted under
     MAX_LOADED_MODELS=1): fire 1..5 concurrent identical requests,
     record per-request decode tok/s, aggregate tok/s, peak RAM.

HARD GATE: aborts unless the box is idle (no live kanban agents, GPU low).
Re-checks idle before each heavy phase so we never lag the board's live agents.

Decode rate uses Ollama eval_count/eval_duration (decode-only), NOT wall clock.
"""

import json
import time
import subprocess
import urllib.request
import threading
import argparse
import sys
import os
import re

OLLAMA_URL = "http://localhost:11434"
QWEN36 = "qwen3.6:latest"
QWEN30 = "qwen3:30b-a3b"
# Long-ish generation to get a stable decode-rate sample (not a 5-token blip).
PROMPT = "/no_think Write a detailed numbered list explaining how a four-stroke internal combustion engine works, step by step."
NUM_PREDICT = 400
USABLE_TPS = 15.0  # per-agent "usable" threshold
NUM_CTX = 32768      # KV context per slot — loop-appropriate 32k (SAG-4463)
BUDGET_GB = 110      # GiB for model+KV: 122 GiB unified − ~12 GiB OS+board
SWEEP = [1, 2, 3, 4, 5]
OUTDIR = os.path.dirname(os.path.abspath(__file__))


# ---------- idle gate ----------
def kanban_count() -> int:
    out = subprocess.run(["pgrep", "-af", "kanban task"],
                         capture_output=True, text=True).stdout
    return len([l for l in out.splitlines() if "kanban task" in l and "pgrep" not in l])


def gpu_busy_pct() -> int:
    try:
        out = subprocess.run(["rocm-smi", "--showuse"], capture_output=True,
                             text=True, timeout=15).stdout
        m = re.search(r"GPU use \(%\):\s*(\d+)", out)
        return int(m.group(1)) if m else -1
    except Exception:
        return -1


def assert_idle(stage: str):
    k = kanban_count()
    g = gpu_busy_pct()
    if k != 0:
        print(f"[ABORT @ {stage}] box NOT idle: {k} kanban agents live "
              f"(GPU {g}%). Deferring per SAG-4434 off-hours constraint.")
        sys.exit(3)
    print(f"[idle-ok @ {stage}] kanban=0, GPU={g}%")


# ---------- ollama helpers ----------
def ollama_ps():
    req = urllib.request.Request(f"{OLLAMA_URL}/api/ps", method="GET")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read()).get("models", [])


def peak_ram_gb() -> float:
    """Sum size_vram of resident models (unified memory on this box)."""
    try:
        models = ollama_ps()
        return round(sum(m.get("size_vram", 0) for m in models) / 1024**3, 1)
    except Exception:
        return -1.0


def generate(model: str, slot: list, idx: int):
    payload = json.dumps({
        "model": model, "prompt": PROMPT, "stream": False,
        "options": {"num_predict": NUM_PREDICT, "think": False, "num_ctx": NUM_CTX},
    }).encode()
    req = urllib.request.Request(f"{OLLAMA_URL}/api/generate", data=payload,
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            body = json.loads(resp.read())
    except Exception as e:
        slot.append({"idx": idx, "error": str(e)})
        return
    wall = time.monotonic() - t0
    ec = body.get("eval_count", 0)
    ed = body.get("eval_duration", 0)
    tps = ec / (ed / 1e9) if ed > 0 else 0
    slot.append({"idx": idx, "eval_count": ec,
                 "eval_duration_s": round(ed / 1e9, 2),
                 "decode_tps": round(tps, 1), "wall_s": round(wall, 2)})


def run_n(model: str, n: int):
    slots = [[] for _ in range(n)]
    threads = [threading.Thread(target=generate, args=(model, slots[i], i))
               for i in range(n)]
    peak = [peak_ram_gb()]

    def sample_ram():
        while any(t.is_alive() for t in threads):
            peak.append(peak_ram_gb())
            time.sleep(2)

    mon = threading.Thread(target=sample_ram)
    t0 = time.monotonic()
    for t in threads:
        t.start()
    mon.start()
    for t in threads:
        t.join()
    mon.join()
    wall = time.monotonic() - t0
    flat = [s[0] for s in slots if s]
    return flat, wall, max(peak)


def loaded_context(model: str) -> int:
    """Return context length of the named model from /api/ps, or -1 if not found."""
    try:
        for m in ollama_ps():
            if model in m.get("name", ""):
                return m.get("context", -1)
    except Exception:
        pass
    return -1


def solo_tps(model: str) -> dict:
    slot = []
    generate(model, slot, 0)
    return slot[0] if slot else {"error": "no result"}


def model_arch(model: str) -> dict:
    out = subprocess.run(["ollama", "show", model], capture_output=True,
                         text=True, timeout=60).stdout
    arch = re.search(r"architecture\s+(\S+)", out)
    params = re.search(r"parameters\s+(\S+)", out)
    ctx = re.search(r"context length\s+(\S+)", out)
    return {"architecture": arch.group(1) if arch else "?",
            "parameters": params.group(1) if params else "?",
            "context_length": ctx.group(1) if ctx else "?",
            "raw": out}


# ---------- phases ----------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-pull", action="store_true")
    args = ap.parse_args()

    results = {"started": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
               "usable_tps_threshold": USABLE_TPS, "num_predict": NUM_PREDICT,
               "num_ctx": NUM_CTX, "budget_gb": BUDGET_GB}

    assert_idle("start")

    # a. solo qwen3.6
    print("\n=== a. solo qwen3.6 decode ===")
    results["a_qwen36_solo"] = solo_tps(QWEN36)
    print(results["a_qwen36_solo"])

    # b. pull + arch
    if not args.skip_pull:
        print("\n=== b. pull qwen3:30b-a3b ===")
        subprocess.run(["ollama", "pull", QWEN30], check=False)
    results["b_qwen30_arch"] = model_arch(QWEN30)
    print({k: v for k, v in results["b_qwen30_arch"].items() if k != "raw"})

    # c. solo qwen3:30b-a3b  (this load evicts qwen3.6 under MAX_LOADED=1)
    assert_idle("before-solo-30b")
    print("\n=== c. solo qwen3:30b-a3b decode ===")
    results["c_qwen30_solo"] = solo_tps(QWEN30)
    results["c_qwen30_resident_gb"] = peak_ram_gb()
    results["c_qwen30_loaded_ctx"] = loaded_context(QWEN30)
    print(results["c_qwen30_solo"], "resident_gb=", results["c_qwen30_resident_gb"],
          "loaded_ctx=", results["c_qwen30_loaded_ctx"])

    # d. concurrency sweep on qwen3:30b-a3b alone
    print("\n=== d. concurrency sweep qwen3:30b-a3b ===")
    sweep = []
    for n in SWEEP:
        assert_idle(f"before-sweep-N{n}")
        reqs, wall, peak = run_n(QWEN30, n)
        ok = [r for r in reqs if "decode_tps" in r]
        per = [r["decode_tps"] for r in ok]
        agg = sum(r["eval_count"] for r in ok) / wall if wall > 0 else 0
        row = {"n": n, "wall_s": round(wall, 1),
               "per_req_tps": per,
               "min_tps": round(min(per), 1) if per else 0,
               "avg_tps": round(sum(per) / len(per), 1) if per else 0,
               "aggregate_tps": round(agg, 1),
               "peak_ram_gb": peak,
               "errors": [r for r in reqs if "error" in r],
               "usable": bool(per) and min(per) >= USABLE_TPS}
        sweep.append(row)
        print(f"  N={n}: min={row['min_tps']} avg={row['avg_tps']} "
              f"agg={row['aggregate_tps']} tok/s, peak_ram={peak}GB, "
              f"usable={row['usable']}")
    results["d_sweep"] = sweep
    usable_ns = [r["n"] for r in sweep if r["usable"]]
    results["max_usable_n"] = max(usable_ns) if usable_ns else 0

    # --- Three-number summary (SAG-4463) ---
    sweep_by_n = {r["n"]: r for r in sweep}
    ram1 = sweep_by_n.get(1, {}).get("peak_ram_gb", -1)
    ram2 = sweep_by_n.get(2, {}).get("peak_ram_gb", -1)
    summary: dict = {}
    if ram1 > 0 and ram2 > 0 and ram2 > ram1:
        per_slot_kv_gb = round(ram2 - ram1, 2)
        weights_gb = round(ram1 - per_slot_kv_gb, 2)
        mem_max = int((BUDGET_GB - weights_gb) / per_slot_kv_gb) if per_slot_kv_gb > 0 else -1
        summary["per_slot_kv_gb"] = per_slot_kv_gb
        summary["weights_gb"] = weights_gb
        summary["extrapolation_note"] = (
            "EXTRAPOLATION — KV assumed linear; measured N=1..5 only; "
            "valid only if KV usage scales linearly with N"
        )
    else:
        mem_max = -1
        summary["extrapolation_note"] = "insufficient RAM samples to extrapolate"
    # (a)
    summary["memory_bound_max_slots"] = mem_max
    # (b)
    max_usable_n = results["max_usable_n"]
    summary["throughput_usable_max_slots"] = max_usable_n
    # (c)
    per_req_tps = sweep_by_n.get(max_usable_n, {}).get("avg_tps", 0) if max_usable_n > 0 else 0
    summary["per_slot_tps_at_usable"] = per_req_tps
    summary["one_liner"] = (
        f"You can run {max_usable_n} concurrent qwen3:30b-a3b loop workers "
        f"at ~{per_req_tps} tok/s each at 32k ctx."
    )
    results["summary"] = summary
    print("\n=== SUMMARY ===")
    for k, v in summary.items():
        print(f"  {k}: {v}")

    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    path = os.path.join(OUTDIR, f"results-sag4463-{ts}.json")
    with open(path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nWROTE {path}")
    print(f"MAX USABLE N (>= {USABLE_TPS} tok/s/agent) = {results['max_usable_n']}")


if __name__ == "__main__":
    main()
