#!/usr/bin/env python3
"""Prépare le dataset ML 120 classes — 100% IA (mots ET chiffres générés par Cerebras).
Entrée : scripts/llm-finetune/sft_120_ml_full.jsonl (type + event_reason + log_sig + 11 métriques IA)
Étapes : lecture -> nettoyage -> CSV -> split stratifié 80/10/10 -> train (HistGBM) + rapport.
Sorties (scripts/train-120/) : dataset_120_ml.csv, ml_train/val/test.csv, model_120_ml.joblib
"""
import os, sys, json, collections
import pandas as pd
D = os.path.dirname(os.path.abspath(__file__))
ML_JSONL = os.path.join(D, "..", "llm-finetune", "sft_120_ml_full.jsonl")

NUMMAP = {  # champ IA -> colonne feature_
    "cpu_cores":"feature_cpu_cores","memory_mb":"feature_memory_mb","pod_restarts":"feature_pod_restarts",
    "unavailable_replicas":"feature_unavailable_replicas","latency_p95_s":"feature_latency_p95_s",
    "http_5xx_ratio":"feature_http_5xx_ratio","node_not_ready":"feature_node_not_ready",
    "db_hits":"feature_db_hits","log_lines":"feature_log_lines","error_lines":"feature_error_lines",
    "warning_lines":"feature_warning_lines",
}

# ---- 1) LECTURE (tout IA) + nettoyage ----
rows=[]; bad=0
with open(ML_JSONL, encoding="utf-8") as fh:
    for line in fh:
        try:
            o=json.loads(line); t=o.get("type")
            if not t or t=="UNKNOWN": bad+=1; continue
            r={"scenario":t,
               "event_reason":str(o.get("event_reason","None")) or "None",
               "log_sig":str(o.get("log_sig","unknown")) or "unknown"}
            for ai,col in NUMMAP.items(): r[col]=float(o.get(ai,0) or 0)
            rows.append(r)
        except Exception:
            bad+=1
df=pd.DataFrame(rows)
print(f"[1] Lu {len(df)} lignes (100% IA) | rejets={bad} | types={df['scenario'].nunique()}/120")
vals=sorted(df['scenario'].value_counts().values)
print(f"    équilibre : min={vals[0]} max={vals[-1]} moy={sum(vals)//len(vals)}/type")

# ---- 2) PERSISTANCE ----
df.to_csv(os.path.join(D,"dataset_120_ml.csv"), index=False)
print("[2] -> dataset_120_ml.csv")

# ---- 3) ENCODAGE + SPLIT stratifié 80/10/10 + TRAIN ----
from sklearn.preprocessing import LabelEncoder
from sklearn.model_selection import train_test_split
from sklearn.metrics import f1_score, accuracy_score, classification_report
from sklearn.ensemble import HistGradientBoostingClassifier
import joblib

feat_num=[c for c in df.columns if c.startswith("feature_")]
le_r=LabelEncoder(); df["event_reason_code"]=le_r.fit_transform(df["event_reason"])
le_l=LabelEncoder(); df["log_sig_code"]=le_l.fit_transform(df["log_sig"])
feats=feat_num+["event_reason_code","log_sig_code"]
le=LabelEncoder(); df["y"]=le.fit_transform(df["scenario"])

tr,tmp=train_test_split(df,test_size=0.20,random_state=42,stratify=df["y"])
va,te=train_test_split(tmp,test_size=0.50,random_state=42,stratify=tmp["y"])
for name,part in [("ml_train.csv",tr),("ml_val.csv",va),("ml_test.csv",te)]:
    part.drop(columns=["y"]).to_csv(os.path.join(D,name),index=False)
print(f"[3] Split : train={len(tr)} val={len(va)} test={len(te)} | types test={te['scenario'].nunique()}/120")

clf=HistGradientBoostingClassifier(max_iter=300,max_depth=8,learning_rate=0.2,random_state=42)
clf.fit(tr[feats].values,tr["y"].values)
pred=clf.predict(te[feats].values)
f1=f1_score(te["y"].values,pred,average="macro"); acc=accuracy_score(te["y"].values,pred)
rep=classification_report(te["y"].values,pred,output_dict=True,zero_division=0)
strong=sum(1 for k,v in rep.items() if k not in ("accuracy","macro avg","weighted avg")
           and isinstance(v,dict) and v.get("f1-score",0)>=0.60)
joblib.dump({"model":clf,"le_scenario":le,"le_reason":le_r,"le_log":le_l,"features":feats},
            os.path.join(D,"model_120_ml.joblib"))
print("\n===== MODÈLE ML 120 (candidat, 100% IA, HistGBM) =====")
print(f"  classes          : {len(le.classes_)}/120")
print(f"  F1 macro (test)  : {f1:.3f}")
print(f"  Accuracy (test)  : {acc:.3f}")
print(f"  classes F1>=0.60 : {strong}/120")
print(f"  -> model_120_ml.joblib (CANDIDAT, non live)")
