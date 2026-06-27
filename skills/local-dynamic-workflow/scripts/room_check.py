#!/usr/bin/env python3
"""room_check — decide how many local workers (2-4) to deploy RIGHT NOW.

Looks at the box's available room (free memory, load average, what's already
loaded in Ollama) and returns a recommended fan-out width for a SAME-model
parallel sweep on the Strix Halo box.

Grounding (measured, SAG-3075):
  - Same model fanned out runs at full per-stream speed up to ~4 workers on an
    IDLE box (single iGPU batches the requests).
  - Under contention the per-stream speed is unchanged but requests QUEUE, so
    you want fewer workers when the box is busy (enrichment/Digester/etc).
  - The Ollama service is configured OLLAMA_MAX_LOADED_MODELS=1, so DIFFERENT
    models never co-reside — Ollama swaps + queues them automatically. This
    script therefore sizes width for ONE model; switching models is serial.

Usage:
  python3 room_check.py [--model NAME] [--json]
Exit: prints a one-line summary, or full JSON with --json. Recommended width on
stdout's last token is also parseable as an int via `--bare`.
"""
import json, os, sys, urllib.request

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
CEILING = int(os.environ.get("ROOM_CEILING", "4"))   # measured idle ceiling
MODEL_HEADROOM_GB = float(os.environ.get("MODEL_HEADROOM_GB", "6"))


def mem_available_gb():
    with open("/proc/meminfo") as f:
        for line in f:
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / (1024 * 1024)
    return 0.0


def load1_and_cores():
    load1 = os.getloadavg()[0]
    cores = os.cpu_count() or 1
    return load1, cores


def ollama_ps():
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/ps", timeout=10) as r:
            return json.load(r).get("models", [])
    except Exception:
        return []


def recommend(model=None):
    mem = mem_available_gb()
    load1, cores = load1_and_cores()
    lpc = load1 / cores                      # load per core
    loaded = ollama_ps()
    loaded_names = [m["name"] for m in loaded]

    # --- width sizing: start at the measured ceiling, back off under load ---
    if lpc < 0.25:
        width = CEILING            # box quiet -> full 4-wide
    elif lpc < 0.45:
        width = min(3, CEILING)
    elif lpc < 0.70:
        width = min(2, CEILING)    # busy (e.g. enrichment running) -> 2
    else:
        width = 1                  # slammed -> serial; let Ollama's queue absorb

    # --- memory guard: if a *different* model must be loaded, ensure room ---
    must_load = model is not None and not any(model in n for n in loaded_names)
    note = ""
    if must_load and mem < MODEL_HEADROOM_GB:
        width = 1
        note = (f"low free mem ({mem:.0f}GB) to load '{model}' — forcing serial; "
                f"Ollama will swap (MAX_LOADED_MODELS=1) and queue.")
    elif must_load:
        note = (f"'{model}' not resident — Ollama will swap it in "
                f"(MAX_LOADED_MODELS=1); first call pays load latency.")
    else:
        note = "target model already resident; same-model fan-out is cheap."

    return {
        "recommended_concurrency": width,
        "ceiling": CEILING,
        "mem_available_gb": round(mem, 1),
        "load1": round(load1, 2),
        "cores": cores,
        "load_per_core": round(lpc, 2),
        "loaded_models": loaded_names,
        "must_load_target": must_load,
        "note": note,
    }


def main():
    args = sys.argv[1:]
    model = None
    if "--model" in args:
        model = args[args.index("--model") + 1]
    rec = recommend(model)
    if "--bare" in args:
        print(rec["recommended_concurrency"])
    elif "--json" in args:
        print(json.dumps(rec, indent=2))
    else:
        print(f"deploy {rec['recommended_concurrency']} worker(s) "
              f"(ceiling {rec['ceiling']}, load/core {rec['load_per_core']}, "
              f"{rec['mem_available_gb']}GB free) — {rec['note']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
