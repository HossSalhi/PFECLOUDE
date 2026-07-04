#!/usr/bin/env python3
"""Rend le ML HELIOS CAPABLE des 120 classes (candidat, ne touche pas le live).

Étapes :
 1) charge le dataset RÉEL v5 (52 scénarios, features réelles) ;
 2) REMAPPE les 52 vers la taxonomie 120 (synonymes) ;
 3) pour chaque type des 120 SANS données réelles, synthétise des lignes À PARTIR de la
    distribution d'un ANALOGUE réel de même catégorie + bruit gaussien (in-manifold —
    PAS d'empreinte artificielle, contrairement à model_120 qui cassait en live) ;
 4) entraîne un XGBoost 120 classes (candidat) ;
 5) VALIDE : (a) holdout global, (b) F1 sur les classes RÉELLES seules (doit rester élevé
    = preuve que le réel ne s'effondre pas), (c) matrice de confusion réel vs synthétique.

Honnêteté : les ~68 classes synthétisées d'un analogue se CONFONDENT avec leur analogue sur des
signaux réels (elles partagent la distribution) -> le modèle est STRUCTURELLEMENT capable des 120,
mais seules les ~40-52 à données réelles sont fiables. C'est le maximum physiquement possible sans
vraies données pour les 68 non-déclenchables.
"""
import pandas as pd, numpy as np, json, sys
from collections import Counter

CSV = "/opt/pfe-ai-service/datasets/aiops_training_dataset_v5.csv"
OUT_CSV = "/tmp/dataset_120.csv"
LABEL = "scenario"
SEED = 42

CANON_120 = [
 "POD_CRASH_LOOP","POD_OOM_KILLED","POD_CPU_STRESS","POD_MEMORY_LEAK","POD_PENDING_INSUFFICIENT_RESOURCES",
 "POD_INIT_CONTAINER_FAILURE","POD_CONFIG_ERROR","POD_IMAGE_PULL_BACKOFF","POD_ERR_IMAGE_PULL","POD_STUCK_TERMINATING",
 "POD_EVICTED","POD_FAILED_PROBE","POD_READINESS_FAILURE","POD_LIVENESS_FAILURE","POD_CONTAINER_RUNTIME_ERROR",
 "POD_RESTART_STORM","POD_SECURITY_CONTEXT_ERROR","POD_SECRET_MISSING","POD_CONFIGMAP_MISSING","POD_TOO_MANY_RESTARTS",
 "DEPLOYMENT_UNAVAILABLE","DEPLOYMENT_ROLLOUT_STUCK","DEPLOYMENT_ROLLBACK_REQUIRED","REPLICA_SATURATION","HPA_SCALING_FAILURE",
 "HPA_OVER_SCALING","HPA_UNDER_SCALING","DEPLOYMENT_VERSION_MISMATCH","DEPLOYMENT_PROGRESS_DEADLINE_EXCEEDED",
 "SERVICE_UNAVAILABLE","SERVICE_NO_ENDPOINTS","NETWORK_PARTITION","NETWORK_PACKET_LOSS","NETWORK_HIGH_LATENCY",
 "NETWORK_POLICY_BLOCKING","DNS_FAILURE","DNS_SLOW_RESPONSE","INGRESS_UNAVAILABLE","INGRESS_5XX_ERRORS",
 "LOAD_BALANCER_UNAVAILABLE","LOAD_BALANCER_OVERLOAD","TCP_CONNECTION_FAILURE",
 "NODE_NOT_READY","NODE_HIGH_CPU","NODE_HIGH_MEMORY","NODE_DISK_PRESSURE","NODE_MEMORY_PRESSURE","NODE_PID_PRESSURE",
 "NODE_DISK_FULL","NODE_NETWORK_DOWN","NODE_REBOOT","NODE_KERNEL_PANIC","NODE_TIME_DRIFT","NODE_KUBELET_DOWN",
 "NODE_CONTAINERD_DOWN","NODE_FILESYSTEM_READONLY","NODE_UNREACHABLE",
 "API_SERVER_DOWN","API_SERVER_HIGH_LATENCY","ETCD_DOWN","ETCD_QUORUM_LOST","ETCD_HIGH_LATENCY","ETCD_DATABASE_CORRUPTION",
 "CONTROLLER_MANAGER_DOWN","SCHEDULER_DOWN","CERTIFICATE_EXPIRED","CERTIFICATE_NEAR_EXPIRATION","LEADER_ELECTION_FAILURE",
 "ADMISSION_WEBHOOK_FAILURE",
 "PVC_PENDING","PVC_LOST","PV_LOST","STORAGE_CLASS_ERROR","STORAGE_CAPACITY_EXCEEDED","VOLUME_MOUNT_FAILURE",
 "READ_ONLY_VOLUME","CSI_DRIVER_FAILURE","SNAPSHOT_FAILURE",
 "DATABASE_CONNECTIVITY","DB_CONNECTION_REFUSED","DB_CONNECTION_POOL_EXHAUSTION","DB_SLOW_QUERIES","DB_HIGH_CPU",
 "DB_HIGH_MEMORY","DB_DISK_FULL","DB_REPLICATION_LAG","DB_PRIMARY_DOWN","DB_DEADLOCK","DB_TRANSACTION_TIMEOUT","DB_TOO_MANY_CONNECTIONS",
 "LATENCY_DEGRADATION","HIGH_ERROR_RATE_5XX","HIGH_ERROR_RATE_4XX","HIGH_RESPONSE_TIME","REQUEST_TIMEOUT","THROUGHPUT_DROP",
 "TRAFFIC_SPIKE","MEMORY_LEAK_APPLICATION","THREAD_POOL_EXHAUSTION","CACHE_FAILURE",
 "SECRET_EXPOSED","UNAUTHORIZED_ACCESS","RBAC_MISCONFIGURATION","PRIVILEGED_CONTAINER","MALICIOUS_POD","CRYPTO_MINING_DETECTED",
 "DDOS_ATTACK","IMAGE_VULNERABILITY","TLS_CERTIFICATE_EXPIRED","SUSPICIOUS_NETWORK_ACTIVITY",
 "CASCADING_FAILURE","RESOURCE_CONTENTION","DEPENDENCY_FAILURE","PARTIAL_CLUSTER_OUTAGE","COMPLETE_CLUSTER_OUTAGE",
 "MULTI_NODE_FAILURE","MULTI_SERVICE_DEGRADATION","ANOMALOUS_TRAFFIC_PATTERN","PREDICTED_NODE_FAILURE","PREDICTED_DISK_EXHAUSTION",
]

# 52 scénario réel -> nom canonique 120 (synonymes)
REMAP = {
 "CPU_SATURATION":"POD_CPU_STRESS","MEMORY_LEAK":"POD_MEMORY_LEAK","ERROR_RATE_5XX_SPIKE":"HIGH_ERROR_RATE_5XX",
 "ERROR_RATE_4XX_SPIKE":"HIGH_ERROR_RATE_4XX","HIGH_LATENCY_P95":"HIGH_RESPONSE_TIME","HIGH_LATENCY_P99":"LATENCY_DEGRADATION",
 "REQUEST_RATE_DROP":"THROUGHPUT_DROP","NETWORK_DNS_FAILURE":"DNS_FAILURE","NETWORK_LATENCY_BETWEEN_NODES":"NETWORK_HIGH_LATENCY",
 "SERVICE_DISCOVERY_FAILURE":"SERVICE_NO_ENDPOINTS","INGRESS_BACKEND_DOWN":"INGRESS_UNAVAILABLE",
 "TLS_CERTIFICATE_EXPIRY":"TLS_CERTIFICATE_EXPIRED","NODE_HARDWARE_FAILURE":"NODE_KERNEL_PANIC",
 "NODE_DISK_IO_SATURATION":"NODE_DISK_PRESSURE","NODE_CLOCK_SKEW":"NODE_TIME_DRIFT","PVC_USAGE_HIGH":"STORAGE_CAPACITY_EXCEEDED",
 "PV_FILESYSTEM_CORRUPTION":"READ_ONLY_VOLUME","EXTERNAL_API_DEPENDENCY_DOWN":"DEPENDENCY_FAILURE",
 "UNAUTHORIZED_API_ACCESS_SPIKE":"UNAUTHORIZED_ACCESS","AUTH_BRUTE_FORCE_ATTEMPT":"SUSPICIOUS_NETWORK_ACTIVITY",
 "AUTH_TOKEN_EXPIRED":"UNAUTHORIZED_ACCESS","SECRET_ROTATION_FAILURE":"SECRET_EXPOSED","DB_HIGH_WRITE_LOAD":"DB_HIGH_CPU",
 "CIRCUIT_BREAKER_OPEN":"DEPENDENCY_FAILURE",
 # types observabilité maison hors 120 -> conservés comme "extra" (préfixe OBS_) pour ne pas polluer
 "METRICS_SCRAPE_FAILURE":"OBS_METRICS_SCRAPE_FAILURE","PROMETHEUS_OUT_OF_MEMORY":"OBS_PROMETHEUS_OOM",
 "LOKI_INGESTION_LAG":"OBS_LOKI_LAG",
}
# analogue explicite pour les manquants (sinon fallback par catégorie/préfixe)
ANALOG = {
 "POD_ERR_IMAGE_PULL":"POD_IMAGE_PULL_BACKOFF","POD_RESTART_STORM":"POD_CRASH_LOOP","POD_TOO_MANY_RESTARTS":"POD_CRASH_LOOP",
 "POD_FAILED_PROBE":"POD_READINESS_FAILURE","POD_CONFIG_ERROR":"POD_INIT_CONTAINER_FAILURE",
 "POD_SECRET_MISSING":"POD_INIT_CONTAINER_FAILURE","POD_CONFIGMAP_MISSING":"POD_INIT_CONTAINER_FAILURE",
 "POD_SECURITY_CONTEXT_ERROR":"POD_INIT_CONTAINER_FAILURE","POD_EVICTED":"NODE_DISK_PRESSURE",
 "POD_CONTAINER_RUNTIME_ERROR":"NODE_KUBELET_DOWN",
 "NODE_MEMORY_PRESSURE":"NODE_HIGH_MEMORY","NODE_PID_PRESSURE":"NODE_HIGH_CPU","NODE_REBOOT":"NODE_NOT_READY",
 "NODE_NETWORK_DOWN":"NODE_NOT_READY","NODE_CONTAINERD_DOWN":"NODE_KUBELET_DOWN","NODE_FILESYSTEM_READONLY":"READ_ONLY_VOLUME",
 "NODE_UNREACHABLE":"NODE_NOT_READY",
 "API_SERVER_DOWN":"NODE_KUBELET_DOWN","API_SERVER_HIGH_LATENCY":"HIGH_RESPONSE_TIME","ETCD_DOWN":"NODE_KUBELET_DOWN",
 "ETCD_QUORUM_LOST":"NODE_NOT_READY","ETCD_HIGH_LATENCY":"NODE_DISK_PRESSURE","ETCD_DATABASE_CORRUPTION":"READ_ONLY_VOLUME",
 "CONTROLLER_MANAGER_DOWN":"NODE_KUBELET_DOWN","SCHEDULER_DOWN":"NODE_KUBELET_DOWN","LEADER_ELECTION_FAILURE":"NODE_KUBELET_DOWN",
 "ADMISSION_WEBHOOK_FAILURE":"DEPENDENCY_FAILURE","CERTIFICATE_EXPIRED":"TLS_CERTIFICATE_EXPIRED",
 "CERTIFICATE_NEAR_EXPIRATION":"TLS_CERTIFICATE_EXPIRED",
 "PVC_LOST":"PVC_PENDING","PV_LOST":"READ_ONLY_VOLUME","STORAGE_CLASS_ERROR":"PVC_PENDING","VOLUME_MOUNT_FAILURE":"PVC_PENDING",
 "CSI_DRIVER_FAILURE":"PVC_PENDING","SNAPSHOT_FAILURE":"STORAGE_CAPACITY_EXCEEDED",
 "DATABASE_CONNECTIVITY":"DB_CONNECTION_REFUSED","DB_HIGH_MEMORY":"DB_HIGH_CPU","DB_PRIMARY_DOWN":"DB_CONNECTION_REFUSED",
 "DB_TRANSACTION_TIMEOUT":"DB_SLOW_QUERIES","DB_TOO_MANY_CONNECTIONS":"DB_CONNECTION_POOL_EXHAUSTION",
 "DEPLOYMENT_UNAVAILABLE":"POD_CRASH_LOOP","DEPLOYMENT_ROLLOUT_STUCK":"POD_CRASH_LOOP","DEPLOYMENT_ROLLBACK_REQUIRED":"HIGH_ERROR_RATE_5XX",
 "REPLICA_SATURATION":"POD_CPU_STRESS","HPA_SCALING_FAILURE":"POD_CPU_STRESS","HPA_OVER_SCALING":"POD_CPU_STRESS",
 "HPA_UNDER_SCALING":"POD_CPU_STRESS","DEPLOYMENT_VERSION_MISMATCH":"HIGH_ERROR_RATE_5XX","DEPLOYMENT_PROGRESS_DEADLINE_EXCEEDED":"POD_CRASH_LOOP",
 "SERVICE_UNAVAILABLE":"SERVICE_NO_ENDPOINTS","NETWORK_POLICY_BLOCKING":"NETWORK_PARTITION","DNS_SLOW_RESPONSE":"DNS_FAILURE",
 "INGRESS_5XX_ERRORS":"HIGH_ERROR_RATE_5XX","LOAD_BALANCER_UNAVAILABLE":"INGRESS_UNAVAILABLE","LOAD_BALANCER_OVERLOAD":"POD_CPU_STRESS",
 "TCP_CONNECTION_FAILURE":"NETWORK_PACKET_LOSS",
 "REQUEST_TIMEOUT":"HIGH_RESPONSE_TIME","TRAFFIC_SPIKE":"POD_CPU_STRESS","MEMORY_LEAK_APPLICATION":"POD_MEMORY_LEAK","CACHE_FAILURE":"DB_SLOW_QUERIES",
 "SECRET_EXPOSED":"UNAUTHORIZED_ACCESS","RBAC_MISCONFIGURATION":"UNAUTHORIZED_ACCESS","PRIVILEGED_CONTAINER":"SUSPICIOUS_NETWORK_ACTIVITY",
 "MALICIOUS_POD":"SUSPICIOUS_NETWORK_ACTIVITY","CRYPTO_MINING_DETECTED":"POD_CPU_STRESS","DDOS_ATTACK":"HIGH_ERROR_RATE_5XX",
 "IMAGE_VULNERABILITY":"UNAUTHORIZED_ACCESS",
 "CASCADING_FAILURE":"DEPENDENCY_FAILURE","RESOURCE_CONTENTION":"POD_CPU_STRESS","PARTIAL_CLUSTER_OUTAGE":"NODE_NOT_READY",
 "COMPLETE_CLUSTER_OUTAGE":"NODE_NOT_READY","MULTI_NODE_FAILURE":"NODE_NOT_READY","MULTI_SERVICE_DEGRADATION":"HIGH_RESPONSE_TIME",
 "ANOMALOUS_TRAFFIC_PATTERN":"HIGH_ERROR_RATE_5XX","PREDICTED_NODE_FAILURE":"NODE_HIGH_CPU","PREDICTED_DISK_EXHAUSTION":"NODE_DISK_FULL",
}

def main():
    rng = np.random.default_rng(SEED)
    df = pd.read_csv(CSV)
    df[LABEL] = df[LABEL].map(lambda s: REMAP.get(s, s))
    meta = {LABEL, "incident_label", "severity_label", "action_label"}
    feat = [c for c in df.columns if c not in meta and np.issubdtype(df[c].dtype, np.number)]
    present = set(df[LABEL].unique())
    real_types = sorted(t for t in present if not t.startswith("OBS_") and t not in ("NO_INCIDENT","UNKNOWN_ANOMALY"))
    missing = [t for t in CANON_120 if t not in present]
    print(f"features={len(feat)} | classes réelles présentes (mappées)={len(real_types)} | manquantes à synthétiser={len(missing)}")

    synth = []
    for t in missing:
        analog = ANALOG.get(t)
        if analog not in present:
            analog = "POD_CRASH_LOOP"  # fallback
        src = df[df[LABEL] == analog]
        n = min(120, max(60, len(src)))
        s = src.sample(n=n, replace=len(src) < n, random_state=SEED).copy()
        for c in feat:
            sd = df[c].std()
            if sd and np.isfinite(sd):
                s[c] = s[c] + rng.normal(0, 0.08 * sd, size=len(s))
        s[LABEL] = t
        synth.append(s)
    df120 = pd.concat([df] + synth, ignore_index=True)
    df120.to_csv(OUT_CSV, index=False)
    n_classes = df120[LABEL].nunique()
    covered = sum(1 for t in CANON_120 if t in set(df120[LABEL].unique()))
    print(f"DATASET 120 écrit : {OUT_CSV} | classes total={n_classes} | des 120 couverts={covered}/120 | lignes={len(df120)}")
    print(f"  (réelles fiables={len(real_types)} + synthétisées d'un analogue={len(missing)})")

    # ---- entraînement + validation ----
    from sklearn.model_selection import train_test_split
    from sklearn.preprocessing import LabelEncoder
    from sklearn.metrics import f1_score, accuracy_score
    from xgboost import XGBClassifier
    import joblib
    X = df120[feat].fillna(0.0).values
    le = LabelEncoder(); y = le.fit_transform(df120[LABEL].values)
    real_mask = df120[LABEL].isin(real_types).values
    Xtr, Xte, ytr, yte, mtr, mte = train_test_split(X, y, real_mask, test_size=0.2, random_state=SEED, stratify=y)
    clf = XGBClassifier(n_estimators=300, max_depth=8, learning_rate=0.15, subsample=0.9,
                        colsample_bytree=0.8, tree_method="hist", n_jobs=3,
                        objective="multi:softprob", num_class=len(le.classes_))
    clf.fit(Xtr, ytr)
    pred = clf.predict(Xte)
    f1_all = f1_score(yte, pred, average="macro")
    # F1 sur les classes RÉELLES seules (preuve de non-effondrement)
    real_labels = set(le.transform(real_types))
    idx_real = [i for i, v in enumerate(yte) if v in real_labels]
    f1_real = f1_score([yte[i] for i in idx_real], [pred[i] for i in idx_real], average="macro")
    acc_real = accuracy_score([yte[i] for i in idx_real], [pred[i] for i in idx_real])
    joblib.dump({"model": clf, "label_encoder": le, "features": feat}, "/tmp/model_120_candidate.joblib")
    print("\n===== RÉSULTATS (candidat 120 classes) =====")
    print(f"  classes du modèle          : {len(le.classes_)} (dont 120 canoniques couverts={covered})")
    print(f"  F1 macro GLOBAL (holdout)  : {f1_all:.3f}")
    print(f"  F1 macro CLASSES RÉELLES   : {f1_real:.3f}   <-- doit rester élevé (non-effondrement)")
    print(f"  Accuracy classes réelles   : {acc_real:.3f}")
    print("  modèle candidat -> /tmp/model_120_candidate.joblib (NON déployé live)")

if __name__ == "__main__":
    main()
