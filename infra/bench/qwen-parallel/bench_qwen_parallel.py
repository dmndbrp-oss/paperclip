#!/usr/bin/env python3
"""
Benchmark qwen3.6 N=1 vs N=4 concurrent requests.
Measures decode tok/s per request using Ollama eval_count/eval_duration.
Run N=1 first (baseline), then N=4 (parallel with NUM_PARALLEL=4 active).
"""

import json
import time
import urllib.request
import threading
import argparse
import sys

OLLAMA_URL = "http://localhost:11434"
MODEL = "qwen3.6:latest"
PROMPT = "/no_think Say 'hello world' and count to 50."
# Short enough to complete quickly; num_predict caps token output
NUM_PREDICT = 80


def _generate(prompt: str, result_slot: list, idx: int):
    payload = json.dumps({
        "model": MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {
            "num_predict": NUM_PREDICT,
            "think": False,
        },
    }).encode()
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    t0 = time.monotonic()
    with urllib.request.urlopen(req, timeout=300) as resp:
        body = json.loads(resp.read())
    wall_s = time.monotonic() - t0

    eval_count = body.get("eval_count", 0)
    eval_duration_ns = body.get("eval_duration", 0)
    decode_tps = eval_count / (eval_duration_ns / 1e9) if eval_duration_ns > 0 else 0
    result_slot.append({
        "idx": idx,
        "eval_count": eval_count,
        "eval_duration_s": round(eval_duration_ns / 1e9, 2),
        "decode_tps": round(decode_tps, 1),
        "wall_s": round(wall_s, 2),
        "response_snippet": body.get("response", "")[:80],
    })


def run_n(n: int) -> list:
    results = [[] for _ in range(n)]
    threads = [
        threading.Thread(target=_generate, args=(PROMPT, results[i], i))
        for i in range(n)
    ]
    t0 = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall_total = time.monotonic() - t0
    flat = [r[0] for r in results if r]
    return flat, wall_total


def check_model_state():
    req = urllib.request.Request(f"{OLLAMA_URL}/api/ps", method="GET")
    with urllib.request.urlopen(req, timeout=10) as resp:
        body = json.loads(resp.read())
    return body.get("models", [])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=1, help="Number of concurrent requests")
    parser.add_argument("--skip-n1", action="store_true", help="Skip N=1, run N only")
    args = parser.parse_args()

    print(f"=== Qwen 3.6 parallel benchmark (N={args.n}) ===")
    print(f"Model: {MODEL}, prompt: {PROMPT[:40]}..., num_predict={NUM_PREDICT}\n")

    # --- N=1 baseline ---
    if not args.skip_n1 and args.n != 1:
        print("Running N=1 baseline...")
        r1, w1 = run_n(1)
        if r1:
            print(f"  N=1: eval_count={r1[0]['eval_count']} tokens, "
                  f"eval_duration={r1[0]['eval_duration_s']}s, "
                  f"decode={r1[0]['decode_tps']} tok/s, wall={r1[0]['wall_s']}s")
        else:
            print("  N=1: ERROR — no result")
        print()

    # --- Capture model state before N=concurrent ---
    print("Model state BEFORE concurrent run:")
    models = check_model_state()
    for m in models:
        print(f"  name={m['name']} context_length={m.get('context_length','?')} "
              f"size_vram={m.get('size_vram',0)//1024//1024//1024:.1f}GB")
    if not models:
        print("  (no models loaded)")
    print()

    # --- N concurrent ---
    print(f"Running N={args.n} concurrent requests...")
    results, wall_total = run_n(args.n)

    print(f"\n--- Results (N={args.n}) ---")
    total_tokens = sum(r["eval_count"] for r in results)
    per_req_tps = [r["decode_tps"] for r in results]
    for r in results:
        print(f"  req[{r['idx']}]: {r['eval_count']} tokens, "
              f"decode={r['decode_tps']} tok/s, wall={r['wall_s']}s")
    print(f"  Total wall: {wall_total:.1f}s, aggregate tok/s: "
          f"{total_tokens / wall_total:.1f} tok/s")
    print(f"  Per-req tok/s avg: {sum(per_req_tps)/len(per_req_tps):.1f}")

    # --- Model state after ---
    print(f"\nModel state AFTER N={args.n} run (during keepalive):")
    models = check_model_state()
    for m in models:
        print(f"  name={m['name']} context_length={m.get('context_length','?')} "
              f"size_vram={m.get('size_vram',0)//1024//1024//1024:.1f}GB "
              f"(count={len(models)} instances)")
    if not models:
        print("  (model already unloaded)")

    print("\nDone.")


if __name__ == "__main__":
    main()
