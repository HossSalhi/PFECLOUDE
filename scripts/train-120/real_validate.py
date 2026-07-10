#!/usr/bin/env python3
"""VALIDATION REELLE du modele ML sur le vrai cluster (via chaos toolkit).
Capture les VRAIS event_reason + message d'evenement K8s -> extrait log_sig (meme Drain3-lite qu'a
l'entrainement) -> predit avec ml_120_realistic.joblib -> compare au type attendu. Prometheus down ->
metriques neutralisees (l'ablation a montre qu'elles ne portent quasi rien : c'est log_sig qui classe).

Usage : python real_validate.py <ns> <app> <TYPE_ATTENDU>
"""
import subprocess, re, json, os, sys
import numpy as np, pandas as pd, joblib
from scipy.sparse import hstack, csr_matrix
D=os.path.dirname(os.path.abspath(__file__))
art=joblib.load(os.path.join(D,"artifacts","ml_120_realistic.joblib"))
M,SC,TL,TE,LE,NUM=art["model"],art["scaler"],art["tf_log"],art["tf_evt"],art["label_encoder"],art["num_features"]

STOP=set("the a an of to in on is are was for and or with without not no that this it its as at be by from into over under after before your you we our their his her los pod node error rate high low failed failure down".split())
def extract_sig(line):
    s=re.sub(r"cve-\d+","cve",str(line).lower())
    s=re.sub(r"\b\d+(\.\d+)*\b|0x[0-9a-f]+|[0-9a-f]{8,}|\beth0\b|:\d+"," ",s)
    toks=[w for w in re.split(r"[^a-z]+",s) if len(w)>2 and w not in STOP]
    return "_".join(toks[:6]) if toks else "unknown"

def sh(cmd): return subprocess.run(cmd,capture_output=True,text=True,shell=True).stdout

FAULT={"BackOff","OOMKilling","Unhealthy","Failed","FailedMount","FailedScheduling","Evicted",
       "FailedKillPod","ProvisioningFailed","NetworkNotReady","FailedCreatePodSandBox","ErrImagePull",
       "ImagePullBackOff","CrashLoopBackOff","NodeNotReady"}
def capture(ns, app):
    """Signal fiable = statut du conteneur (waiting.reason / lastState terminated OOMKilled/Error),
    complete par le message d'evenement de panne pour le log_sig."""
    reason="None"; sigsrc=""
    # 1) STATUT CONTENEUR (le plus fiable)
    try:
        for pod in json.loads(sh(f'kubectl get pods -n {ns} -l app={app} -o json')).get("items",[]):
            for cs in pod.get("status",{}).get("containerStatuses",[]):
                w=(cs.get("state",{}) or {}).get("waiting",{}) or {}
                lt=(cs.get("lastState",{}) or {}).get("terminated",{}) or {}
                if w.get("reason") in FAULT: reason=w["reason"]; sigsrc=w.get("message","") or w["reason"]
                elif lt.get("reason") in ("OOMKilled","Error","ContainerCannotRun"): reason=lt["reason"]; sigsrc=sigsrc or lt["reason"]
    except Exception: pass
    # 2) MESSAGE D'EVENEMENT de panne (source riche pour log_sig)
    try:
        items=json.loads(sh(f'kubectl get events -n {ns} -o json')).get("items",[])
        rel=[e for e in items if app in json.dumps(e.get("involvedObject",{}))]
        rel.sort(key=lambda e:e.get("lastTimestamp") or "", reverse=True)
        for e in rel:
            if e.get("reason") in FAULT or e.get("type")=="Warning":
                if reason=="None": reason=e.get("reason","None")
                sigsrc=e.get("message","") or sigsrc; break
    except Exception: pass
    return reason, sigsrc, extract_sig(sigsrc or reason)

def predict(event_reason, log_sig):
    row=pd.DataFrame([{**{n:0.0 for n in NUM},"event_reason":event_reason or "None","log_txt":log_sig.replace("_"," ")}])
    X=hstack([csr_matrix(SC.transform(row[NUM].values)),TL.transform(row["log_txt"]),TE.transform(row["event_reason"])]).tocsr()
    p=M.predict_proba(X)[0]; top=np.argsort(p)[::-1][:3]
    return [(LE.inverse_transform([i])[0], float(p[i])) for i in top]

if __name__=="__main__":
    ns,app,exp=(sys.argv+["pfe","podinfo","?"])[1:4]
    reason,msg,sig=capture(ns,app)
    top=predict(reason,sig)
    ok="OK" if top[0][0]==exp else "X"
    print(json.dumps({"expected":exp,"event_reason":reason,"log_sig":sig,
                      "msg":msg[:90],"pred":top[0][0],"conf":round(top[0][1],3),
                      "top3":[f"{t} {c:.2f}" for t,c in top],"match":ok},ensure_ascii=False))
