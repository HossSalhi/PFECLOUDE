#!/usr/bin/env python3
"""PIPELINE ML PROFESSIONNEL — classification des 120 types d'incidents Kubernetes (HELIOS).
Dataset : scripts/llm-finetune/sft_120_ml_full.jsonl (12000, 100% IA : event_reason + log_sig + 11 metriques).
Couvre les 12 etapes : comprendre -> nettoyage -> EDA -> pretraitement -> desequilibre -> feature selection
-> split -> anti-fuite -> pipeline -> modeles (LogReg/SVM/RF/XGBoost/LightGBM) -> metriques -> sauvegarde.
Artefacts -> scripts/train-120/artifacts/  (modele + vectorizers + scaler + encodeur + plots + rapport).
"""
import os, json, warnings, joblib
import numpy as np, pandas as pd
from scipy.sparse import hstack, csr_matrix
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
warnings.filterwarnings("ignore")

D = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(D, "..", "llm-finetune", "sft_120_ml_full.jsonl")
OUT = os.path.join(D, "artifacts"); os.makedirs(OUT, exist_ok=True)
def sep(t): print("\n"+"="*72+f"\n {t}\n"+"="*72)

# ============================================================ 1) COMPRENDRE
sep("1) COMPRENDRE LE DATASET")
rows=[json.loads(l) for l in open(SRC,encoding="utf-8")]
df=pd.DataFrame(rows)
NUM=["cpu_cores","memory_mb","pod_restarts","unavailable_replicas","latency_p95_s","http_5xx_ratio",
     "node_not_ready","db_hits","log_lines","error_lines","warning_lines"]
print(f"Lignes: {len(df)} | Colonnes: {list(df.columns)}")
print(f"CIBLE = 'type' | Nb classes = {df['type'].nunique()}")
print(f"Features : 11 numeriques + 2 textuelles (event_reason, log_sig)")
vc=df['type'].value_counts()
print(f"Equilibre classes : min={vc.min()} max={vc.max()} moy={vc.mean():.0f} -> {'EQUILIBRE' if vc.max()-vc.min()<=5 else 'DESEQUILIBRE'}")

# ============================================================ 2) NETTOYAGE
sep("2) NETTOYAGE")
n0=len(df)
df=df[df['type']!="UNKNOWN"].copy()
df=df.drop_duplicates(subset=["type","event_reason","log_sig"]+NUM)
for c in NUM: df[c]=pd.to_numeric(df[c],errors="coerce").fillna(0.0)
df["event_reason"]=df["event_reason"].fillna("None").astype(str).str.strip()
df["log_sig"]=df["log_sig"].fillna("unknown").astype(str).str.strip().str.lower()
print(f"UNKNOWN retires + doublons + NaN geres : {n0} -> {len(df)} lignes")
print(f"Valeurs manquantes restantes : {int(df[NUM].isna().sum().sum())}")

# ============================================================ 3) EDA
sep("3) ANALYSE EXPLORATOIRE (EDA)")
print("Stats descriptives (numeriques) :")
print(df[NUM].describe().T[["mean","std","min","50%","max"]].round(2).to_string())
# outliers (IQR) par feature
print("\nOutliers (methode IQR) par feature :")
for c in NUM:
    q1,q3=df[c].quantile(.25),df[c].quantile(.75); iqr=q3-q1
    o=((df[c]<q1-1.5*iqr)|(df[c]>q3+1.5*iqr)).sum()
    print(f"  {c:22} : {o} outliers ({o*100//len(df)}%)")
# plot distribution des classes
plt.figure(figsize=(14,4)); df['type'].value_counts().plot(kind='bar'); plt.title("Distribution des 120 classes")
plt.tight_layout(); plt.savefig(os.path.join(OUT,"eda_class_distribution.png"),dpi=90); plt.close()
print(f"\n-> plot: artifacts/eda_class_distribution.png")

# ============================================================ 7) SPLIT (AVANT preprocessing = anti-fuite)
sep("7+8) SPLIT STRATIFIE 70/15/15 (AVANT preprocessing = anti-fuite)")
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import LabelEncoder, StandardScaler
from sklearn.feature_extraction.text import TfidfVectorizer
le=LabelEncoder(); y=le.fit_transform(df["type"])
Xtr,Xtmp,ytr,ytmp=train_test_split(df,y,test_size=0.30,random_state=42,stratify=y)
Xva,Xte,yva,yte=train_test_split(Xtmp,ytmp,test_size=0.50,random_state=42,stratify=ytmp)
print(f"train={len(Xtr)} | val={len(Xva)} | test={len(Xte)} | classes test={pd.Series(yte).nunique()}/120")
print("Anti-fuite : scaler + TF-IDF ajustes UNIQUEMENT sur train, appliques a val/test.")

# ============================================================ 4) PRETRAITEMENT (fit sur TRAIN)
sep("4) PRETRAITEMENT : StandardScaler (num) + TF-IDF (log_sig mots, event_reason char)")
scaler=StandardScaler().fit(Xtr[NUM].values)
tf_log=TfidfVectorizer(token_pattern=r"[a-z0-9]+",min_df=2).fit(Xtr["log_sig"].str.replace("_"," "))
tf_evt=TfidfVectorizer(analyzer="char_wb",ngram_range=(2,4),min_df=2).fit(Xtr["event_reason"])
def build(part):
    num=csr_matrix(scaler.transform(part[NUM].values))
    return hstack([num, tf_log.transform(part["log_sig"].str.replace("_"," ")), tf_evt.transform(part["event_reason"])]).tocsr()
Xtr_m,Xva_m,Xte_m=build(Xtr),build(Xva),build(Xte)
print(f"Matrice features : {Xtr_m.shape[1]} colonnes (11 num + {len(tf_log.vocabulary_)} log_sig + {len(tf_evt.vocabulary_)} event_reason)")

# ============================================================ 5) DESEQUILIBRE
sep("5) GESTION DU DESEQUILIBRE")
print(f"Classes equilibrees (100/type) -> SMOTE/oversampling NON necessaire. class_weight='balanced' active par securite.")

# ============================================================ 10) MODELES
sep("10) ENTRAINEMENT + COMPARAISON DES MODELES (sur validation)")
from sklearn.linear_model import LogisticRegression
from sklearn.svm import LinearSVC
from sklearn.ensemble import RandomForestClassifier
from xgboost import XGBClassifier
from lightgbm import LGBMClassifier
from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score, classification_report, confusion_matrix
models={
 "LogisticRegression":LogisticRegression(max_iter=1000,C=5,class_weight="balanced",n_jobs=-1),
 "LinearSVC":LinearSVC(C=1,class_weight="balanced"),
 "RandomForest":RandomForestClassifier(n_estimators=300,n_jobs=-1,class_weight="balanced",random_state=42),
 "XGBoost":XGBClassifier(n_estimators=400,max_depth=8,learning_rate=0.2,subsample=0.9,colsample_bytree=0.8,
                         tree_method="hist",n_jobs=-1,objective="multi:softprob",num_class=len(le.classes_),verbosity=0),
 "LightGBM":LGBMClassifier(n_estimators=400,max_depth=8,learning_rate=0.2,n_jobs=-1,verbose=-1),
}
res={}
for name,clf in models.items():
    clf.fit(Xtr_m,ytr)
    p=clf.predict(Xva_m)
    f1=f1_score(yva,p,average="macro"); acc=accuracy_score(yva,p)
    res[name]=(f1,acc,clf)
    print(f"  {name:20} val: F1_macro={f1:.4f}  accuracy={acc:.4f}")
best_name=max(res,key=lambda k:res[k][0]); best=res[best_name][2]
print(f"\n>>> MEILLEUR MODELE : {best_name} (F1_macro val={res[best_name][0]:.4f})")

# ============================================================ 11) METRIQUES (sur TEST)
sep("11) EVALUATION FINALE DU MEILLEUR MODELE (sur TEST)")
pt=best.predict(Xte_m)
acc=accuracy_score(yte,pt)
f1M=f1_score(yte,pt,average="macro"); f1W=f1_score(yte,pt,average="weighted")
preM=precision_score(yte,pt,average="macro",zero_division=0); recM=recall_score(yte,pt,average="macro",zero_division=0)
rep=classification_report(yte,pt,output_dict=True,zero_division=0)
strong=sum(1 for k,v in rep.items() if k not in("accuracy","macro avg","weighted avg") and isinstance(v,dict) and v["f1-score"]>=0.60)
perfect=sum(1 for k,v in rep.items() if k not in("accuracy","macro avg","weighted avg") and isinstance(v,dict) and v["f1-score"]>=0.90)
print(f"  Accuracy         : {acc:.4f}")
print(f"  F1 macro         : {f1M:.4f}   | F1 weighted : {f1W:.4f}")
print(f"  Precision macro  : {preM:.4f}   | Recall macro : {recM:.4f}")
print(f"  Classes F1>=0.60 : {strong}/120   | F1>=0.90 : {perfect}/120")
try:
    from sklearn.metrics import roc_auc_score
    if hasattr(best,"predict_proba"):
        auc=roc_auc_score(yte,best.predict_proba(Xte_m),multi_class="ovr",average="macro")
        print(f"  ROC-AUC (macro ovr): {auc:.4f}")
except Exception as e: pass
# plots : comparaison modeles + matrice de confusion
plt.figure(figsize=(8,4)); names=list(res); f1s=[res[n][0] for n in names]
plt.bar(names,f1s); plt.ylabel("F1 macro (val)"); plt.title("Comparaison des modeles"); plt.xticks(rotation=20)
plt.tight_layout(); plt.savefig(os.path.join(OUT,"model_comparison.png"),dpi=90); plt.close()
cm=confusion_matrix(yte,pt)
plt.figure(figsize=(9,8)); plt.imshow(cm,cmap="viridis"); plt.title(f"Matrice de confusion 120x120 ({best_name})")
plt.xlabel("predit"); plt.ylabel("vrai"); plt.colorbar()
plt.tight_layout(); plt.savefig(os.path.join(OUT,"confusion_matrix.png"),dpi=90); plt.close()
print("-> plots: artifacts/model_comparison.png, artifacts/confusion_matrix.png")

# ============================================================ 12) SAUVEGARDE
sep("12) SAUVEGARDE (modele + transformations)")
joblib.dump({"model":best,"model_name":best_name,"scaler":scaler,"tf_log":tf_log,"tf_evt":tf_evt,
             "label_encoder":le,"num_features":NUM},os.path.join(OUT,"ml_120_pipeline.joblib"))
with open(os.path.join(OUT,"REPORT.txt"),"w",encoding="utf-8") as f:
    f.write(f"MODELE: {best_name}\nAccuracy: {acc:.4f}\nF1 macro: {f1M:.4f}\nF1 weighted: {f1W:.4f}\n"
            f"Precision macro: {preM:.4f}\nRecall macro: {recM:.4f}\nClasses F1>=0.60: {strong}/120\n"
            f"Classes F1>=0.90: {perfect}/120\ntrain/val/test: {len(Xtr)}/{len(Xva)}/{len(Xte)}\n")
print(f"  -> artifacts/ml_120_pipeline.joblib (modele + scaler + 2 TF-IDF + label encoder)")
print(f"  -> artifacts/REPORT.txt")
print("\n=== PIPELINE ML TERMINE ===")
