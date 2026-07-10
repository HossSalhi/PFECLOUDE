#!/usr/bin/env python3
"""Labelling HYBRIDE gratuit : MON action experte (juste) + explication RAISONNEE de Llama-3.3-70B
(specifique a chaque incident). Groq gratuit. Chaque exemple = action correcte + diagnostic+explication uniques.
Lit GROQ_API_KEY. Ecriture incrementale + resumable. Gere 429.
env: LABEL_N (defaut 20), LABEL_OUT (defaut sft_120_reasoned.jsonl), LABEL_WORKERS (defaut 4), SRC (defaut sft_120.jsonl).
"""
import os, re, json, time, threading, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor
D=os.path.dirname(os.path.abspath(__file__))
KEY=os.environ["GROQ_API_KEY"]
MODEL=os.environ.get("GROQ_MODEL","llama-3.3-70b-versatile")
N=int(os.environ.get("LABEL_N","20"))
SRC=os.path.join(D, os.environ.get("SRC","sft_120.jsonl"))
OUTFILE=os.path.join(D, os.environ.get("LABEL_OUT","sft_120_reasoned.jsonl"))
WORKERS=int(os.environ.get("LABEL_WORKERS","4"))
URL="https://api.groq.com/openai/v1/chat/completions"

# charger (incident, MON action experte) depuis sft_120.jsonl
items=[]
for l in open(SRC,encoding="utf-8"):
    m=json.loads(l)["messages"]
    inc=m[1]["content"].replace("Incident: ","").replace(" Decide the remediation action.","").strip()
    act=re.search(r"ACTION=(\S+)", m[2]["content"]).group(1)
    items.append((inc,act))

SYS=("Expert Kubernetes SRE. Given an incident and the correct ACTION (do NOT change it), write a specific "
     "root-cause diagnosis and a specific explanation referencing the incident's app/namespace/cluster and how "
     "the ACTION fixes it. JSON only: {\"diagnosis\":\"...\",\"explanation\":\"...\"}.")
DSYS="You are the automatic decision engine of a Kubernetes self-healing system. Output: DIAGNOSTIC: <cause> | ACTION=<ACTION_NAME> | EXPLICATION: <how to fix>."

def reason(inc, act):
    body=json.dumps({"model":MODEL,"temperature":0.4,"max_tokens":220,"response_format":{"type":"json_object"},
        "messages":[{"role":"system","content":SYS},
                    {"role":"user","content":f"Incident: {inc}\nCorrect ACTION (do not change): {act}"}]}).encode()
    req=urllib.request.Request(URL,data=body,headers={"Authorization":"Bearer "+KEY,"Content-Type":"application/json","User-Agent":"Mozilla/5.0"})
    for att in range(25):
        try:
            with urllib.request.urlopen(req,timeout=60) as r:
                d=json.loads(r.read())
            obj=json.loads(d["choices"][0]["message"]["content"])
            return (re.sub(r"\s+"," ",str(obj.get("diagnosis","")))[:260],
                    re.sub(r"\s+"," ",str(obj.get("explanation","")))[:380])
        except urllib.error.HTTPError as e:
            if e.code in (429,503): time.sleep(1.5); continue   # backoff DOUX (429 = rafale, recup vite)
            time.sleep(1)
        except Exception:
            time.sleep(1)
    return None

lock=threading.Lock(); cnt={"n":0}
def process(inc, act, f):
    r=reason(inc, act)
    if not r: return
    diag,expl=r
    with lock:
        f.write(json.dumps({"messages":[{"role":"system","content":DSYS},
            {"role":"user","content":f"Incident: {inc}. Decide the remediation action."},
            {"role":"assistant","content":f"DIAGNOSTIC: {diag} | ACTION={act} | EXPLICATION: {expl}"}]},ensure_ascii=False)+"\n")
        f.flush(); cnt["n"]+=1
        if N<=25: print(f"[{act}] {inc[:58]}\n   DIAG: {diag[:90]}\n   EXPL: {expl[:150]}\n",flush=True)
        elif cnt["n"]%100==0: print(f"  {cnt['n']}/{N}",flush=True)

def main():
    start=sum(1 for _ in open(OUTFILE,encoding="utf-8")) if os.path.exists(OUTFILE) else 0
    todo=items[start:start+N]
    print(f"=== HYBRIDE Groq/{MODEL} | a faire={len(todo)} (depuis {start}) ===",flush=True)
    with open(OUTFILE,"a" if start else "w",encoding="utf-8") as f:
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            for inc,act in todo: ex.submit(process,inc,act,f)
    print(f"\nFINI: {cnt['n']} labellises (action juste + explication raisonnee) -> {OUTFILE}",flush=True)

if __name__=="__main__": main()
