#!/usr/bin/env python3
"""Uniform tool-call re-bench (SAG-2537).

DIRECT Ollama /api/chat per SAG-2459 (never opencode_local).
Identical N across all models; 1-tool control + multi-tool selection.
"""
import json, os, sys, urllib.request
from pathlib import Path

OLLAMA_URL = "http://localhost:11434"
HERE = Path(__file__).parent
PROMPTS = json.loads((HERE / "prompts.json").read_text())["prompts"]
TOOLS = json.loads((HERE / "tools.json").read_text())
SINGLE_TOOLS = TOOLS["single"]
MULTI_TOOLS = TOOLS["multi"]

DEFAULT_MODELS = [
    "qwen3:30b-a3b",
    "qwen3:32b",
    "gemma3-tc:27b",
    "gemma4:26b-a4b-it-q4_K_M",
]

_env_models = os.environ.get("BENCH_MODELS", "").strip()
MODELS = [m.strip() for m in _env_models.split(",") if m.strip()] or DEFAULT_MODELS
OUT_FILE = os.environ.get("BENCH_OUT", "results.json")


def call(model, prompt, tools):
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "tools": tools,
        "stream": False,
        "think": False,  # top-level field disables qwen3 reasoning tokens (options.think does not work)
    }).encode()
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/chat", data=payload,
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=600) as resp:
        return json.loads(resp.read())


def is_pass(data):
    tcs = data.get("message", {}).get("tool_calls", [])
    return any(
        tc.get("function", {}).get("name") == "get_weather"
        and isinstance(tc.get("function", {}).get("arguments", {}), dict)
        and "location" in tc.get("function", {}).get("arguments", {})
        for tc in tcs
    )


def run_mode(model, tools):
    passed, details = 0, []
    total_eval_count, total_eval_duration = 0, 0
    for i, p in enumerate(PROMPTS):
        try:
            data = call(model, p, tools)
            ok = is_pass(data)
            note = data.get("message", {}).get("content", "")[:120]
            total_eval_count += data.get("eval_count", 0) or 0
            total_eval_duration += data.get("eval_duration", 0) or 0
        except Exception as e:
            ok, note = False, f"ERROR: {e}"
        passed += 1 if ok else 0
        details.append({"i": i + 1, "pass": ok, "note": note})
    if total_eval_duration > 0:
        tok_per_sec = round(total_eval_count / total_eval_duration * 1e9, 1)
    else:
        tok_per_sec = None
    return passed, details, tok_per_sec


def main():
    n = len(PROMPTS)
    out = {
        "bench": "SAG-2537 uniform tool-call re-bench",
        "N": n,
        "tool_call_path": "DIRECT /api/chat (SAG-2459)",
        "multi_tool_count": len(MULTI_TOOLS),
        "rows": [],
    }
    for m in MODELS:
        s_pass, s_det, s_tps = run_mode(m, SINGLE_TOOLS)
        mu_pass, mu_det, mu_tps = run_mode(m, MULTI_TOOLS)
        # average tok/s across both modes (use non-null values only)
        tps_vals = [v for v in [s_tps, mu_tps] if v is not None]
        tok_per_sec = round(sum(tps_vals) / len(tps_vals), 1) if tps_vals else None
        out["rows"].append({
            "model": m,
            "single_tool": f"{s_pass}/{n}",
            "single_pct": round(s_pass / n * 100),
            "multi_tool": f"{mu_pass}/{n}",
            "multi_pct": round(mu_pass / n * 100),
            "tok_per_sec": tok_per_sec,
            "single_detail": s_det,
            "multi_detail": mu_det,
        })
        print(f"{m}: single {s_pass}/{n}  multi {mu_pass}/{n}  tok/s {tok_per_sec}", flush=True)
    (HERE / OUT_FILE).write_text(json.dumps(out, indent=2))
    print("wrote", HERE / OUT_FILE, flush=True)


if __name__ == "__main__":
    sys.exit(main())
