"""
SAG-3674 corrected lever sweep.
- faff99c applied (availability=null repair active)
- think:false at TOP LEVEL of request body (not under options)
- N=20 from all queue statuses
- 3 configs: A (baseline), B (full combo), C (B minus think:false)
- Uses actual dispatcher parse path (_repair_cross_fields + validate)
"""
import os, sys, json, time, re
sys.path.insert(0, os.path.join(os.getcwd(), "enrichment"))
sys.path.insert(0, os.path.join(os.getcwd(), "pilot-artifacts"))
import httpx, psycopg2
from dispatcher import _build_enrichment_messages, _repair_cross_fields, PRIMARY_MODEL
from validator import validate

BASE = os.environ.get("LITELLM_BASE_URL", "http://localhost:4000")
KEY  = os.environ.get("LITELLM_API_KEY", "")
DB   = os.environ["DATABASE_URL"]
OLLAMA = "http://localhost:11434"
OLLAMA_MODEL = "gemma4:26b-a4b-it-q4_K_M"
N = int(os.environ.get("SPIKE_N", "20"))

ARTIFACT_DIR = os.path.join(os.getcwd(), "pilot-artifacts/phase-a/config-spike-304cf549")

# ---- DB: fetch N rows across all statuses (read-only; no writes) -----------
def fetch_rows(n):
    c = psycopg2.connect(DB)
    cur = c.cursor()
    cur.execute(
        "SELECT source_row_id, payload_json FROM enrichment_staging.enrichment_queue "
        "ORDER BY ctid LIMIT %s", (n,)
    )
    rows = [(r[0], r[1] if isinstance(r[1], dict) else json.loads(r[1]))
            for r in cur.fetchall()]
    c.close()
    return rows

# ---- LiteLLM call via /v1/chat/completions ---------------------------------
def litellm_call(system, user, *, response_format=None, max_tokens=2048,
                 temperature=None, think=None, timeout=300):
    body = {
        "model": PRIMARY_MODEL,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "max_tokens": max_tokens,
    }
    if response_format is not None:
        body["response_format"] = response_format
    if temperature is not None:
        body["temperature"] = temperature
    if think is not None:
        body["think"] = think  # TOP-LEVEL — not under options
    headers = {"Authorization": f"Bearer {KEY}"} if KEY else {}
    t = time.monotonic()
    try:
        r = httpx.post(f"{BASE}/v1/chat/completions", headers=headers, json=body, timeout=timeout)
        dt = time.monotonic() - t
        if r.status_code != 200:
            return r.status_code, None, None, None, dt
        data = r.json()
        msg = data["choices"][0]["message"]
        content = msg.get("content") or ""
        # Dispatcher parse path: strip think tags then markdown fences
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
        content = re.sub(r"^```(?:json)?\s*", "", content).rstrip("`").strip()
        finish = data["choices"][0].get("finish_reason")
        compl_tokens = data.get("usage", {}).get("completion_tokens")
        return r.status_code, content, finish, compl_tokens, dt
    except Exception as e:
        return 0, None, "error", None, time.monotonic() - t

# ---- Direct Ollama call (probe only) ---------------------------------------
def ollama_call(system, user, *, max_tokens=2048, think=None, timeout=300):
    body = {
        "model": OLLAMA_MODEL,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "stream": False,
        "options": {"num_predict": max_tokens},
    }
    if think is not None:
        body["think"] = think  # TOP-LEVEL per Ollama /api/chat spec
    t = time.monotonic()
    try:
        r = httpx.post(f"{OLLAMA}/api/chat", json=body, timeout=timeout)
        dt = time.monotonic() - t
        return r.status_code, r.json(), dt
    except Exception as e:
        return 0, str(e), time.monotonic() - t

# ---- Schema validation via dispatcher parse path ---------------------------
def try_parse_and_validate(content):
    """Apply dispatcher's actual parse path: strip think tags, strip fences, parse, repair, validate."""
    if not content:
        return False, ["empty_content"], None
    try:
        parsed = json.loads(content)
        _repair_cross_fields(parsed)
        v = validate(parsed)
        return bool(v.get("valid")), v.get("errors", []), parsed
    except json.JSONDecodeError as e:
        return False, [f"json_parse_error: {str(e)[:80]}"], None
    except Exception as e:
        return False, [f"validate_error: {str(e)[:80]}"], None

# ---- Probe: verify think:false suppresses thinking -------------------------
print(f"\n{'='*70}")
print(f"PROBE — verify top-level think:false suppresses gemma4 thinking")
print(f"{'='*70}\n")

rows = fetch_rows(N)
if not rows:
    print("ERROR: No rows in enrichment_staging.enrichment_queue")
    sys.exit(1)
print(f"Fetched {len(rows)} rows from all statuses (read-only)\n")

probe_id, probe_payload = rows[0]
sys_p, usr_p = _build_enrichment_messages(probe_payload)

# Probe A: baseline (no think:false) — confirm thinking present
print(f"[Probe baseline — no think:false] row={probe_id}")
sc, content, finish, compl, dt = litellm_call(sys_p, usr_p, max_tokens=2048, timeout=120)
print(f"  HTTP {sc} {dt:.1f}s  finish={finish}  compl_tokens={compl}")
print(f"  content_len={len(content or '')}  empty={(not content)}")
baseline_thinking_present = (not content) or compl == 2048
print(f"  thinking_budget_exhausted={baseline_thinking_present}")

# Probe B: top-level think:false via Ollama direct
print(f"\n[Probe think:false — Ollama direct /api/chat] row={probe_id}")
sc_o, resp_o, dt_o = ollama_call(sys_p, usr_p, max_tokens=2048, think=False, timeout=180)
print(f"  HTTP {sc_o} {dt_o:.1f}s")
think_suppressed = False
probe_results = {}
if isinstance(resp_o, dict):
    msg_o = resp_o.get("message", {})
    content_o = msg_o.get("content") or ""
    thinking_o = msg_o.get("thinking") or ""
    done_reason = resp_o.get("done_reason")
    eval_count = resp_o.get("eval_count")
    print(f"  done_reason={done_reason!r}  eval_count={eval_count}")
    print(f"  thinking_len={len(thinking_o)}  content_len={len(content_o)}")
    print(f"  content_sample={repr(content_o[:200])}")
    think_suppressed = len(thinking_o) == 0 and done_reason != "length"
    print(f"  >> think_suppressed={think_suppressed}")
    probe_results = {
        "done_reason": done_reason, "eval_count": eval_count,
        "thinking_len": len(thinking_o), "content_len": len(content_o),
        "think_suppressed": think_suppressed,
    }
else:
    print(f"  raw: {str(resp_o)[:300]}")

print(f"\nProbe summary: think:false at top-level → suppresses_thinking={think_suppressed}")

# ============================================================================
# N=20 SWEEP — 3 configs via LiteLLM (real dispatcher parse path)
# ============================================================================
print(f"\n{'='*70}")
print(f"N=20 SWEEP — 3 configs via LiteLLM + dispatcher parse path")
print(f"faff99c applied: _repair_cross_fields includes availability=null→in_stock")
print(f"{'='*70}\n")

CONFIGS = [
    ("A_baseline",
     dict(max_tokens=2048),
     "max_tokens=2048, no other levers (anchor vs 3% from abf6b813)"),
    ("B_full_combo",
     dict(max_tokens=4096, response_format={"type": "json_object"}, temperature=0, think=False),
     "json_object + max_tokens=4096 + temperature=0 + think:false(top-level) + faff99c"),
    ("C_combo_no_think",
     dict(max_tokens=4096, response_format={"type": "json_object"}, temperature=0),
     "json_object + max_tokens=4096 + temperature=0 + faff99c (NO think:false — ablation)"),
]

sweep_results = {}

for cfg_name, kwargs, description in CONFIGS:
    print(f"\n--- {cfg_name} ---")
    print(f"    {description}")
    valid_count = 0
    row_details = []
    modes = {"valid": 0, "empty": 0, "truncated": 0, "missing_fields": 0, "other_invalid": 0, "http_err": 0}

    for sid, pj in rows:
        system, user = _build_enrichment_messages(pj)
        sc, content, finish, compl, dt = litellm_call(system, user, timeout=300, **kwargs)
        if sc != 200 or content is None:
            modes["http_err"] += 1
            print(f"  {sid}: HTTP_ERR {sc} {dt:.1f}s")
            row_details.append({"row": sid, "valid": False, "mode": "http_err", "latency_s": round(dt, 2)})
            continue
        if not content:
            modes["empty"] += 1
            print(f"  {sid}: EMPTY {dt:.1f}s finish={finish} compl={compl}")
            row_details.append({"row": sid, "valid": False, "mode": "empty", "finish": finish,
                                 "compl_tokens": compl, "latency_s": round(dt, 2)})
            continue
        valid, errs, parsed = try_parse_and_validate(content)
        if valid:
            valid_count += 1
            modes["valid"] += 1
            print(f"  {sid}: VALID {dt:.1f}s")
            row_details.append({"row": sid, "valid": True, "finish": finish,
                                 "compl_tokens": compl, "latency_s": round(dt, 2)})
        else:
            errs_str = str(errs[:3])
            if any("missing_required" in str(e) or "null_required" in str(e) for e in errs):
                modes["missing_fields"] += 1
            elif any("parse" in str(e) or "truncat" in str(e) for e in errs):
                modes["truncated"] += 1
            else:
                modes["other_invalid"] += 1
            print(f"  {sid}: INVALID {dt:.1f}s {errs_str}")
            row_details.append({"row": sid, "valid": False, "errors": (errs or [])[:3],
                                 "finish": finish, "compl_tokens": compl, "latency_s": round(dt, 2)})

    n = len(rows)
    rate = valid_count / n if n > 0 else 0.0
    print(f"\n  >> {cfg_name}: {valid_count}/{n} = {rate*100:.0f}% primary schema-valid")
    print(f"     failure modes: {dict((k, v) for k, v in modes.items() if v and k != 'valid')}")
    sweep_results[cfg_name] = {
        "description": description,
        "n": n,
        "valid": valid_count,
        "schema_valid_pct": round(rate * 100, 1),
        "failure_modes": modes,
        "rows": row_details,
    }

# ============================================================================
# SAVE ARTIFACTS
# ============================================================================
artifact = {
    "probe": probe_results,
    "sweep": sweep_results,
    "think_suppressed_by_top_level": think_suppressed,
    "faff99c_applied": True,
    "n": N,
    "model": PRIMARY_MODEL,
}
out_path = os.path.join(ARTIFACT_DIR, "corrected_sweep.json")
with open(out_path, "w") as f:
    json.dump(artifact, f, indent=2, default=str)
print(f"\nArtifact written to {out_path}")

# ============================================================================
# SUMMARY TABLE
# ============================================================================
print(f"\n{'='*70}")
print("SUMMARY TABLE")
print(f"{'='*70}")
print(f"{'Config':<20} {'N':>4} {'Valid%':>8}  Description")
print("-"*80)
for k, v in sweep_results.items():
    print(f"{k:<20} {v['n']:>4} {v['schema_valid_pct']:>7.0f}%  {v['description'][:55]}")
print()
print(f"think:false top-level → suppresses_thinking = {think_suppressed}")
print(f"faff99c (availability repair) = APPLIED")
print()
A_pct = sweep_results.get("A_baseline", {}).get("schema_valid_pct", 0)
B_pct = sweep_results.get("B_full_combo", {}).get("schema_valid_pct", 0)
C_pct = sweep_results.get("C_combo_no_think", {}).get("schema_valid_pct", 0)
go_nogo = "GO (proceed to full 100-row run)" if B_pct >= 85 else f"NO-GO (best={B_pct:.0f}% < 85%)"
print(f"A baseline: {A_pct:.0f}%  B full combo: {B_pct:.0f}%  C no-think: {C_pct:.0f}%")
print(f"==> GO/NO-GO: {go_nogo}")
