#!/usr/bin/env python3
"""Preparation LLM v2 — PRO, ZERO FUITE. Repart de l'enrichi, deduplique GLOBALEMENT sur le texte
d'incident (input), re-split stratifie 80/10/10, et ASSERTE l'absence de fuite train/val/test.
Sortie : train.jsonl / val.jsonl / test.jsonl (ecrase). + rapport.
"""
import json, re, os, random, collections
random.seed(42)
D=os.path.dirname(os.path.abspath(__file__))
IN=os.path.join(D,"sft_120_sub_reasoned.jsonl")

# mapping incident -> TYPE
src=open(os.path.join(D,"gen_120_subcases.py"),encoding="utf-8").read()
g={"__file__":os.path.join(D,"gen_120_subcases.py")}; exec(src.split("WRAP=")[0], g); S=g["S"]
def pat(sym):
    p=re.escape(sym)
    for ph in [r"\{app\}",r"\{ns\}",r"\{node\}",r"\{db\}",r"\{cluster\}"]: p=p.replace(ph,".+?")
    return re.compile(p.replace(r"\{n\}",r"\d+"))
PATS=[(t,pat(sym)) for t,subs in S.items() for (sym,_,_) in subs]
def type_of(u):
    for t,rx in PATS:
        if rx.search(u): return t
    return None

rep=collections.Counter(); clean=[]; seen_user=set()
for line in open(IN,encoding="utf-8"):
    rep["lues"]+=1
    try:
        obj=json.loads(line); m=obj["messages"]
        assert len(m)==3 and [x["role"] for x in m]==["system","user","assistant"]
    except Exception:
        rep["json_ko"]+=1; continue
    u=m[1]["content"]; a=m[2]["content"]
    act=re.search(r"ACTION=(\S+)",a); expl=re.search(r"EXPLICATION:\s*(.*)$",a,re.S); diag=re.search(r"DIAGNOSTIC:\s*(.*?)\s*\|\s*ACTION=",a,re.S)
    if not(act and expl and diag): rep["structure_ko"]+=1; continue
    e=expl.group(1).strip(); dg=diag.group(1).strip()
    if "a enrichir" in a.lower(): rep["non_enrichi"]+=1; continue
    if len(e)<40 or len(dg)<15: rep["trop_court"]+=1; continue
    if e.count('"')>6 or e.strip().startswith("{"): rep["artefact"]+=1; continue
    # DEDUP GLOBAL sur le TEXTE D'INCIDENT (input) = anti-fuite a la racine
    if u in seen_user: rep["doublon_incident"]+=1; continue
    seen_user.add(u)
    typ=type_of(u)
    if not typ: rep["type_inconnu"]+=1; continue
    e=re.sub(r"\s+"," ",e.replace("�","")).strip()[:600]
    dg=re.sub(r"\s+"," ",dg.replace("�","")).strip()
    obj["messages"][2]["content"]=f"DIAGNOSTIC: {dg} | ACTION={act.group(1)} | EXPLICATION: {e}"
    clean.append((typ,obj)); rep["gardees"]+=1

print("=== NETTOYAGE + DEDUP GLOBAL ===")
for k in ["lues","json_ko","structure_ko","non_enrichi","trop_court","artefact","doublon_incident","type_inconnu","gardees"]:
    if rep[k]: print(f"  {k:20}: {rep[k]}")

# equilibre
bytype=collections.defaultdict(list)
for typ,o in clean: bytype[typ].append(o)
vals=sorted(len(v) for v in bytype.values())
print(f"\n=== EQUILIBRE : {len(bytype)}/120 types | min={vals[0]} max={vals[-1]} moy={sum(vals)//len(vals)} ===")

# split STRATIFIE par type
train=[];val=[];test=[]
for typ,objs in bytype.items():
    random.shuffle(objs); n=len(objs); ntr=int(n*0.8); nva=int(n*0.1)
    train+=objs[:ntr]; val+=objs[ntr:ntr+nva]; test+=objs[ntr+nva:]
random.shuffle(train); random.shuffle(val); random.shuffle(test)

def dump(name,rows):
    with open(os.path.join(D,name),"w",encoding="utf-8") as f:
        for o in rows: f.write(json.dumps(o,ensure_ascii=False)+"\n")
dump("train.jsonl",train); dump("val.jsonl",val); dump("test.jsonl",test)

# ASSERTION ANTI-FUITE
utr={o["messages"][1]["content"] for o in train}
uva={o["messages"][1]["content"] for o in val}
ute={o["messages"][1]["content"] for o in test}
leak=len(utr&uva)+len(utr&ute)+len(uva&ute)
tv=len({type_of(o['messages'][1]['content']) for o in val}); tt=len({type_of(o['messages'][1]['content']) for o in test})
print(f"\n=== SPLIT 80/10/10 : train={len(train)} val={len(val)} test={len(test)} ===")
print(f"  types : train={len(bytype)}/120  val={tv}/120  test={tt}/120")
print(f"  FUITE (user commun entre splits) : {leak}  {'✅ ZERO FUITE' if leak==0 else '❌ RESTE DE LA FUITE'}")
print(f"\n>>> {'DATASET 100% PRET POUR FINE-TUNING' if leak==0 and tv==120 and tt==120 else 'A REVOIR'}")
