import os, sys, json, time
sys.path.insert(0, os.path.join(os.getcwd(), "enrichment"))
sys.path.insert(0, os.path.join(os.getcwd(), "pilot-artifacts"))
import httpx, psycopg2
from dispatcher import _build_enrichment_messages, PRIMARY_MODEL
from validator import validate
BASE=os.environ["LITELLM_BASE_URL"]; KEY=os.environ["LITELLM_API_KEY"]; DB=os.environ["DATABASE_URL"]
c=psycopg2.connect(DB);cur=c.cursor()
cur.execute("select source_row_id,payload_json from enrichment_staging.enrichment_queue where status='pending' limit 1")
sid,pj=cur.fetchone(); pj=pj if isinstance(pj,dict) else json.loads(pj); c.close()
system,user=_build_enrichment_messages(pj)
def run(mt):
    body={"model":PRIMARY_MODEL,"messages":[{"role":"system","content":system},{"role":"user","content":user}],
          "max_tokens":mt,"response_format":{"type":"json_object"},"temperature":0}
    t=time.monotonic();r=httpx.post(f"{BASE}/v1/chat/completions",headers={"Authorization":f"Bearer {KEY}"},json=body,timeout=240);dt=time.monotonic()-t
    d=r.json();msg=d["choices"][0]["message"];content=msg.get("content") or ""
    fr=d["choices"][0]["finish_reason"];ct=d["usage"]["completion_tokens"]
    out=f"max_tokens={mt}: HTTP {r.status_code} {dt:.1f}s finish={fr} compl_tokens={ct} content_len={len(content)}"
    try:
        p=json.loads(content);v=validate(p)
        out+=f" | PARSE OK | valid={v.get('valid')} errors={v.get('errors')}"
    except Exception as e:
        out+=f" | PARSE FAIL: {e}"
    print(out); return
print(f"row {sid}, response_format=json_object, temp=0")
run(4096)
run(8192)
