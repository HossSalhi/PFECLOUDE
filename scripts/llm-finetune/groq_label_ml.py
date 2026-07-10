#!/usr/bin/env python3
"""Enrichissement Groq MULTI-CLES pour le ML : chaque incident (texte) -> LIGNE DE FEATURES ML.
Base = sft_120_sub.jsonl (12000 incidents, SANS action/explication). Pour chaque incident + son TYPE
(connu par mapping regex des symptomes), Groq produit des features monitoring REALISTES ancrees dans
l'incident : event_reason + log_sig + metriques (cpu/mem/restarts/unavail/latence/5xx/node/db/logs).
-> le ML apprend a classer le TYPE (120) a partir de ces signaux (comme en live: Camunda detecte,
l'IA classe/resout). Sortie sft_120_ml.jsonl. Multi-cles + resumable + rotation TPD.

env: GROQ_API_KEYS (virgules), GROQ_MODEL (llama-3.1-8b-instant), SRC (sft_120_sub.jsonl),
     LABEL_OUT (sft_120_ml.jsonl), LABEL_N (20000), LABEL_WORKERS (6).
"""
import os, re, json, time, threading, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor
D=os.path.dirname(os.path.abspath(__file__))
KEYS=[k.strip() for k in os.environ.get("GROQ_API_KEYS", os.environ.get("GROQ_API_KEY","")).split(",") if k.strip()]
assert KEYS, "GROQ_API_KEYS vide"
MODEL=os.environ.get("GROQ_MODEL","llama-3.1-8b-instant")
N=int(os.environ.get("LABEL_N","20000"))
SRC=os.path.join(D, os.environ.get("SRC","sft_120_sub.jsonl"))
OUTFILE=os.path.join(D, os.environ.get("LABEL_OUT","sft_120_ml.jsonl"))
WORKERS=int(os.environ.get("LABEL_WORKERS","6"))
# Endpoint générique compatible OpenAI. Groq par défaut ; Gemini = AI Studio (compat OpenAI) :
#   API_URL=https://generativelanguage.googleapis.com/v1beta/openai/chat/completions  GROQ_MODEL=gemini-2.0-flash
URL=os.environ.get("API_URL","https://api.groq.com/openai/v1/chat/completions")

# --- rotation de cles PARALLELE round-robin (comme groq_label_multi) ---
klock=threading.Lock(); available=list(range(len(KEYS))); rr=[0]
def get_key():
    with klock:
        if not available: return None,-1
        idx=available[rr[0]%len(available)]; rr[0]+=1
        return KEYS[idx],idx
def mark_tpd(idx):
    with klock:
        if idx in available:
            available.remove(idx); print(f"  >> cle #{idx+1} TPD epuise -> retiree ({len(available)} restante(s))",flush=True)
        return len(available)>0
def reset_all():
    with klock: available[:]=list(range(len(KEYS)))

# --- mapping incident -> TYPE (via symptomes du generateur LLM) ---
_src=open(os.path.join(D,"gen_120_subcases.py"),encoding="utf-8").read()
_g={"__file__":os.path.join(D,"gen_120_subcases.py")}; exec(_src.split("WRAP=")[0], _g)
S=_g["S"]
def _pat(sym):
    p=re.escape(sym)
    for ph in [r"\{app\}",r"\{ns\}",r"\{node\}",r"\{db\}",r"\{cluster\}"]: p=p.replace(ph,".+?")
    return re.compile(p.replace(r"\{n\}",r"\d+"))
PATS=[(t,_pat(sym)) for t,subs in S.items() for (sym,_,_) in subs]
def type_of(inc):
    for t,rx in PATS:
        if rx.search(inc): return t
    return None

# charger (incident, TYPE)
items=[]
for l in open(SRC,encoding="utf-8"):
    inc=json.loads(l)["messages"][1]["content"]
    core=inc.replace("Incident: ","").replace(" Decide the remediation action.","").strip()
    items.append((core, type_of(inc) or "UNKNOWN"))

SYS=("You label Kubernetes incidents for an ML classifier. Given an incident and its TYPE, output ONLY "
     "JSON with the TWO discriminant signals Kubernetes really provides (the numbers come from elsewhere):\n"
     '{"event_reason":"<the k8s Event Reason, e.g. OOMKilling, ImagePullBackOff, FailedMount, NodeNotReady, '
     'CreateContainerConfigError, Unhealthy, Evicted, FailedScheduling, ProvisioningFailed; or None>",'
     '"log_sig":"<short snake_case log signature specific to THIS incident, e.g. secret_not_found, '
     'oomkilled, coredns_servfail, etcd_no_leader_quorum>"}\n'
     "Both must be consistent with THIS incident's type. JSON only.")

def reason(inc, typ):
    body=json.dumps({"model":MODEL,"temperature":0.5,"max_tokens":90,"response_format":{"type":"json_object"},
        "messages":[{"role":"system","content":SYS},
                    {"role":"user","content":f"Incident: {inc}\nType: {typ}"}]}).encode()
    for att in range(120):
        key,idx=get_key()
        if key is None:
            time.sleep(25); reset_all(); continue
        req=urllib.request.Request(URL,data=body,headers={"Authorization":"Bearer "+key,
            "Content-Type":"application/json","User-Agent":"Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req,timeout=60) as r:
                d=json.loads(r.read())
            o=json.loads(d["choices"][0]["message"]["content"])
            row={"event_reason":str(o.get("event_reason","None"))[:40] or "None",
                 "log_sig":re.sub(r"[^a-z0-9_]","",str(o.get("log_sig","unknown")).lower().replace(" ","_"))[:40] or "unknown"}
            return row
        except urllib.error.HTTPError as e:
            if e.code==429:
                bd=""
                try: bd=e.read().decode()
                except Exception: pass
                if "tokens per day" in bd or "TPD" in bd: mark_tpd(idx); continue
                time.sleep(1.5); continue
            time.sleep(1)
        except Exception:
            time.sleep(1)
    return None

wlock=threading.Lock(); cnt={"n":0}
def process(inc, typ, f):
    r=reason(inc, typ)
    if not r: return
    with wlock:
        f.write(json.dumps({"type":typ,"incident":inc,**r},ensure_ascii=False)+"\n"); f.flush()
        cnt["n"]+=1
        if N<=10:
            print(json.dumps({"type":typ,**r},ensure_ascii=False),flush=True)
        elif cnt["n"]%100==0:
            print(f"  {start+cnt['n']}/12000  (+{cnt['n']})  cles actives {len(available)}/{len(KEYS)}",flush=True)

def main():
    global start
    start=sum(1 for _ in open(OUTFILE,encoding="utf-8")) if os.path.exists(OUTFILE) else 0
    todo=items[start:start+N]
    print(f"=== ML FEATURES Groq/{MODEL} | {len(KEYS)} cle(s) | a faire={len(todo)} (depuis {start}) ===",flush=True)
    with open(OUTFILE,"a" if start else "w",encoding="utf-8") as f:
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            for inc,typ in todo: ex.submit(process,inc,typ,f)
    print(f"\nFINI session: +{cnt['n']} -> total {start+cnt['n']}/12000 ({OUTFILE})",flush=True)

if __name__=="__main__": main()
