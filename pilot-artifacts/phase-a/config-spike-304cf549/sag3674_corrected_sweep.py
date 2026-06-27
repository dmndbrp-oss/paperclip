"""
SAG-3674: Corrected lever retest — top-level think:false + faff99c fold-in + N=20.

Fixes two execution defects from SAG-3673:
  1. think:false placed at TOP LEVEL of Ollama /api/chat body (not under options).
  2. faff99c cherry-picked: _repair_cross_fields now defaults availability->in_stock.

Configs measured at N=20 (all queue statuses — pending exhausted):
  A. Baseline        — max_tokens=2048, no other levers.
  B. Faithful combo  — json_object + max_tokens=4096 + temperature=0 + think:false (top-level).
  C. Ablation        — same as B but WITHOUT think:false (isolates think:false contribution).

Parse path: <think>-strip + _repair_cross_fields + validate() — matches production dispatcher.
"""
import os, sys, json, re, time
sys.path.insert(0, os.path.join(os.getcwd(), "enrichment"))
sys.path.insert(0, os.path.join(os.getcwd(), "pilot-artifacts"))
import httpx, psycopg2
from dispatcher import _build_enrichment_messages, _repair_cross_fields, PRIMARY_MODEL
from validator import validate

LITELLM_BASE = os.environ.get("LITELLM_BASE_URL", "http://localhost:4000")
LITELLM_KEY  = os.environ.get("LITELLM_API_KEY", "")
OLLAMA_BASE  = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
DB_URL       = os.environ["DATABASE_URL"]
N            = int(os.environ.get("SPIKE_N", "20"))
# gemma4 Ollama model name (LiteLLM alias → this)
OLLAMA_MODEL = "gemma4:26b-a4b-it-q4_K_M"

ARTIFACT_DIR = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def fetch_rows(n):
    """Pull N rows from all statuses (pending exhausted — measurement only, no writes)."""
    c = psycopg2.connect(DB_URL)
    cur = c.cursor()
    cur.execute(
        "SELECT source_row_id, payload_json "
        "FROM enrichment_staging.enrichment_queue "
        "ORDER BY ctid LIMIT %s",
        (n,),
    )
    rows = [
        (r[0], r[1] if isinstance(r[1], dict) else json.loads(r[1]))
        for r in cur.fetchall()
    ]
    c.close()
    return rows


# ---------------------------------------------------------------------------
# Parse helpers — match production dispatcher path
# ---------------------------------------------------------------------------

def _dispatcher_parse(raw_content):
    """Apply the same post-processing as dispatcher._litellm_complete."""
    content = raw_content or ""
    # Strip thinking tags (qwen3/gemma4 leak) — mirrors dispatcher line
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
    # Strip markdown JSON fences
    content = re.sub(r"^```(?:json)?\s*", "", content).rstrip("`").strip()
    return content


def _score(raw_content):
    """
    Parse + repair + validate using production dispatcher path.
    Returns (valid: bool|None, errors: list, parsed: dict|None, mode: str).
    mode is one of: valid, truncated, missing_fields, other_invalid, empty.
    """
    content = _dispatcher_parse(raw_content)
    if not content:
        return None, [], None, "empty"
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as e:
        return False, [f"json_parse_error: {e}"], None, "truncated"
    _repair_cross_fields(parsed)
    v = validate(parsed)
    if v.get("valid"):
        return True, [], parsed, "valid"
    errs = v.get("errors") or []
    if any("missing_required" in str(e) or "null_required" in str(e) for e in errs):
        return False, errs, parsed, "missing_fields"
    return False, errs, parsed, "other_invalid"


# ---------------------------------------------------------------------------
# LiteLLM call (OpenAI-compatible)
# ---------------------------------------------------------------------------

def litellm_call(system, user, *, max_tokens=2048, response_format=None,
                 temperature=None, timeout=300):
    body = {
        "model": PRIMARY_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
    }
    if response_format is not None:
        body["response_format"] = response_format
    if temperature is not None:
        body["temperature"] = temperature
    headers = {"Authorization": f"Bearer {LITELLM_KEY}"} if LITELLM_KEY else {}
    t = time.monotonic()
    try:
        r = httpx.post(
            f"{LITELLM_BASE}/v1/chat/completions",
            headers=headers, json=body, timeout=timeout,
        )
        dt = time.monotonic() - t
        if r.status_code != 200:
            return None, r.status_code, dt, {}
        data = r.json()
        msg = data["choices"][0]["message"]
        content = msg.get("content") or ""
        finish = data["choices"][0].get("finish_reason", "")
        usage = data.get("usage", {})
        return content, 200, dt, {"finish": finish, "compl_tokens": usage.get("completion_tokens")}
    except Exception as e:
        return None, 0, time.monotonic() - t, {"error": str(e)[:80]}


# ---------------------------------------------------------------------------
# Ollama direct call  — top-level think:false goes HERE, not under options
# ---------------------------------------------------------------------------

def ollama_call(system, user, *, max_tokens=4096, temperature=0,
                think=None, json_format=True, timeout=300):
    body = {
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "options": {
            "num_predict": max_tokens,
            "temperature": temperature,
        },
    }
    if json_format:
        body["format"] = "json"
    if think is not None:
        body["think"] = think  # TOP-LEVEL — Ollama API requirement
    t = time.monotonic()
    try:
        r = httpx.post(f"{OLLAMA_BASE}/api/chat", json=body, timeout=timeout)
        dt = time.monotonic() - t
        if r.status_code != 200:
            return None, r.status_code, dt, {}
        data = r.json()
        msg = data.get("message", {})
        content = msg.get("content") or ""
        thinking = msg.get("thinking") or ""
        finish = data.get("done_reason", "")
        eval_count = data.get("eval_count")
        return content, 200, dt, {
            "finish": finish,
            "compl_tokens": eval_count,
            "thinking_len": len(thinking),
        }
    except Exception as e:
        return None, 0, time.monotonic() - t, {"error": str(e)[:80]}


# ---------------------------------------------------------------------------
# Single-row sweep helper
# ---------------------------------------------------------------------------

def sweep_rows(rows, call_fn, label):
    valid = 0
    modes = {k: 0 for k in ("valid", "truncated", "missing_fields", "other_invalid", "empty", "http_err")}
    details = []
    for sid, pj in rows:
        system, user = _build_enrichment_messages(pj)
        content, http_status, dt, meta = call_fn(system, user)
        if http_status != 200:
            modes["http_err"] += 1
            details.append({"row": sid, "valid": None, "mode": "http_err",
                             "http_status": http_status, "latency_s": round(dt, 2),
                             "errors": [f"HTTP {http_status}"], **meta})
            print(f"  {sid}: HTTP_ERR {http_status} {dt:.1f}s")
            continue
        is_valid, errs, _, mode = _score(content)
        if is_valid:
            valid += 1
        modes[mode] += 1
        row_info = {
            "row": sid, "valid": is_valid, "mode": mode,
            "content_len": len(content or ""), "latency_s": round(dt, 2),
            "errors": errs[:3], **meta,
        }
        details.append(row_info)
        extra = ""
        if meta.get("thinking_len"):
            extra = f" thinking={meta['thinking_len']}"
        print(f"  {sid}: {mode.upper()} {dt:.1f}s{extra} compl={meta.get('compl_tokens')}")
    n = len(rows)
    pct = 100 * valid / n if n else 0
    print(f"  >> {label}: {valid}/{n} = {pct:.0f}% schema-valid | modes={dict((k,v) for k,v in modes.items() if v)}")
    return valid, n, pct, modes, details


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

rows = fetch_rows(N)
print(f"SAG-3674 corrected sweep | faff99c applied | N={len(rows)} (all statuses) | model={PRIMARY_MODEL}")
print(f"Parse path: <think>-strip + _repair_cross_fields + validate()")
print(f"OLLAMA_BASE={OLLAMA_BASE}")
print()

if not rows:
    print("ERROR: no rows found in enrichment_staging.enrichment_queue"); sys.exit(1)


# ===========================================================================
# PROBE — verify think:false suppresses thinking (1 row, Ollama direct)
# ===========================================================================
probe_sid, probe_pj = rows[0]
probe_system, probe_user = _build_enrichment_messages(probe_pj)
print("=" * 70)
print(f"PROBE — verify top-level think:false suppresses thinking (row={probe_sid})")
print("=" * 70)

# Probe WITHOUT think:false (control)
content_ctl, sc_ctl, dt_ctl, meta_ctl = ollama_call(
    probe_system, probe_user, max_tokens=4096, temperature=0,
    think=None, json_format=True, timeout=300,
)
print(f"[control — no think param] HTTP {sc_ctl} {dt_ctl:.1f}s")
print(f"  done_reason={meta_ctl.get('finish')!r} compl_tokens={meta_ctl.get('compl_tokens')}")
print(f"  thinking_len={meta_ctl.get('thinking_len')} (>0 = thinking active)")
print(f"  content_len={len(content_ctl or '')}")
v_ctl, errs_ctl, _, mode_ctl = _score(content_ctl)
print(f"  valid={v_ctl} mode={mode_ctl} errors={errs_ctl[:2]}")
print()

# Probe WITH think:false at top level
content_tf, sc_tf, dt_tf, meta_tf = ollama_call(
    probe_system, probe_user, max_tokens=4096, temperature=0,
    think=False, json_format=True, timeout=300,
)
print(f"[think:false at top level] HTTP {sc_tf} {dt_tf:.1f}s")
print(f"  done_reason={meta_tf.get('finish')!r} (should be 'stop')")
print(f"  compl_tokens={meta_tf.get('compl_tokens')}")
print(f"  thinking_len={meta_tf.get('thinking_len')} (should be ~0)")
print(f"  content_len={len(content_tf or '')}")
v_tf, errs_tf, _, mode_tf = _score(content_tf)
print(f"  valid={v_tf} mode={mode_tf} errors={errs_tf[:2]}")

think_suppressed = (meta_tf.get("thinking_len") or 0) < 10 and meta_tf.get("finish") == "stop"
print(f"\nPROBE RESULT: think:false suppression {'CONFIRMED' if think_suppressed else 'NOT CONFIRMED'}")
if not think_suppressed:
    print("WARNING: think:false top-level did NOT suppress thinking — reporting honestly, sweep continues")
print()

probe_artifact = {
    "control": {"done_reason": meta_ctl.get("finish"), "thinking_len": meta_ctl.get("thinking_len"),
                "compl_tokens": meta_ctl.get("compl_tokens"), "content_len": len(content_ctl or ""),
                "valid": v_ctl, "mode": mode_ctl},
    "think_false": {"done_reason": meta_tf.get("finish"), "thinking_len": meta_tf.get("thinking_len"),
                    "compl_tokens": meta_tf.get("compl_tokens"), "content_len": len(content_tf or ""),
                    "valid": v_tf, "mode": mode_tf},
    "suppression_confirmed": think_suppressed,
}


# ===========================================================================
# CONFIG A — Baseline (max_tokens=2048, LiteLLM, no levers)
# ===========================================================================
print("=" * 70)
print(f"CONFIG A — Baseline (LiteLLM, max_tokens=2048) | N={len(rows)}")
print("=" * 70)
t_a = time.monotonic()
valid_a, n_a, pct_a, modes_a, details_a = sweep_rows(
    rows,
    lambda s, u: litellm_call(s, u, max_tokens=2048),
    label="A baseline",
)
lat_a = time.monotonic() - t_a
print()


# ===========================================================================
# CONFIG B — Full faithful combo (Ollama direct, think:false top-level)
# ===========================================================================
print("=" * 70)
print(f"CONFIG B — json_object + max_tokens=4096 + temp=0 + think:false (top-level) | N={len(rows)}")
print("=" * 70)
t_b = time.monotonic()
valid_b, n_b, pct_b, modes_b, details_b = sweep_rows(
    rows,
    lambda s, u: ollama_call(s, u, max_tokens=4096, temperature=0,
                             think=False, json_format=True),
    label="B full combo",
)
lat_b = time.monotonic() - t_b
print()


# ===========================================================================
# CONFIG C — Ablation (same as B, but think:false removed)
# ===========================================================================
print("=" * 70)
print(f"CONFIG C — Ablation: json_object + max_tokens=4096 + temp=0 (no think:false) | N={len(rows)}")
print("=" * 70)
t_c = time.monotonic()
valid_c, n_c, pct_c, modes_c, details_c = sweep_rows(
    rows,
    lambda s, u: ollama_call(s, u, max_tokens=4096, temperature=0,
                             think=None, json_format=True),
    label="C ablation",
)
lat_c = time.monotonic() - t_c
print()


# ===========================================================================
# SUMMARY TABLE
# ===========================================================================
print("=" * 70)
print("SUMMARY TABLE")
print("=" * 70)
print(f"{'Config':<10} {'Description':<55} {'N':>4} {'Valid%':>8}")
print("-" * 80)
desc_a = "Baseline (LiteLLM, max_tokens=2048)"
desc_b = "json_object+4096+temp=0+think:false@top-level (Ollama direct)"
desc_c = "json_object+4096+temp=0, no think:false (ablation)"
print(f"{'A':<10} {desc_a:<55} {n_a:>4} {pct_a:>7.0f}%")
print(f"{'B':<10} {desc_b:<55} {n_b:>4} {pct_b:>7.0f}%")
print(f"{'C':<10} {desc_c:<55} {n_c:>4} {pct_c:>7.0f}%")
print()
print(f"think:false suppression: {'CONFIRMED' if think_suppressed else 'NOT CONFIRMED (see probe section)'}")
print(f"faff99c (availability null repair): APPLIED (cherry-picked)")
go_no = "YES" if pct_b >= 85 else "NO"
print(f"\nDoes config B move primary materially toward >=85%? {go_no} ({pct_b:.0f}%)")
print()
print(f"Baseline (A): {pct_a:.0f}%  |  Full combo (B): {pct_b:.0f}%  |  Ablation (C): {pct_c:.0f}%")


# ===========================================================================
# ARTIFACT SAVE
# ===========================================================================
artifact = {
    "meta": {
        "issue": "SAG-3674",
        "parent": "SAG-3671",
        "N": N,
        "model": PRIMARY_MODEL,
        "ollama_model": OLLAMA_MODEL,
        "faff99c_applied": True,
        "think_suppression_confirmed": think_suppressed,
    },
    "probe": probe_artifact,
    "A_baseline": {"valid": valid_a, "n": n_a, "pct": round(pct_a, 1),
                   "modes": modes_a, "rows": details_a},
    "B_full_combo": {"valid": valid_b, "n": n_b, "pct": round(pct_b, 1),
                     "modes": modes_b, "rows": details_b},
    "C_ablation": {"valid": valid_c, "n": n_c, "pct": round(pct_c, 1),
                   "modes": modes_c, "rows": details_c},
}
out_path = os.path.join(ARTIFACT_DIR, "sag3674_corrected_results.json")
with open(out_path, "w") as f:
    json.dump(artifact, f, indent=2, default=str)
print(f"Artifact written to {out_path}")
