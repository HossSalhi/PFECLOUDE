#!/usr/bin/env python3
"""Enrichissement HYBRIDE Groq MULTI-CLES : MON action experte (juste) + explication RAISONNEE
de Llama (specifique a chaque incident). Bascule AUTOMATIQUEMENT entre plusieurs cles Groq :
quand une cle epuise son TPD (tokens/jour), passe a la suivante -> multiplie le quota quotidien.
Sortie incrementale + resumable. Gere 429 per-minute (backoff doux) et 429 TPD (rotation de cle).

env:
  GROQ_API_KEYS  = cles separees par des virgules (ou GROQ_API_KEY pour une seule)
  GROQ_MODEL     = defaut llama-3.1-8b-instant
  SRC            = defaut sft_120_sub.jsonl
  LABEL_OUT      = defaut sft_120_sub_reasoned.jsonl
  LABEL_N        = nb a faire (defaut 20000)
  LABEL_WORKERS  = defaut 3
"""
import os, re, json, time, threading, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor
D=os.path.dirname(os.path.abspath(__file__))
KEYS=[k.strip() for k in os.environ.get("GROQ_API_KEYS", os.environ.get("GROQ_API_KEY","")).split(",") if k.strip()]
assert KEYS, "GROQ_API_KEYS vide"
MODEL=os.environ.get("GROQ_MODEL","llama-3.1-8b-instant")
N=int(os.environ.get("LABEL_N","20000"))
SRC=os.path.join(D, os.environ.get("SRC","sft_120_sub.jsonl"))
OUTFILE=os.path.join(D, os.environ.get("LABEL_OUT","sft_120_sub_reasoned.jsonl"))
WORKERS=int(os.environ.get("LABEL_WORKERS","3"))
URL="https://api.groq.com/openai/v1/chat/completions"

# --- rotation de cles PARALLELE round-robin (thread-safe) ---
# Toutes les cles tournent en meme temps (chaque requete prend la cle suivante) -> debit x N.
# Une cle qui epuise son TPD est RETIREE du pool ; reintegree apres pause si toutes tombent.
klock=threading.Lock()
available=list(range(len(KEYS)))    # indices de cles encore utilisables
rr=[0]                              # compteur round-robin
def get_key():
    with klock:
        if not available: return None, -1
        idx=available[rr[0] % len(available)]; rr[0]+=1
        return KEYS[idx], idx
def mark_tpd(idx):
    """Retire la cle idx (TPD epuise). Retourne False s'il n'en reste plus."""
    with klock:
        if idx in available:
            available.remove(idx)
            print(f"  >> cle #{idx+1} TPD epuise -> retiree ({len(available)} cle(s) restante(s))",flush=True)
        return len(available)>0
def reset_all():
    with klock:
        available[:]=list(range(len(KEYS)))
        print("  >> reset: toutes les cles reintegrees (fenetre glissante)",flush=True)

# charger (incident, MON action experte) depuis la source
items=[]
for l in open(SRC,encoding="utf-8"):
    m=json.loads(l)["messages"]
    inc=m[1]["content"].replace("Incident: ","").replace(" Decide the remediation action.","").strip()
    act=re.search(r"ACTION=(\S+)", m[2]["content"]).group(1)
    items.append((inc,act))

SYS=("Expert Kubernetes SRE. Given an incident and the correct ACTION (do NOT change it), write a specific "
     "root-cause diagnosis and a specific explanation referencing the incident's app/namespace/cluster and how "
     "the ACTION fixes it. JSON only: {\"diagnosis\":\"...\",\"explanation\":\"...\"}.")
DSYS="You are the automatic decision engine of a Kubernetes self-healing system. Reason from the symptoms and output: DIAGNOSTIC: <cause> | ACTION=<ACTION_NAME> | EXPLICATION: <how to fix>."

def reason(inc, act):
    body=json.dumps({"model":MODEL,"temperature":0.4,"max_tokens":220,"response_format":{"type":"json_object"},
        "messages":[{"role":"system","content":SYS},
                    {"role":"user","content":f"Incident: {inc}\nCorrect ACTION (do not change): {act}"}]}).encode()
    for att in range(120):
        key,idx=get_key()
        if key is None:                                    # toutes les cles TPD-epuisees
            print("  !! toutes les cles epuisees -> pause 25s (fenetre glissante)",flush=True)
            time.sleep(25); reset_all(); continue
        req=urllib.request.Request(URL,data=body,headers={"Authorization":"Bearer "+key,
            "Content-Type":"application/json","User-Agent":"Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req,timeout=60) as r:
                d=json.loads(r.read())
            obj=json.loads(d["choices"][0]["message"]["content"])
            return (re.sub(r"\s+"," ",str(obj.get("diagnosis","")))[:260],
                    re.sub(r"\s+"," ",str(obj.get("explanation","")))[:380])
        except urllib.error.HTTPError as e:
            if e.code==429:
                bd=""
                try: bd=e.read().decode()
                except Exception: pass
                if "tokens per day" in bd or "TPD" in bd:          # quota JOUR -> retirer la cle du pool
                    mark_tpd(idx); continue                         # (le branchement key=None gere l'epuisement total)
                time.sleep(1.5); continue                           # quota MINUTE -> backoff doux, autre cle au prochain tour
            time.sleep(1)
        except Exception:
            time.sleep(1)
    return None

wlock=threading.Lock(); cnt={"n":0}
def process(inc, act, f):
    r=reason(inc, act)
    if not r: return
    diag,expl=r
    with wlock:
        f.write(json.dumps({"messages":[{"role":"system","content":DSYS},
            {"role":"user","content":f"Incident: {inc}. Decide the remediation action."},
            {"role":"assistant","content":f"DIAGNOSTIC: {diag} | ACTION={act} | EXPLICATION: {expl}"}]},ensure_ascii=False)+"\n")
        f.flush(); cnt["n"]+=1
        if cnt["n"]%50==0:
            done_tot=start+cnt["n"]
            print(f"  {done_tot}/12000  (+{cnt['n']} cette session)  cles actives: {len(available)}/{len(KEYS)}",flush=True)

def main():
    global start
    start=sum(1 for _ in open(OUTFILE,encoding="utf-8")) if os.path.exists(OUTFILE) else 0
    todo=items[start:start+N]
    print(f"=== MULTI-CLE Groq/{MODEL} | {len(KEYS)} cle(s) | a faire={len(todo)} (depuis {start}) ===",flush=True)
    with open(OUTFILE,"a" if start else "w",encoding="utf-8") as f:
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            for inc,act in todo: ex.submit(process,inc,act,f)
    print(f"\nFINI session: +{cnt['n']} enrichis -> total {start+cnt['n']}/12000  ({OUTFILE})",flush=True)

if __name__=="__main__": main()
