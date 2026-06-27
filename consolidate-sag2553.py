#!/usr/bin/env python3
"""Consolidate SAG-2554 benchmark results into results-sag2553.json"""
import json
from pathlib import Path

HERE = Path("infra/bench/tool-call")

files = {
    "llama": HERE / "results-sag2554-llama.json",
    "qwen": HERE / "results-sag2554-qwen.json",
}

base_data = None
all_rows = []

for name, filepath in files.items():
    if filepath.exists():
        with open(filepath) as f:
            data = json.load(f)
        if base_data is None:
            base_data = {
                "bench": data["bench"],
                "N": data["N"],
                "tool_call_path": data["tool_call_path"],
                "multi_tool_count": data["multi_tool_count"],
                "rows": [],
            }
        all_rows.extend(data["rows"])
        print(f"Loaded {name}: {len(data['rows'])} model(s)")
    else:
        print(f"File not found: {filepath} ({name})")

if base_data:
    base_data["rows"] = all_rows
    out_path = HERE / "results-sag2553.json"
    with open(out_path, "w") as f:
        json.dump(base_data, f, indent=2)
    print(f"Consolidated {len(all_rows)} models -> {out_path}")
else:
    print("ERROR: No results files found")
