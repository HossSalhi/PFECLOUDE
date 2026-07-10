#!/usr/bin/env python3
"""Preparation du dataset enrichi pour le FINE-TUNING.
Entree : sft_120_sub_reasoned.jsonl (12000, enrichi Groq)
Etapes : validation JSON -> nettoyage (ratés/vides/placeholder) -> scrub -> dedup
         -> mapping type (120) -> verif equilibre -> split STRATIFIE 80/10/10
Sortie : train.jsonl / val.jsonl / test.jsonl  (+ rapport)
"""
import json, re, os, random, collections
random.seed(42)
D=os.path.dirname(os.path.abspath(__file__))
IN=os.path.join(D,"sft_120_sub_reasoned.jsonl")

# --- mapping incident -> TYPE (via les symptomes du generateur) ---
src=open(os.path.join(D,"gen_120_subcases.py"),encoding="utf-8").read()
g={"__file__":os.path.join(D,"gen_120_subcases.py")}; exec(src.split("WRAP=")[0], g)
S=g["S"]; TYPES=list(S.keys())
def pat(sym):
    p=re.escape(sym)
    for ph in [r"\{app\}",r"\{ns\}",r"\{node\}",r"\{db\}",r"\{cluster\}"]: p=p.replace(ph,".+?")
    return re.compile(p.replace(r"\{n\}",r"\d+"))
PATS=[(t,pat(sym)) for t,subs in S.items() for (sym,_,_) in subs]
def type_of(inc):
    for t,rx in PATS:
        if rx.search(inc): return t
    return None

# --- lecture + validation + nettoyage ---
report=collections.Counter()
clean=[]; seen=set()
for ln,line in enumerate(open(IN,encoding="utf-8"),1):
    report["lues"]+=1
    try:
        obj=json.loads(line); m=obj["messages"]
        assert len(m)==3 and [x["role"] for x in m]==["system","user","assistant"]
    except Exception:
        report["json_invalide"]+=1; continue
    a=m[2]["content"]; u=m[1]["content"]
    act=re.search(r"ACTION=(\S+)",a); expl=re.search(r"EXPLICATION:\s*(.*)$",a,re.S)
    diag=re.search(r"DIAGNOSTIC:\s*(.*?)\s*\|\s*ACTION=",a,re.S)
    if not (act and expl and diag): report["structure_ko"]+=1; continue
    e=expl.group(1).strip(); dg=diag.group(1).strip()
    # nettoyage: placeholder non-enrichi, vide, trop court, trop long, artefacts JSON
    if "(a enrichir)" in a or "a enrichir" in e.lower(): report["non_enrichi"]+=1; continue
    if len(e)<40: report["expl_trop_courte"]+=1; continue
    if len(dg)<15: report["diag_trop_court"]+=1; continue
    if e.count('"')>6 or e.strip().startswith("{"): report["artefact_json"]+=1; continue
    # scrub: normaliser espaces, retirer chars de remplacement
    e=re.sub(r"\s+"," ",e.replace("�","")).strip()
    dg=re.sub(r"\s+"," ",dg.replace("�","")).strip()
    e=e[:600]  # borne dure
    # dedup (sur user+action+expl)
    key=(u,act.group(1),e)
    if key in seen: report["doublon"]+=1; continue
    seen.add(key)
    typ=type_of(u)
    if not typ: report["type_inconnu"]+=1; continue
    # reconstruire l'assistant propre
    a2=f"DIAGNOSTIC: {dg} | ACTION={act.group(1)} | EXPLICATION: {e}"
    obj["messages"][2]["content"]=a2
    clean.append((typ, obj))
    report["gardees"]+=1

print("=== NETTOYAGE ===")
for k in ["lues","json_invalide","structure_ko","non_enrichi","expl_trop_courte","diag_trop_court","artefact_json","doublon","type_inconnu","gardees"]:
    if report[k]: print(f"  {k:20s}: {report[k]}")

# --- equilibre par type ---
bytype=collections.defaultdict(list)
for typ,obj in clean: bytype[typ].append(obj)
vals=sorted(len(v) for v in bytype.values())
print(f"\n=== EQUILIBRE ({len(bytype)}/120 types) ===")
print(f"  min={vals[0]} max={vals[-1]} moyenne={sum(vals)//len(vals)} exemples/type")

# --- longueurs (tokens ~ chars/4) ---
lens=[len(json.dumps(o,ensure_ascii=False)) for _,o in clean]
lens.sort()
p95=lens[int(len(lens)*0.95)]
print(f"\n=== LONGUEURS ===")
print(f"  exemple median={lens[len(lens)//2]} chars (~{lens[len(lens)//2]//4} tokens) | p95={p95} chars (~{p95//4} tokens) | max={lens[-1]}")

# --- split STRATIFIE 80/10/10 par type ---
train=[]; val=[]; test=[]
for typ,objs in bytype.items():
    random.shuffle(objs)
    n=len(objs); n_tr=int(n*0.8); n_va=int(n*0.1)
    train+=objs[:n_tr]; val+=objs[n_tr:n_tr+n_va]; test+=objs[n_tr+n_va:]
random.shuffle(train); random.shuffle(val); random.shuffle(test)
def dump(name,rows):
    with open(os.path.join(D,name),"w",encoding="utf-8") as f:
        for o in rows: f.write(json.dumps(o,ensure_ascii=False)+"\n")
dump("train.jsonl",train); dump("val.jsonl",val); dump("test.jsonl",test)
print(f"\n=== SPLIT STRATIFIE (80/10/10 par type) ===")
print(f"  train.jsonl : {len(train)}")
print(f"  val.jsonl   : {len(val)}")
print(f"  test.jsonl  : {len(test)}")
print(f"  TOTAL       : {len(train)+len(val)+len(test)}")
# verif: chaque type present dans val et test ?
tv=collections.Counter(type_of(o['messages'][1]['content']) for o in val)
tt=collections.Counter(type_of(o['messages'][1]['content']) for o in test)
print(f"  types dans val : {len(tv)}/120 | types dans test : {len(tt)}/120")
print("\n>>> PRET POUR LE FINE-TUNING (train/val/test.jsonl, format chat messages).")
