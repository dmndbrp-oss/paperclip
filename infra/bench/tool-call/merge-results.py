#!/usr/bin/env python3
"""Merge two partial bench results into results-sag2553.json."""
import json
from pathlib import Path

HERE = Path(__file__).parent
PARTS = ["results-sag2554-llama.json", "results-sag2554-qwen3coder.json"]

rows = []
meta = None
for fname in PARTS:
    p = HERE / fname
    if not p.exists():
        print(f"WARNING: {fname} not found, skipping")
        continue
    data = json.loads(p.read_text())
    if meta is None:
        meta = {k: v for k, v in data.items() if k != "rows"}
    rows.extend(data.get("rows", []))

if not rows:
    print("ERROR: no rows to merge")
    raise SystemExit(1)

out = dict(meta or {})
out["rows"] = rows
out["bench"] = "SAG-2537 uniform tool-call re-bench"
out["note_sag2554"] = "SAG-2553 fleet-alignment rows: llama3.3:70b + qwen3-coder:30b; GLM-5.1 unobtainable-on-ollama"

dest = HERE / "results-sag2553.json"
dest.write_text(json.dumps(out, indent=2))
print(f"wrote {dest}")
print("rows:")
for r in rows:
    print(f"  {r['model']}: single {r['single_tool']} ({r['single_pct']}%)  multi {r['multi_tool']} ({r['multi_pct']}%)  tok/s {r.get('tok_per_sec')}")
