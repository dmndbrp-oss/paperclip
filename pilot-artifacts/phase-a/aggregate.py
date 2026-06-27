"""
Phase A aggregation script — SAG-2154 Step 3.
Usage: python pilot-artifacts/phase-a/aggregate.py <batch_id> <run_ts>
Reads:  pilot-artifacts/phase-a/run-metrics-{batch_id}.jsonl
Writes: pilot-artifacts/phase-a/aggregate-{run_ts}.json
        pilot-artifacts/phase-a/anomalies-{run_ts}.jsonl
"""
import json
import math
import os
import sys
from datetime import datetime, timezone

def percentile(lst, p):
    if not lst:
        return None
    s = sorted(lst)
    k = (len(s) - 1) * p / 100
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)

def main():
    if len(sys.argv) < 2:
        print("Usage: aggregate.py <run_ts> [batch_id ...]")
        print("  If no batch_ids given, reads ALL run-metrics-*.jsonl in the same directory.")
        sys.exit(1)

    run_ts = sys.argv[1]
    batch_ids = sys.argv[2:]
    base = os.path.dirname(os.path.abspath(__file__))
    agg_path   = os.path.join(base, f"aggregate-{run_ts}.json")
    anom_path  = os.path.join(base, f"anomalies-{run_ts}.jsonl")

    import glob as _glob
    if batch_ids:
        jsonl_files = [os.path.join(base, f"run-metrics-{b}.jsonl") for b in batch_ids]
    else:
        jsonl_files = sorted(_glob.glob(os.path.join(base, "run-metrics-*.jsonl")))

    if not jsonl_files:
        print("ERROR: no metrics JSONL files found")
        sys.exit(1)

    rows = []
    for jf in jsonl_files:
        if not os.path.exists(jf):
            print(f"WARNING: file not found: {jf}")
            continue
        with open(jf) as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    print(f"Read {len(rows)} rows from {len(jsonl_files)} file(s)")

    if not rows:
        print("ERROR: no rows in metrics file")
        sys.exit(1)

    primary_rows = [r for r in rows if r["tier_used"] == "primary"]
    fallback_rows = [r for r in rows if r["tier_used"] == "fallback"]
    failed_rows  = [r for r in rows if r["tier_used"] == "failed"]
    reviewer_rows = [r for r in rows if r.get("reviewer_used")]

    primary_valid = [r for r in rows if r.get("primary_validator_valid") is True]
    schema_valid_rate_primary = len(primary_valid) / len(rows) if rows else 0

    reviewer_flagged = [r for r in reviewer_rows
                        if r.get("reviewer_anomaly_score") is not None
                        and r.get("reviewer_anomaly_score") >= 0.5]
    reviewer_flagged_rate = len(reviewer_flagged) / len(reviewer_rows) if reviewer_rows else None

    # Latencies
    prim_lats  = [r["primary_latency_s"] for r in rows if r.get("primary_latency_s") is not None]
    fall_lats  = [r["fallback_latency_s"] for r in fallback_rows if r.get("fallback_latency_s") is not None]
    rev_lats   = [r["reviewer_latency_s"] for r in reviewer_rows if r.get("reviewer_latency_s") is not None]
    total_lats = [r["row_total_s"] for r in rows if r.get("row_total_s") is not None]

    wall_time_s = sum(total_lats) / max(len(total_lats), 1)  # avg (concurrency=1 → sequential)
    # For sequential execution, wall time ≈ sum of row_total_s
    wall_time_s_total = sum(total_lats)

    agg = {
        "run_ts": run_ts,
        "jsonl_files": [os.path.basename(f) for f in jsonl_files],
        "rows_processed": len(rows),
        "tier_distribution": {
            "primary": len(primary_rows),
            "fallback": len(fallback_rows),
            "failed": len(failed_rows),
        },
        "schema_valid_rate_primary": round(schema_valid_rate_primary, 4),
        "reviewer_flagged_rate": round(reviewer_flagged_rate, 4) if reviewer_flagged_rate is not None else None,
        "latency_primary_p50_s": round(percentile(prim_lats, 50), 3) if prim_lats else None,
        "latency_primary_p95_s": round(percentile(prim_lats, 95), 3) if prim_lats else None,
        "latency_fallback_p50_s": round(percentile(fall_lats, 50), 3) if fall_lats else None,
        "latency_fallback_p95_s": round(percentile(fall_lats, 95), 3) if fall_lats else None,
        "latency_reviewer_p50_s": round(percentile(rev_lats, 50), 3) if rev_lats else None,
        "latency_reviewer_p95_s": round(percentile(rev_lats, 95), 3) if rev_lats else None,
        "wall_time_s": round(wall_time_s_total, 1),
        "total_cost_usd": 0.0,  # all local inference — free
    }

    with open(agg_path, "w") as f:
        json.dump(agg, f, indent=2)
    print(f"Wrote aggregate: {agg_path}")
    print(json.dumps(agg, indent=2))

    # Anomalies: reviewer_anomaly_score >= 0.5 OR tier_used != "primary"
    anomalies = [r for r in rows
                 if (r.get("reviewer_anomaly_score") is not None and r["reviewer_anomaly_score"] >= 0.5)
                 or r["tier_used"] != "primary"]

    with open(anom_path, "w") as f:
        for a in anomalies:
            f.write(json.dumps(a) + "\n")
    print(f"\nWrote anomalies ({len(anomalies)} rows): {anom_path}")


if __name__ == "__main__":
    main()
