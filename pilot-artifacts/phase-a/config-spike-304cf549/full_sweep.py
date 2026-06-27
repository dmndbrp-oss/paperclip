"""
SAG-3673 full structured-output config probe + lever sweep.
Step 0 (mandatory probe) + Levers 1-4 + Combo.
Outputs raw JSON artifacts to config-spike-304cf549/ directory.
"""
import os, sys, json, time
sys.path.insert(0, os.path.join(os.getcwd(), "enrichment"))
sys.path.insert(0, os.path.join(os.getcwd(), "pilot-artifacts"))
import httpx, psycopg2
from dispatcher import _build_enrichment_messages, PRIMARY_MODEL
from validator import validate

BASE = os.environ.get("LITELLM_BASE_URL", "http://localhost:4000")
KEY  = os.environ.get("LITELLM_API_KEY", "")
DB   = os.environ["DATABASE_URL"]
OLLAMA = "http://localhost:11434"
# gemma4 actual Ollama model name (LiteLLM alias → this)
OLLAMA_MODEL = "gemma4:26b-a4b-it-q4_K_M"

# ---- DB: fetch pending rows deterministically --------------------------------
def fetch_rows(n=20):
    c = psycopg2.connect(DB)
    cur = c.cursor()
    cur.execute(
        "SELECT source_row_id, payload_json FROM enrichment_staging.enrichment_queue "
        "WHERE status='pending' ORDER BY id LIMIT %s", (n,)
    )
    rows = [(r[0], r[1] if isinstance(r[1], dict) else json.loads(r[1]))
            for r in cur.fetchall()]
    c.close()
    return rows

# ---- LiteLLM call wrapper ---------------------------------------------------
def litellm_call(model, system, user, *, response_format=None, max_tokens=2048,
                 temperature=None, extra=None, timeout=300):
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "max_tokens": max_tokens,
    }
    if response_format is not None:
        body["response_format"] = response_format
    if temperature is not None:
        body["temperature"] = temperature
    if extra:
        body.update(extra)
    headers = {"Authorization": f"Bearer {KEY}"} if KEY else {}
    t = time.monotonic()
    try:
        r = httpx.post(f"{BASE}/v1/chat/completions", headers=headers, json=body, timeout=timeout)
        dt = time.monotonic() - t
        if r.headers.get("content-type", "").startswith("application/json"):
            return r.status_code, r.json(), dt
        return r.status_code, r.text, dt
    except Exception as e:
        return 0, str(e), time.monotonic() - t

# ---- Direct Ollama call (for Step 0 raw response) ---------------------------
def ollama_call(model, system, user, *, max_tokens=2048, think=None, timeout=300):
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "stream": False,
        "options": {"num_predict": max_tokens},
    }
    if think is not None:
        body["think"] = think
    t = time.monotonic()
    try:
        r = httpx.post(f"{OLLAMA}/api/chat", json=body, timeout=timeout)
        dt = time.monotonic() - t
        return r.status_code, r.json(), dt
    except Exception as e:
        return 0, str(e), time.monotonic() - t

# ---- Schema validation wrapper ----------------------------------------------
def try_validate(content):
    if not content:
        return None, "empty_content"
    try:
        parsed = json.loads(content)
        v = validate(parsed)
        return v.get("valid"), v.get("errors", [])
    except json.JSONDecodeError as e:
        return False, [f"json_parse_error: {e}"]
    except Exception as e:
        return False, [f"validate_error: {e}"]

# ---- Main -------------------------------------------------------------------
rows = fetch_rows(20)
print(f"Available pending rows: {len(rows)}", flush=True)
if not rows:
    print("ERROR: No pending rows in enrichment_staging.enrichment_queue")
    sys.exit(1)

N = len(rows)
# Use first row for Step 0 probe, then all rows for lever sweep
probe_row_id, probe_payload = rows[0]
system0, user0 = _build_enrichment_messages(probe_payload)

print(f"\n{'='*70}")
print(f"STEP 0 — PROBE (mandatory baseline, row={probe_row_id})")
print(f"Model: {PRIMARY_MODEL} (Ollama: {OLLAMA_MODEL})")
print(f"{'='*70}\n")

# --- Step 0a: LiteLLM baseline (exact dispatcher config) ---
sc, resp, dt = litellm_call(PRIMARY_MODEL, system0, user0, max_tokens=2048)
print(f"[0a LiteLLM baseline] HTTP {sc} {dt:.1f}s")
if isinstance(resp, dict) and resp.get("choices"):
    msg = resp["choices"][0]["message"]
    content = msg.get("content") or ""
    finish_reason = resp["choices"][0].get("finish_reason")
    usage = resp.get("usage", {})
    print(f"  message keys: {list(msg.keys())}")
    print(f"  content: len={len(content)!r}, empty={content == ''!r}")
    print(f"  finish_reason: {finish_reason!r}")
    print(f"  usage: prompt_tokens={usage.get('prompt_tokens')}, completion_tokens={usage.get('completion_tokens')}, total={usage.get('total_tokens')}")
    for k in ("reasoning_content", "reasoning", "thinking"):
        v = msg.get(k)
        if v:
            print(f"  !! {k} present: len={len(str(v))}, sample={str(v)[:200]!r}")
    print(f"  content sample: {repr(content[:300])}")
    valid, errs = try_validate(content)
    print(f"  schema_valid: {valid}, errors: {errs[:3] if errs else []}")
else:
    print(f"  raw resp: {str(resp)[:500]}")

step0_litellm = {"row": probe_row_id, "http_status": sc, "latency_s": dt,
                 "response": resp if isinstance(resp, dict) else str(resp)[:1000]}

print()

# --- Step 0b: Direct Ollama call (full raw response) ---
print("[0b Ollama direct /api/chat]")
sc_o, resp_o, dt_o = ollama_call(OLLAMA_MODEL, system0, user0, max_tokens=2048)
print(f"  HTTP {sc_o} {dt_o:.1f}s")
if isinstance(resp_o, dict):
    msg_o = resp_o.get("message", {})
    print(f"  message keys: {list(msg_o.keys())}")
    content_o = msg_o.get("content") or ""
    thinking_o = msg_o.get("thinking") or msg_o.get("reasoning") or ""
    print(f"  content: len={len(content_o)}, empty={content_o == ''!r}")
    print(f"  done_reason: {resp_o.get('done_reason')!r}")
    print(f"  eval_count (completion tokens): {resp_o.get('eval_count')}")
    print(f"  prompt_eval_count: {resp_o.get('prompt_eval_count')}")
    if thinking_o:
        print(f"  !! thinking present: len={len(thinking_o)}, sample={thinking_o[:200]!r}")
    print(f"  content sample: {repr(content_o[:300])}")
    valid_o, errs_o = try_validate(content_o)
    print(f"  schema_valid: {valid_o}, errors: {errs_o[:3] if errs_o else []}")
else:
    print(f"  raw resp: {str(resp_o)[:500]}")

step0_ollama = {"row": probe_row_id, "http_status": sc_o, "latency_s": dt_o,
                "response": resp_o if isinstance(resp_o, dict) else str(resp_o)[:2000]}

# Write Step 0 artifact
artifact_dir = os.path.join(os.getcwd(), "pilot-artifacts/phase-a/config-spike-304cf549")
with open(os.path.join(artifact_dir, "step0_probe.json"), "w") as f:
    json.dump({"litellm": step0_litellm, "ollama": step0_ollama}, f, indent=2, default=str)
print(f"\nStep 0 artifact written to config-spike-304cf549/step0_probe.json")

# ===========================================================================
# LEVER SWEEP — N rows, each lever applied once to PRIMARY tier only
# ===========================================================================
print(f"\n{'='*70}")
print(f"LEVER SWEEP — N={N} rows (all available pending)")
print(f"Validator: pilot-artifacts/validator.py :: validate()")
print(f"{'='*70}\n")

LEVERS = [
    ("L0_baseline",      dict(max_tokens=2048)),
    ("L1_json_object",   dict(max_tokens=2048, response_format={"type": "json_object"})),
    ("L2_4k_tokens",     dict(max_tokens=4096)),
    ("L3_temp0",         dict(max_tokens=2048, temperature=0)),
    # L4: request-side think suppression via Ollama think:false passed through extra
    ("L4_think_false",   dict(max_tokens=2048, extra={"options": {"think": False}})),
    ("L_combo_1234",     dict(max_tokens=4096, response_format={"type": "json_object"},
                              temperature=0, extra={"options": {"think": False}})),
]

lever_results = {}

for lever_name, kwargs in LEVERS:
    print(f"\n--- {lever_name} ---")
    valid_count = 0
    total = 0
    row_details = []
    for row_id, payload in rows:
        system, user = _build_enrichment_messages(payload)
        extra = kwargs.pop("extra", None)
        sc, resp, dt = litellm_call(PRIMARY_MODEL, system, user, timeout=180, extra=extra, **kwargs)
        if extra:
            kwargs["extra"] = extra
        if isinstance(resp, dict) and resp.get("choices"):
            msg = resp["choices"][0]["message"]
            content = msg.get("content") or ""
            finish_reason = resp["choices"][0].get("finish_reason")
            compl_tokens = resp.get("usage", {}).get("completion_tokens")
        else:
            content = ""
            finish_reason = "error"
            compl_tokens = None

        valid, errs = try_validate(content)
        if valid:
            valid_count += 1
        total += 1
        row_details.append({
            "row": row_id, "valid": valid, "finish_reason": finish_reason,
            "completion_tokens": compl_tokens, "content_len": len(content),
            "errors": (errs or [])[:3], "latency_s": round(dt, 2)
        })
        print(f"  {row_id}: valid={valid}, finish={finish_reason}, compl_tokens={compl_tokens}, len={len(content)}, dt={dt:.1f}s")

    rate = valid_count / total if total > 0 else 0.0
    print(f"  >> {lever_name}: {valid_count}/{total} = {rate*100:.0f}% schema-valid")
    lever_results[lever_name] = {
        "config_delta": {k: v for k, v in kwargs.items() if k != "extra"},
        "n": total,
        "valid": valid_count,
        "schema_valid_pct": round(rate * 100, 1),
        "rows": row_details,
    }
    if rate >= 0.85:
        print(f"  *** REACHED >=85% threshold on {lever_name} — stopping early ***")
        break

# Write lever sweep artifact
with open(os.path.join(artifact_dir, "lever_sweep.json"), "w") as f:
    json.dump(lever_results, f, indent=2, default=str)
print(f"\nLever sweep artifact written to config-spike-304cf549/lever_sweep.json")

# Summary table
print(f"\n{'='*70}")
print("SUMMARY TABLE")
print(f"{'='*70}")
print(f"{'Lever':<20} {'Config delta':<45} {'N':>4} {'Valid%':>8}")
print("-"*80)
for k, v in lever_results.items():
    cfg = str({kk: vv for kk, vv in v['config_delta'].items()})
    print(f"{k:<20} {cfg:<45} {v['n']:>4} {v['schema_valid_pct']:>7.0f}%")
print()
print("Validator path: pilot-artifacts/validator.py :: validate()")
print(f"PRIMARY_MODEL: {PRIMARY_MODEL}")
