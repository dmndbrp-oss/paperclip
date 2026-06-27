"""SAG-2154 / 304cf549 primary-tier config spike — gemma4 structured-output probe.
Reuses the EXACT enrichment prompt + validator from dispatcher.py. Read-only on DB.
"""
import os, sys, json, time
sys.path.insert(0, os.path.join(os.getcwd(), "enrichment"))
sys.path.insert(0, os.path.join(os.getcwd(), "pilot-artifacts"))
import httpx, psycopg2
from dispatcher import _build_enrichment_messages, PRIMARY_MODEL
from validator import validate

BASE = os.environ.get("LITELLM_BASE_URL", "http://localhost:4000")
KEY  = os.environ.get("LITELLM_API_KEY", "")
DB   = os.environ.get("DATABASE_URL")

def fetch_rows(n):
    c=psycopg2.connect(DB); cur=c.cursor()
    cur.execute("select source_row_id, payload_json from enrichment_staging.enrichment_queue where status='pending' limit %s",(n,))
    rows=[(r[0], r[1] if isinstance(r[1],dict) else json.loads(r[1])) for r in cur.fetchall()]
    c.close(); return rows

def call(model, system, user, *, response_format=None, max_tokens=2048, temperature=None, extra=None):
    body={"model":model,"messages":[{"role":"system","content":system},{"role":"user","content":user}],"max_tokens":max_tokens}
    if response_format is not None: body["response_format"]=response_format
    if temperature is not None: body["temperature"]=temperature
    if extra: body.update(extra)
    t=time.monotonic()
    r=httpx.post(f"{BASE}/v1/chat/completions",headers={"Authorization":f"Bearer {KEY}"},json=body,timeout=120)
    dt=time.monotonic()-t
    return r.status_code, (r.json() if r.headers.get('content-type','').startswith('application/json') else r.text), dt

rows = fetch_rows(1)
sid, payload = rows[0]
system, user = _build_enrichment_messages(payload)
print(f"=== ROW {sid} | model={PRIMARY_MODEL} ===\n")

# Lever 0: BASELINE (current dispatcher config) — dump FULL response structure
sc, resp, dt = call(PRIMARY_MODEL, system, user)
print(f"[0 BASELINE no-response_format max_tokens=2048] HTTP {sc} {dt:.1f}s")
msg = resp["choices"][0]["message"] if isinstance(resp,dict) and resp.get("choices") else {}
print("  message keys:", list(msg.keys()))
print("  content len:", len(msg.get("content") or ""))
print("  finish_reason:", resp["choices"][0].get("finish_reason") if isinstance(resp,dict) and resp.get("choices") else "?")
print("  usage:", resp.get("usage") if isinstance(resp,dict) else "?")
for k in ("reasoning_content","reasoning","thinking"):
    if msg.get(k): print(f"  !! reasoning in message['{k}'] len={len(str(msg[k]))} sample={str(msg[k])[:150]!r}")
print("  content sample:", repr((msg.get("content") or "")[:200]))
print()

# Lever 1: response_format = json_object
sc, resp, dt = call(PRIMARY_MODEL, system, user, response_format={"type":"json_object"})
msg = resp["choices"][0]["message"] if isinstance(resp,dict) and resp.get("choices") else {}
content = msg.get("content") or ""
print(f"[1 response_format=json_object max_tokens=2048] HTTP {sc} {dt:.1f}s content_len={len(content)}")
try:
    parsed=json.loads(content); v=validate(parsed)
    print("  JSON parse: OK | validator valid:", v.get("valid"), "| errors:", v.get("errors"))
except Exception as e:
    print("  JSON parse FAILED:", e, "| content sample:", repr(content[:200]))
