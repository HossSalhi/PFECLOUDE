#!/usr/bin/env python3
"""ANALYSES ML AVANCEES (expert) sur les 120 types — complete ml_pipeline_120.py.
Validation croisee, ablation, robustesse au bruit, tuning, fix LightGBM, confusions, importance.
Utilise des sklearn Pipelines (preprocessing PAR FOLD = zero fuite). Artefacts -> artifacts/.
"""
import os, json, warnings, joblib
import numpy as np, pandas as pd
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
warnings.filterwarnings("ignore")
from sklearn.compose import ColumnTransformer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler, LabelEncoder
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.model_selection import StratifiedKFold, cross_val_score, GridSearchCV, train_test_split
from sklearn.linear_model import LogisticRegression
from sklearn.svm import LinearSVC
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import f1_score, accuracy_score, confusion_matrix, classification_report
from lightgbm import LGBMClassifier

D=os.path.dirname(os.path.abspath(__file__)); OUT=os.path.join(D,"artifacts"); os.makedirs(OUT,exist_ok=True)
SRC=os.path.join(D,"..","llm-finetune","sft_120_ml_full.jsonl")
NUM=["cpu_cores","memory_mb","pod_restarts","unavailable_replicas","latency_p95_s","http_5xx_ratio",
     "node_not_ready","db_hits","log_lines","error_lines","warning_lines"]
def sep(t): print("\n"+"="*72+f"\n {t}\n"+"="*72)

df=pd.DataFrame(json.loads(l) for l in open(SRC,encoding="utf-8"))
df=df[df['type']!="UNKNOWN"].drop_duplicates(subset=["type","event_reason","log_sig"]+NUM).reset_index(drop=True)
for c in NUM: df[c]=pd.to_numeric(df[c],errors="coerce").fillna(0.0)
df["event_reason"]=df["event_reason"].fillna("None").astype(str)
df["log_sig"]=df["log_sig"].fillna("unknown").astype(str).str.lower()
df["log_txt"]=df["log_sig"].str.replace("_"," ")
le=LabelEncoder(); y=le.fit_transform(df["type"])
print(f"Dataset: {len(df)} lignes, {df['type'].nunique()} classes")

def make_pre(groups=("num","log","evt")):
    t=[]
    if "num" in groups: t.append(("num",StandardScaler(),NUM))
    if "log" in groups: t.append(("log",TfidfVectorizer(token_pattern=r"[a-z0-9]+",min_df=2),"log_txt"))
    if "evt" in groups: t.append(("evt",TfidfVectorizer(analyzer="char_wb",ngram_range=(2,4),min_df=2),"event_reason"))
    return ColumnTransformer(t)
def pipe(clf,groups=("num","log","evt")): return Pipeline([("pre",make_pre(groups)),("clf",clf)])
cv=StratifiedKFold(5,shuffle=True,random_state=42)
scorer="f1_macro"

# ===== 1) VALIDATION CROISEE (5-fold) =====
sep("1) VALIDATION CROISEE 5-FOLD (robustesse reelle)")
cvmods={"LogisticRegression":LogisticRegression(max_iter=1000,C=5,class_weight="balanced",n_jobs=-1),
        "LinearSVC":LinearSVC(C=1,class_weight="balanced"),
        "RandomForest":RandomForestClassifier(n_estimators=200,n_jobs=-1,class_weight="balanced",random_state=42)}
cv_res={}
for name,clf in cvmods.items():
    s=cross_val_score(pipe(clf),df,y,cv=cv,scoring=scorer,n_jobs=-1)
    cv_res[name]=s; print(f"  {name:20} F1_macro = {s.mean():.4f} +/- {s.std():.4f}  (folds: {np.round(s,3)})")
best_cv=max(cv_res,key=lambda k:cv_res[k].mean())
print(f"  >>> plus stable/haut : {best_cv} ({cv_res[best_cv].mean():.4f})")

# ===== 2) ABLATION (qu'est-ce qui porte la perf ?) =====
sep("2) ETUDE D'ABLATION (contribution de chaque groupe de features)")
base=LogisticRegression(max_iter=1000,C=5,class_weight="balanced",n_jobs=-1)
for label,groups in [("chiffres seuls",("num",)),("chiffres + log_sig",("num","log")),
                     ("chiffres + event_reason",("num","evt")),("TOUT (num+log+evt)",("num","log","evt"))]:
    s=cross_val_score(pipe(base,groups),df,y,cv=cv,scoring=scorer,n_jobs=-1)
    print(f"  {label:26} : F1_macro = {s.mean():.4f}")

# ===== 3) ROBUSTESSE AU BRUIT (log_sig manquant = scenario prod reel) =====
sep("3) ROBUSTESSE : log_sig degrade (simule des logs bruites/absents en prod)")
Xtr,Xte,ytr,yte=train_test_split(df,y,test_size=0.20,random_state=42,stratify=y)
m=pipe(LogisticRegression(max_iter=1000,C=5,class_weight="balanced",n_jobs=-1)).fit(Xtr,ytr)
rng=np.random.default_rng(0); levels=[0,0.1,0.25,0.5,0.75,1.0]; robust=[]
for p in levels:
    Xn=Xte.copy(); mask=rng.random(len(Xn))<p
    Xn.loc[mask,"log_txt"]="unknown"
    f1=f1_score(yte,m.predict(Xn),average="macro"); robust.append(f1)
    print(f"  {int(p*100):3d}% log_sig manquant -> F1_macro = {f1:.4f}")
plt.figure(figsize=(7,4)); plt.plot([int(p*100) for p in levels],robust,"o-")
plt.xlabel("% log_sig manquant"); plt.ylabel("F1 macro"); plt.title("Robustesse a la degradation des logs"); plt.grid(True)
plt.tight_layout(); plt.savefig(os.path.join(OUT,"robustness_noise.png"),dpi=90); plt.close()
print("  -> plot: artifacts/robustness_noise.png  (montre l'estimation REALISTE en prod)")

# ===== 4) TUNING (GridSearch) =====
sep("4) TUNING HYPERPARAMETRES (GridSearch, LogisticRegression)")
gs=GridSearchCV(pipe(LogisticRegression(max_iter=2000,class_weight="balanced",n_jobs=-1)),
                {"clf__C":[1,3,5,10,20]},cv=3,scoring=scorer,n_jobs=-1)
gs.fit(Xtr,ytr)
print(f"  meilleur C = {gs.best_params_['clf__C']} | F1_macro CV = {gs.best_score_:.4f}")
tuned=gs.best_estimator_

# ===== 5) FIX LIGHTGBM =====
sep("5) CORRECTION DE LIGHTGBM (params adaptes aux features creuses)")
lgb=pipe(LGBMClassifier(n_estimators=500,learning_rate=0.1,num_leaves=63,min_child_samples=5,
                        subsample=0.9,colsample_bytree=0.6,n_jobs=-1,verbose=-1))
lgb.fit(Xtr,ytr); f1lgb=f1_score(yte,lgb.predict(Xte),average="macro")
print(f"  LightGBM corrige : F1_macro test = {f1lgb:.4f}  (avant: 0.15)")

# ===== 6) EVAL FINALE + CONFUSIONS =====
sep("6) MODELE FINAL (tuned) : evaluation + analyse des confusions")
pt=tuned.predict(Xte); acc=accuracy_score(yte,pt); f1=f1_score(yte,pt,average="macro")
print(f"  Accuracy={acc:.4f} | F1_macro={f1:.4f}")
rep=classification_report(yte,pt,output_dict=True,zero_division=0,target_names=le.classes_)
per=sorted([(k,v["f1-score"]) for k,v in rep.items() if k in le.classes_],key=lambda x:x[1])
print("  10 pires classes (F1) :")
for k,f in per[:10]: print(f"    {k:34} F1={f:.2f}")
cm=confusion_matrix(yte,pt); conf=[]
for i in range(len(le.classes_)):
    for j in range(len(le.classes_)):
        if i!=j and cm[i,j]>0: conf.append((cm[i,j],le.classes_[i],le.classes_[j]))
conf.sort(reverse=True)
print("  Top confusions (vrai -> predit) :")
for c,a,b in conf[:8]: print(f"    {c}x  {a} -> {b}")

# ===== 7) SAUVEGARDE MODELE FINAL =====
sep("7) SAUVEGARDE DU MODELE FINAL TUNE")
joblib.dump({"pipeline":tuned,"label_encoder":le,"num_features":NUM},os.path.join(OUT,"ml_120_final.joblib"))
with open(os.path.join(OUT,"REPORT_ADVANCED.txt"),"w",encoding="utf-8") as f:
    f.write(f"CV 5-fold best: {best_cv} {cv_res[best_cv].mean():.4f}+/-{cv_res[best_cv].std():.4f}\n")
    f.write(f"Tuned LogReg C={gs.best_params_['clf__C']} | test acc={acc:.4f} F1={f1:.4f}\n")
    f.write(f"Robustesse (0/25/50/100% log manquant): {[round(r,3) for r in [robust[0],robust[2],robust[3],robust[5]]]}\n")
    f.write(f"LightGBM corrige: {f1lgb:.4f}\n")
print("  -> artifacts/ml_120_final.joblib + REPORT_ADVANCED.txt")
print("\n=== ANALYSES AVANCEES TERMINEES ===")
