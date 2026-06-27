"""Phase A N=100 full validation run — SAG-3686.

Config B' (locked — do NOT change):
  model = gemma4-26b-a4b-it-q4_K_M
  response_format = {"type": "json_object"}
  max_tokens = 4096
  temperature = 0
  think = False  (top-level, NOT under options)

Measurement-only: reads enrichment_queue by ctid order, NO writes to production catalog.
Outputs:
  - per-row JSONL:  phase-a/sag3686-per-row-<timestamp>.jsonl
  - aggregate JSON: phase-a/sag3686-aggregate-<timestamp>.json
  - log:            phase-a/sag3686-run-<timestamp>.log

Usage:
  cd /home/gus-pinsoneault/.paperclip/.../4dc8eabc-212d-4a46-a0eb-aa61b75e82d0/_default
  DATABASE_URL=... LITELLM_BASE_URL=... LITELLM_API_KEY=... python3 pilot-artifacts/phase-a/run-phase-a-100row.py
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import psycopg2
import psycopg2.extras

# --- path setup ---
BASE_DIR = Path(__file__).parent.parent.parent  # workspace root
sys.path.insert(0, str(BASE_DIR / "enrichment"))
sys.path.insert(0, str(BASE_DIR / "pilot-artifacts"))
from dispatcher import _build_enrichment_messages, _repair_cross_fields, PRIMARY_MODEL  # type: ignore
from validator import validate  # type: ignore

# --- config ---
DB_URL = os.environ["DATABASE_URL"]
LITELLM_BASE = os.environ.get("LITELLM_BASE_URL", "http://localhost:4000")
LITELLM_KEY = os.environ.get("LITELLM_API_KEY", "")
N = 100
TIMEOUT = 300.0  # 5 min per row; gemma4 with think:false runs ~6–7s

# --- artifact paths ---
TS = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
PHASE_A_DIR = Path(__file__).parent
JSONL_PATH = PHASE_A_DIR / f"sag3686-per-row-{TS}.jsonl"
AGG_PATH = PHASE_A_DIR / f"sag3686-aggregate-{TS}.json"
LOG_PATH = PHASE_A_DIR / f"sag3686-run-{TS}.log"

# --- logging ---
handlers = [logging.StreamHandler(sys.stdout), logging.FileHandler(str(LOG_PATH))]
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=handlers)
log = logging.getLogger(__name__)

log.info("SAG-3686 Phase A N=100 Config B' run starting — ts=%s", TS)
log.info("model=%s N=%d LITELLM_BASE=%s", PRIMARY_MODEL, N, LITELLM_BASE)
log.info("Config B': response_format=json_object max_tokens=4096 temperature=0 think=false (top-level)")


def wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return 0.0, 0.0
    p_hat = k / n
    denom = 1 + z ** 2 / n
    centre = (p_hat + z ** 2 / (2 * n)) / denom
    half = (z * math.sqrt(p_hat * (1 - p_hat) / n + z ** 2 / (4 * n ** 2))) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def main() -> None:
    # --- baseline catalog check ---
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM enrichment_staging.enrichment_staging")
    baseline_staging = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM enrichment_staging.enrichment_promotion_log")
    baseline_promo = cur.fetchone()[0]
    log.info("Baseline: enrichment_staging=%d, promotion_log=%d", baseline_staging, baseline_promo)

    # --- pull 100 rows by ctid, all statuses (measurement-only) ---
    cur.execute(
        "SELECT source_row_id, payload_json "
        "FROM enrichment_staging.enrichment_queue "
        "ORDER BY ctid LIMIT %s",
        (N,),
    )
    rows = []
    seen_ids: set[str] = set()
    for source_row_id, payload_json in cur.fetchall():
        if source_row_id in seen_ids:
            continue
        seen_ids.add(source_row_id)
        payload = payload_json if isinstance(payload_json, dict) else json.loads(payload_json)
        rows.append((source_row_id, payload))
    conn.close()

    log.info("Pulled %d rows (deduped from %d)", len(rows), N)
    if len(rows) < N:
        log.warning("Only %d rows available (requested %d); proceeding.", len(rows), N)

    # --- counters ---
    valid_count = 0
    trunc_count = 0
    latencies: list[float] = []
    modes: dict[str, int] = {
        "valid": 0, "truncated": 0, "missing_fields": 0,
        "empty": 0, "other_invalid": 0, "http_err": 0,
    }
    invalid_rows: list[dict] = []  # for DoD failure-mode breakdown

    headers = {}
    if LITELLM_KEY:
        headers["Authorization"] = f"Bearer {LITELLM_KEY}"

    jsonl_fh = JSONL_PATH.open("w")

    with httpx.Client(timeout=TIMEOUT) as client:
        for idx, (source_row_id, payload) in enumerate(rows, 1):
            log.info("Row %d/%d  id=%s", idx, len(rows), source_row_id)
            system, user = _build_enrichment_messages(payload)

            body = {
                "model": PRIMARY_MODEL,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "max_tokens": 4096,
                "response_format": {"type": "json_object"},
                "temperature": 0,
                "think": False,  # top-level — suppresses gemma4 thinking tokens
            }

            row_rec: dict = {
                "source_row_id": source_row_id,
                "row_index": idx,
                "primary_valid": None,
                "latency_s": None,
                "failure_mode": None,
                "offending_fields": [],
            }

            t0 = time.monotonic()
            try:
                resp = client.post(f"{LITELLM_BASE}/v1/chat/completions", headers=headers, json=body)
                dt = time.monotonic() - t0
                latencies.append(dt)
                row_rec["latency_s"] = round(dt, 3)

                if resp.status_code != 200:
                    modes["http_err"] += 1
                    row_rec["failure_mode"] = "http_err"
                    row_rec["primary_valid"] = False
                    log.warning("  %s: HTTP %d %.1fs", source_row_id, resp.status_code, dt)
                    jsonl_fh.write(json.dumps(row_rec) + "\n")
                    jsonl_fh.flush()
                    invalid_rows.append(row_rec)
                    continue

                content = resp.json()["choices"][0]["message"].get("content") or ""
                # dispatcher parse path: strip think tags + markdown fences
                content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
                content = re.sub(r"^```(?:json)?\s*", "", content).rstrip("`").strip()

                if not content:
                    modes["empty"] += 1
                    row_rec["failure_mode"] = "empty"
                    row_rec["primary_valid"] = False
                    log.warning("  %s: EMPTY %.1fs", source_row_id, dt)
                    jsonl_fh.write(json.dumps(row_rec) + "\n")
                    jsonl_fh.flush()
                    invalid_rows.append(row_rec)
                    continue

                try:
                    parsed = json.loads(content)
                except Exception as parse_err:
                    modes["truncated"] += 1
                    trunc_count += 1
                    row_rec["failure_mode"] = "truncated"
                    row_rec["primary_valid"] = False
                    log.warning("  %s: TRUNC/parsefail %.1fs len=%d %s", source_row_id, dt, len(content), str(parse_err)[:60])
                    jsonl_fh.write(json.dumps(row_rec) + "\n")
                    jsonl_fh.flush()
                    invalid_rows.append(row_rec)
                    continue

                # apply R1/R2/R3 repairs before validation
                _repair_cross_fields(parsed)
                result = validate(parsed)

                if result.get("valid"):
                    valid_count += 1
                    modes["valid"] += 1
                    row_rec["primary_valid"] = True
                    log.info("  %s: VALID %.1fs", source_row_id, dt)
                else:
                    errs = result.get("errors") or []
                    row_rec["primary_valid"] = False
                    row_rec["offending_fields"] = [str(e) for e in errs[:5]]
                    if any("missing_required" in str(e) or "null_required" in str(e) for e in errs):
                        modes["missing_fields"] += 1
                        row_rec["failure_mode"] = "missing_fields"
                    else:
                        modes["other_invalid"] += 1
                        row_rec["failure_mode"] = "other_invalid"
                    log.warning("  %s: INVALID %.1fs %s", source_row_id, dt, errs[:3])
                    invalid_rows.append(row_rec)

            except httpx.TimeoutException:
                dt = time.monotonic() - t0
                modes["http_err"] += 1
                row_rec["failure_mode"] = "timeout"
                row_rec["primary_valid"] = False
                row_rec["latency_s"] = round(dt, 3)
                log.warning("  %s: TIMEOUT %.1fs", source_row_id, dt)
                invalid_rows.append(row_rec)
            except Exception as exc:
                dt = time.monotonic() - t0
                modes["http_err"] += 1
                row_rec["failure_mode"] = "http_err"
                row_rec["primary_valid"] = False
                row_rec["latency_s"] = round(dt, 3)
                log.warning("  %s: ERROR %.1fs %s", source_row_id, dt, str(exc)[:80])
                invalid_rows.append(row_rec)

            jsonl_fh.write(json.dumps(row_rec) + "\n")
            jsonl_fh.flush()

    jsonl_fh.close()

    # --- post-run catalog assertion ---
    conn2 = psycopg2.connect(DB_URL)
    cur2 = conn2.cursor()
    cur2.execute("SELECT count(*) FROM enrichment_staging.enrichment_staging")
    post_staging = cur2.fetchone()[0]
    cur2.execute("SELECT count(*) FROM enrichment_staging.enrichment_promotion_log")
    post_promo = cur2.fetchone()[0]
    conn2.close()

    catalog_rows_touched = (post_staging - baseline_staging) + (post_promo - baseline_promo)
    log.info("Post-run: enrichment_staging=%d (+%d), promotion_log=%d (+%d)",
             post_staging, post_staging - baseline_staging,
             post_promo, post_promo - baseline_promo)

    # --- stats ---
    n_processed = len(rows)
    pct = 100.0 * valid_count / n_processed if n_processed else 0.0
    ci_lo, ci_hi = wilson_ci(valid_count, n_processed)

    if latencies:
        latencies.sort()
        p50 = latencies[len(latencies) // 2]
        p95 = latencies[int(len(latencies) * 0.95)]
    else:
        p50 = p95 = 0.0

    agg = {
        "run_ts": TS,
        "issue": "SAG-3686",
        "config": "B-prime",
        "model": PRIMARY_MODEL,
        "n_requested": N,
        "n_processed": n_processed,
        "valid_count": valid_count,
        "schema_valid_pct": round(pct, 2),
        "wilson_95ci_lo_pct": round(ci_lo * 100, 1),
        "wilson_95ci_hi_pct": round(ci_hi * 100, 1),
        "truncation_count": trunc_count,
        "failure_modes": modes,
        "latency_p50_s": round(p50, 3),
        "latency_p95_s": round(p95, 3),
        "catalog_rows_touched": catalog_rows_touched,
        "promotion_guard_ok": catalog_rows_touched == 0,
        "per_row_jsonl": str(JSONL_PATH),
        "log_path": str(LOG_PATH),
    }

    AGG_PATH.write_text(json.dumps(agg, indent=2))

    log.info("=" * 70)
    log.info("SAG-3686 RESULTS: valid=%d/%d = %.0f%%", valid_count, n_processed, pct)
    log.info("Wilson 95%% CI: [%.1f%%, %.1f%%]", ci_lo * 100, ci_hi * 100)
    log.info("Truncation count: %d (must be 0)", trunc_count)
    log.info("Failure modes: %s", {k: v for k, v in modes.items() if v})
    log.info("Catalog rows touched: %d (must be 0)", catalog_rows_touched)
    log.info("Promotion guard: %s", "PASS" if catalog_rows_touched == 0 else "FAIL")
    log.info("Per-row JSONL: %s", JSONL_PATH)
    log.info("Aggregate JSON: %s", AGG_PATH)
    log.info("=" * 70)

    # Print failure-mode breakdown
    if invalid_rows:
        log.info("--- Invalid rows breakdown ---")
        by_mode: dict[str, list[str]] = {}
        for r in invalid_rows:
            fm = r.get("failure_mode") or "unknown"
            by_mode.setdefault(fm, []).append(r["source_row_id"])
        for fm, ids in by_mode.items():
            log.info("  %s (%d): %s", fm, len(ids), ", ".join(ids[:10]) + ("..." if len(ids) > 10 else ""))
            # Log offending fields for first few
            for r in invalid_rows:
                if r.get("failure_mode") == fm and r.get("offending_fields"):
                    log.info("    %s offending_fields: %s", r["source_row_id"], r["offending_fields"][:3])

    go_nogo = "GO" if pct >= 85 and trunc_count == 0 else "NO-GO"
    log.info("B' point estimate ≥85%%? %s (%.0f%%)", go_nogo, pct)
    return agg


if __name__ == "__main__":
    main()
