#!/usr/bin/env python3
"""Enrichissement ML BATCHÉ via Cerebras (gemma-4-31b) : chaque incident -> 2 mots (event_reason, log_sig).
Cerebras = riche en tokens (1M/j) mais pauvre en requetes (5/min). => on GROUPE ~20 incidents/appel
pour transformer ~9600 requetes en ~480. Multi-cles round-robin + resumable. 100% IA (pas de canonique).

env: CEREBRAS_KEYS (virgules), MODEL (gemma-4-31b), SRC (sft_120_sub.jsonl), OUT (sft_120_ml.jsonl),
     BATCH (20), MAXN (20000)
"""
import os, re, json, time, urllib.request, urllib.error
D=os.path.dirname(os.path.abspath(__file__))
KEYS=[k.strip() for k in os.environ["CEREBRAS_KEYS"].split(",") if k.strip()]
MODEL=os.environ.get("MODEL","gemma-4-31b")
SRC=os.path.join(D, os.environ.get("SRC","sft_120_sub.jsonl"))
OUT=os.path.join(D, os.environ.get("OUT","sft_120_ml.jsonl"))
BATCH=int(os.environ.get("BATCH","20"))
MAXN=int(os.environ.get("MAXN","20000"))
URL="https://api.cerebras.ai/v1/chat/completions"
ki=[0]
def next_key():
    k=KEYS[ki[0]%len(KEYS)]; ki[0]+=1; return k

# mapping incident -> TYPE
_s=open(os.path.join(D,"gen_120_subcases.py"),encoding="utf-8").read()
_g={"__file__":os.path.join(D,"gen_120_subcases.py")}; exec(_s.split("WRAP=")[0], _g); S=_g["S"]
def _pat(sym):
    p=re.escape(sym)
    for ph in [r"\{app\}",r"\{ns\}",r"\{node\}",r"\{db\}",r"\{cluster\}"]: p=p.replace(ph,".+?")
    return re.compile(p.replace(r"\{n\}",r"\d+"))
PATS=[(t,_pat(sym)) for t,subs in S.items() for (sym,_,_) in subs]
def type_of(inc):
    for t,rx in PATS:
        if rx.search(inc): return t
    return "UNKNOWN"

items=[]
for l in open(SRC,encoding="utf-8"):
    inc=json.loads(l)["messages"][1]["content"]
    core=inc.replace("Incident: ","").replace(" Decide the remediation action.","").strip()
    items.append((core, type_of(inc)))

SYS=("You generate REALISTIC ML monitoring features for a Kubernetes classifier. For EACH incident, output "
     "features CONSISTENT with the incident and its type. Fields per incident:\n"
     '{"event_reason":"<real k8s Event Reason or None>","log_sig":"<snake_case signature specific to it>",'
     '"cpu_cores":<float 0-4>,"memory_mb":<float 20-4000>,"pod_restarts":<int 0-30>,'
     '"unavailable_replicas":<int 0-10>,"latency_p95_s":<float 0-15>,"http_5xx_ratio":<float 0-1>,'
     '"node_not_ready":<int 0-3>,"db_hits":<int 0-20>,"log_lines":<int 0-500>,"error_lines":<int 0-200>,'
     '"warning_lines":<int 0-100>}\n'
     "Numbers MUST reflect THIS incident: OOM->high memory_mb + some pod_restarts; CPU stress->high cpu_cores; "
     "crashloop->high pod_restarts + unavailable_replicas; latency/perf->high latency_p95_s; 5xx->high "
     "http_5xx_ratio; node down/pressure->node_not_ready>=1; DB->db_hits high; security/CVE/cert->mostly "
     "NORMAL metrics (the signal is the event/log, not the numbers). "
     'Respond ONLY with JSON {"items":[{...}, ...]} EXACTLY one object per incident, IN THE SAME ORDER.')

NUM={"cpu_cores":(0.0,4.0),"memory_mb":(20.0,4000.0),"pod_restarts":(0,30),"unavailable_replicas":(0,10),
     "latency_p95_s":(0.0,15.0),"http_5xx_ratio":(0.0,1.0),"node_not_ready":(0,3),"db_hits":(0,20),
     "log_lines":(0,500),"error_lines":(0,200),"warning_lines":(0,100)}
def _clip(k,v):
    lo,hi=NUM[k]
    try: x=float(v)
    except Exception: x=0.0
    x=max(lo,min(hi,x))
    return int(round(x)) if isinstance(lo,int) else round(x,3)
def clean(o):
    if not isinstance(o,dict): o={}
    r={"event_reason": str(o.get("event_reason","None"))[:40] or "None",
       "log_sig": re.sub(r"[^a-z0-9_]","",str(o.get("log_sig","unknown")).lower().replace(" ","_"))[:40] or "unknown"}
    for k in NUM: r[k]=_clip(k,o.get(k,0))
    return r

def api(msgs, max_tokens):
    body=json.dumps({"model":MODEL,"max_tokens":max_tokens,"temperature":0.5,
                     "response_format":{"type":"json_object"},"messages":msgs}).encode()
    for att in range(40):
        key=next_key()
        req=urllib.request.Request(URL,data=body,headers={"Authorization":"Bearer "+key,
            "Content-Type":"application/json","User-Agent":"Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req,timeout=90) as r:
                d=json.loads(r.read())
            return json.loads(d["choices"][0]["message"]["content"])
        except urllib.error.HTTPError as e:
            if e.code==429: time.sleep(6); continue   # per-min limit -> autre cle / attente
            time.sleep(2)
        except Exception:
            time.sleep(2)
    return None

def do_batch(batch):
    """batch = liste de (inc, typ). Retourne liste de dict {event_reason,log_sig} alignee, ou None."""
    lines="\n".join(f"{i+1}. [{typ}] {inc}" for i,(inc,typ) in enumerate(batch))
    o=api([{"role":"system","content":SYS},{"role":"user","content":"Incidents:\n"+lines}], 160*len(batch)+250)
    if (o and isinstance(o.get("items"),list) and len(o["items"])==len(batch)
            and all(isinstance(x,dict) for x in o["items"])):
        return [clean(x) for x in o["items"]]
    return None

def do_single(inc, typ):
    o=api([{"role":"system","content":SYS},
           {"role":"user","content":"Incidents:\n1. ["+typ+"] "+inc}], 120)
    if o and isinstance(o.get("items"),list) and o["items"]:
        return clean(o["items"][0])
    return clean(o if isinstance(o,dict) else {})

def main():
    start=sum(1 for _ in open(OUT,encoding="utf-8")) if os.path.exists(OUT) else 0
    todo=items[start:start+MAXN]
    print(f"=== Cerebras/{MODEL} BATCH={BATCH} | {len(KEYS)} cles | a faire={len(todo)} (depuis {start}) ===",flush=True)
    done=start
    with open(OUT,"a" if start else "w",encoding="utf-8") as f:
        for b in range(0,len(todo),BATCH):
            batch=todo[b:b+BATCH]
            res=do_batch(batch)
            if res is None:  # fallback par incident (garde 100% IA, jamais canonique)
                res=[do_single(inc,typ) for inc,typ in batch]
            for (inc,typ),r in zip(batch,res):
                f.write(json.dumps({"type":typ,"incident":inc,**r},ensure_ascii=False)+"\n")
            f.flush(); done+=len(batch)
            print(f"  {done}/12000  (+{done-start})",flush=True)
    print(f"\nFINI: {done}/12000 -> {OUT}",flush=True)

if __name__=="__main__": main()
