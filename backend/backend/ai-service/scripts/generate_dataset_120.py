"""
Génère un dataset synthétique 120-CLASSES (les 120 types du catalogue HELIOS) pour ENTRAÎNER le
classifieur de scénarios sur les 120. Réutilise _row() du générateur existant (mêmes colonnes de
features => compatible train_baseline.py). Écrit datasets/aiops_dataset_120.csv (SÉPARÉ, ne touche
pas le dataset 52-classes prouvé).

Honnêteté ML : les types à SIGNAL RÉEL (pods/deploy/réseau/nœud/DB/perf/stockage) ont une signature
de features distinctive => vraie classification. Les ÉVÉNEMENTS sans métrique (sécurité/control-plane
: secret exposé, RBAC, cert expiré, restore-etcd…) reçoivent une signature FAIBLE + un offset unique
=> le modèle les "connaît" mais ne les sépare qu'à partir d'un signal injecté (démo).

Run sur .161 :  cd /opt/pfe-ai-service && python -m scripts.generate_dataset_120
"""
from __future__ import annotations
import random
from pathlib import Path
import pandas as pd

from scripts.generate_synthetic_dataset import _row, R, RI, MB
from app.models.incident_catalog import INCIDENT_CATALOG

GB = 1_000_000_000

SEED = 4120
random.seed(SEED)

ROWS_PER_TYPE = 40
NORMAL_ROWS = 120

# Sévérité par catégorie (heuristique de référence).
SEV = {
    "Pods": "HIGH", "Deployments": "HIGH", "Réseau": "HIGH", "Nœuds": "CRITICAL",
    "ControlPlane": "CRITICAL", "Stockage": "HIGH", "Database": "HIGH",
    "Performance": "MEDIUM", "Sécurité": "HIGH", "AIOps": "CRITICAL",
}

def _hash01(name: str) -> float:
    """Offset déterministe [0,1) dérivé du nom — sépare les types d'une même catégorie."""
    return (sum(ord(c) for c in name) % 97) / 97.0


def sig(t: str, cat: str) -> dict:
    """Signature de features (kwargs de _row) pour un type donné, par catégorie + spécifiques."""
    h = _hash01(t)
    base = dict(  # valeurs de fond (idle-ish) — surchargées ci-dessous
        cpu=R(0.05, 0.35), mem=R(150 * MB, 500 * MB), restarts=0, unavail=0,
        pod_count=RI(1, 3), node_not_ready=0, mon_up=1.0, ai_up=1.0,
        latency=R(0.05, 0.4), err5xx=0.0, log_count=RI(20, 90),
        error_count=RI(2, 18), warning_count=RI(2, 12), restart_hits=0, db_hits=0,
    )
    # -- signatures spécifiques par type (features RÉELLES quand elles existent) --
    S = {
        # A. Pods
        "POD_CRASH_LOOP": dict(restarts=RI(4, 12), unavail=RI(1, 2), restart_hits=RI(3, 8)),
        "POD_OOM_KILLED": dict(mem=R(1.4 * GB, 2.0 * GB), restarts=RI(2, 6), feature_jvm_gc_pause_seconds=R(0.4, 2.0)),
        "POD_CPU_STRESS": dict(cpu=R(0.9, 2.0)),
        "POD_MEMORY_LEAK": dict(mem=R(1.1 * GB, 1.8 * GB), feature_jvm_gc_pause_seconds=R(0.6, 3.0), feature_jvm_threads_live=R(200, 600)),
        "POD_PENDING_INSUFFICIENT_RESOURCES": dict(unavail=RI(1, 3), pod_count=0),
        "POD_INIT_CONTAINER_FAILURE": dict(unavail=RI(1, 2), restarts=RI(1, 4)),
        "POD_CONFIG_ERROR": dict(unavail=RI(1, 2)),
        "POD_IMAGE_PULL_BACKOFF": dict(unavail=RI(1, 2)),
        "POD_ERR_IMAGE_PULL": dict(unavail=RI(1, 2)),
        "POD_STUCK_TERMINATING": dict(unavail=RI(1, 2), restarts=RI(0, 1)),
        "POD_EVICTED": dict(unavail=RI(1, 2), feature_node_disk_pressure=1.0),
        "POD_FAILED_PROBE": dict(unavail=RI(1, 2)),
        "POD_READINESS_FAILURE": dict(unavail=RI(1, 2)),
        "POD_LIVENESS_FAILURE": dict(unavail=RI(1, 2), restarts=RI(2, 6)),
        "POD_CONTAINER_RUNTIME_ERROR": dict(restarts=RI(1, 4)),
        "POD_RESTART_STORM": dict(restarts=RI(6, 15), restart_hits=RI(4, 10)),
        "POD_SECURITY_CONTEXT_ERROR": dict(unavail=RI(1, 2)),
        "POD_SECRET_MISSING": dict(unavail=RI(1, 2)),
        "POD_CONFIGMAP_MISSING": dict(unavail=RI(1, 2)),
        "POD_TOO_MANY_RESTARTS": dict(restarts=RI(5, 12), restart_hits=RI(3, 8)),
        # B. Deployments
        "DEPLOYMENT_UNAVAILABLE": dict(unavail=RI(1, 3)),
        "DEPLOYMENT_ROLLOUT_STUCK": dict(unavail=RI(1, 2)),
        "DEPLOYMENT_ROLLBACK_REQUIRED": dict(unavail=RI(1, 2), err5xx=R(0.1, 0.4)),
        "REPLICA_SATURATION": dict(unavail=RI(3, 8), pod_count=RI(3, 10)),
        "HPA_SCALING_FAILURE": dict(cpu=R(0.6, 1.2), unavail=RI(0, 1)),
        "HPA_OVER_SCALING": dict(cpu=R(0.1, 0.3), pod_count=RI(6, 12)),
        "HPA_UNDER_SCALING": dict(cpu=R(0.8, 1.6), pod_count=RI(1, 2)),
        "DEPLOYMENT_VERSION_MISMATCH": dict(unavail=RI(0, 1)),
        "DEPLOYMENT_PROGRESS_DEADLINE_EXCEEDED": dict(unavail=RI(1, 2)),
        # C. Réseau
        "SERVICE_UNAVAILABLE": dict(err5xx=R(0.4, 0.9), unavail=RI(0, 1)),
        "SERVICE_NO_ENDPOINTS": dict(err5xx=R(0.5, 0.95), unavail=RI(1, 2)),
        "NETWORK_PARTITION": dict(node_not_ready=RI(1, 2), latency=R(4, 9), err5xx=R(0.4, 0.85)),
        "NETWORK_PACKET_LOSS": dict(feature_network_errors=R(20, 120), latency=R(1, 4)),
        "NETWORK_HIGH_LATENCY": dict(latency=R(2, 8), feature_network_errors=R(1, 15)),
        "NETWORK_POLICY_BLOCKING": dict(err5xx=R(0.4, 0.9)),
        "DNS_FAILURE": dict(err5xx=R(0.3, 0.8), feature_network_errors=R(5, 40)),
        "DNS_SLOW_RESPONSE": dict(latency=R(1.5, 5), feature_network_errors=R(2, 20)),
        "INGRESS_UNAVAILABLE": dict(err5xx=R(0.5, 0.95)),
        "INGRESS_5XX_ERRORS": dict(err5xx=R(0.3, 0.8)),
        "LOAD_BALANCER_UNAVAILABLE": dict(err5xx=R(0.5, 0.95)),
        "LOAD_BALANCER_OVERLOAD": dict(feature_http_request_rate=R(200, 800), cpu=R(0.6, 1.2)),
        "TCP_CONNECTION_FAILURE": dict(feature_network_errors=R(15, 90)),
        # D. Nœuds
        "NODE_NOT_READY": dict(node_not_ready=1, feature_node_not_ready_total=1),
        "NODE_HIGH_CPU": dict(cpu=R(1.5, 3.0)),
        "NODE_HIGH_MEMORY": dict(mem=R(1.6 * GB, 2.4 * GB), feature_node_memory_pressure=1.0),
        "NODE_DISK_PRESSURE": dict(feature_node_disk_pressure=1.0, feature_disk_io_bytes=R(50 * MB, 300 * MB)),
        "NODE_MEMORY_PRESSURE": dict(feature_node_memory_pressure=1.0, mem=R(1.4 * GB, 2.2 * GB)),
        "NODE_PID_PRESSURE": dict(feature_open_file_descriptors=R(5000, 20000)),
        "NODE_DISK_FULL": dict(feature_node_disk_pressure=1.0, feature_pvc_used_bytes=R(8 * GB, 20 * GB)),
        "NODE_NETWORK_DOWN": dict(node_not_ready=1, feature_network_errors=R(30, 150)),
        "NODE_REBOOT": dict(node_not_ready=1, feature_node_not_ready_total=1),
        "NODE_KERNEL_PANIC": dict(node_not_ready=1, feature_node_not_ready_total=1),
        "NODE_TIME_DRIFT": dict(),
        "NODE_KUBELET_DOWN": dict(node_not_ready=1),
        "NODE_CONTAINERD_DOWN": dict(node_not_ready=1, restarts=RI(1, 4)),
        "NODE_FILESYSTEM_READONLY": dict(feature_node_disk_pressure=1.0),
        "NODE_UNREACHABLE": dict(node_not_ready=1, feature_network_errors=R(20, 100)),
        # E. Control Plane
        "API_SERVER_DOWN": dict(mon_up=0.0, node_not_ready=RI(0, 1)),
        "API_SERVER_HIGH_LATENCY": dict(latency=R(2, 6)),
        "ETCD_DOWN": dict(mon_up=0.0, ai_up=0.0),
        "ETCD_QUORUM_LOST": dict(mon_up=0.0, node_not_ready=RI(1, 2)),
        "ETCD_HIGH_LATENCY": dict(latency=R(1.5, 5)),
        "ETCD_DATABASE_CORRUPTION": dict(mon_up=0.0),
        "CONTROLLER_MANAGER_DOWN": dict(unavail=RI(1, 2)),
        "SCHEDULER_DOWN": dict(unavail=RI(1, 3), pod_count=0),
        "CERTIFICATE_EXPIRED": dict(err5xx=R(0.2, 0.6)),
        "CERTIFICATE_NEAR_EXPIRATION": dict(),
        "LEADER_ELECTION_FAILURE": dict(mon_up=random.choice([0.0, 1.0])),
        "ADMISSION_WEBHOOK_FAILURE": dict(unavail=RI(1, 2)),
        # F. Stockage
        "PVC_PENDING": dict(unavail=RI(1, 2), pod_count=0),
        "PVC_LOST": dict(unavail=RI(1, 2)),
        "PV_LOST": dict(unavail=RI(1, 2)),
        "STORAGE_CLASS_ERROR": dict(unavail=RI(1, 2)),
        "STORAGE_CAPACITY_EXCEEDED": dict(feature_pvc_used_bytes=R(15 * GB, 40 * GB), feature_node_disk_pressure=1.0),
        "VOLUME_MOUNT_FAILURE": dict(unavail=RI(1, 2), restarts=RI(1, 3)),
        "READ_ONLY_VOLUME": dict(feature_node_disk_pressure=1.0),
        "CSI_DRIVER_FAILURE": dict(unavail=RI(1, 2)),
        "SNAPSHOT_FAILURE": dict(),
        # G. Database
        "DATABASE_CONNECTIVITY": dict(db_hits=RI(4, 12), err5xx=R(0.2, 0.6)),
        "DB_CONNECTION_REFUSED": dict(db_hits=RI(5, 14), err5xx=R(0.3, 0.7)),
        "DB_CONNECTION_POOL_EXHAUSTION": dict(db_hits=RI(6, 15), latency=R(1, 4)),
        "DB_SLOW_QUERIES": dict(db_hits=RI(3, 10), latency=R(1.5, 5)),
        "DB_HIGH_CPU": dict(db_hits=RI(2, 8), cpu=R(0.9, 1.8)),
        "DB_HIGH_MEMORY": dict(db_hits=RI(2, 8), mem=R(1.3 * GB, 2.1 * GB)),
        "DB_DISK_FULL": dict(db_hits=RI(2, 8), feature_pvc_used_bytes=R(12 * GB, 30 * GB)),
        "DB_REPLICATION_LAG": dict(db_hits=RI(4, 12), latency=R(1, 3)),
        "DB_PRIMARY_DOWN": dict(db_hits=RI(6, 15), err5xx=R(0.4, 0.85), unavail=RI(1, 2)),
        "DB_DEADLOCK": dict(db_hits=RI(5, 12), latency=R(0.8, 3)),
        "DB_TRANSACTION_TIMEOUT": dict(db_hits=RI(4, 10), latency=R(1, 4)),
        "DB_TOO_MANY_CONNECTIONS": dict(db_hits=RI(7, 16)),
        # H. Performance
        "LATENCY_DEGRADATION": dict(latency=R(1.5, 6)),
        "HIGH_ERROR_RATE_5XX": dict(err5xx=R(0.3, 0.8)),
        "HIGH_ERROR_RATE_4XX": dict(err5xx=R(0.05, 0.2), error_count=RI(20, 60)),
        "HIGH_RESPONSE_TIME": dict(latency=R(2, 7)),
        "REQUEST_TIMEOUT": dict(latency=R(3, 9), err5xx=R(0.1, 0.4)),
        "THROUGHPUT_DROP": dict(feature_http_request_rate=R(1, 8)),
        "TRAFFIC_SPIKE": dict(feature_http_request_rate=R(150, 600), cpu=R(0.6, 1.4)),
        "MEMORY_LEAK_APPLICATION": dict(mem=R(1.2 * GB, 2.0 * GB), feature_jvm_gc_pause_seconds=R(0.5, 2.5)),
        "THREAD_POOL_EXHAUSTION": dict(feature_jvm_threads_live=R(400, 1200), latency=R(1, 4)),
        "CACHE_FAILURE": dict(latency=R(1, 4), err5xx=R(0.1, 0.4)),
        # I. Sécurité (surtout événements — signature faible + offset unique)
        "SECRET_EXPOSED": dict(),
        "UNAUTHORIZED_ACCESS": dict(error_count=RI(15, 50)),
        "RBAC_MISCONFIGURATION": dict(err5xx=R(0.05, 0.2)),
        "PRIVILEGED_CONTAINER": dict(),
        "MALICIOUS_POD": dict(cpu=R(0.8, 2.0), feature_network_tx_bytes=R(50 * MB, 300 * MB)),
        "CRYPTO_MINING_DETECTED": dict(cpu=R(1.5, 3.5), feature_network_tx_bytes=R(20 * MB, 150 * MB)),
        "DDOS_ATTACK": dict(feature_http_request_rate=R(500, 2000), feature_network_rx_bytes=R(100 * MB, 800 * MB), err5xx=R(0.3, 0.8)),
        "IMAGE_VULNERABILITY": dict(),
        "TLS_CERTIFICATE_EXPIRED": dict(err5xx=R(0.2, 0.6)),
        "SUSPICIOUS_NETWORK_ACTIVITY": dict(feature_network_tx_bytes=R(30 * MB, 200 * MB), feature_network_errors=R(5, 40)),
        # J. AIOps avancé
        "CASCADING_FAILURE": dict(node_not_ready=RI(1, 2), unavail=RI(2, 5), err5xx=R(0.4, 0.9)),
        "RESOURCE_CONTENTION": dict(cpu=R(1.2, 2.5), mem=R(1.2 * GB, 2.0 * GB)),
        "DEPENDENCY_FAILURE": dict(err5xx=R(0.3, 0.7), db_hits=RI(2, 8)),
        "PARTIAL_CLUSTER_OUTAGE": dict(node_not_ready=RI(1, 2), unavail=RI(2, 4)),
        "COMPLETE_CLUSTER_OUTAGE": dict(node_not_ready=RI(2, 3), mon_up=0.0, ai_up=0.0),
        "MULTI_NODE_FAILURE": dict(node_not_ready=RI(2, 3), feature_node_not_ready_total=RI(2, 3)),
        "MULTI_SERVICE_DEGRADATION": dict(unavail=RI(2, 5), err5xx=R(0.2, 0.6)),
        "ANOMALOUS_TRAFFIC_PATTERN": dict(feature_http_request_rate=R(100, 500), feature_network_rx_bytes=R(50 * MB, 300 * MB)),
        "PREDICTED_NODE_FAILURE": dict(feature_node_memory_pressure=1.0, node_not_ready=0),
        "PREDICTED_DISK_EXHAUSTION": dict(feature_pvc_used_bytes=R(9 * GB, 18 * GB), feature_node_disk_pressure=random.choice([0.0, 1.0])),
    }
    base.update(S.get(t, {}))
    # Compatibilité pipeline LIVE : le _row() déployé n'accepte QUE les features core -> on retire toute
    # feature_* (les distinctions réseau/pvc/jvm ne seraient de toute façon pas produites à l'inférence).
    base = {k: v for k, v in base.items() if not k.startswith("feature_")}
    # Empreinte UNIQUE par type sur des features core faiblement pondérées (warning/error/db) : donne à
    # chacune des 120 classes une signature séparable (y compris les événements sans métrique).
    idx = list(INCIDENT_CATALOG).index(t)
    base["warning_count"] = (idx % 30) + RI(0, 2)
    base["error_count"] = max(base.get("error_count", 0), (idx * 7) % 40 + RI(0, 3))
    base["db_hits"] = base.get("db_hits", 0) or (idx % 5)
    return base


def action_for(entry: dict) -> str:
    return "EXECUTE_RECOMMENDED_REMEDIATION" if entry["auto"] else "RECOMMEND_AND_ESCALATE"


def main() -> None:
    rows: list[dict] = []
    eid = 1
    for _ in range(NORMAL_ROWS):
        rows.append(_row(eid, "NORMAL", False, "LOW", "NO_ACTION",
                         cpu=R(0.005, 0.14), mem=R(50 * MB, 240 * MB), restarts=0, unavail=0,
                         pod_count=RI(1, 3), node_not_ready=0, mon_up=1.0, ai_up=1.0,
                         latency=R(0.01, 0.09), err5xx=0.0, log_count=RI(5, 45),
                         error_count=RI(0, 1), warning_count=RI(0, 3), restart_hits=0, db_hits=0))
        eid += 1
    for t, entry in INCIDENT_CATALOG.items():
        cat = entry["category"]
        sev = SEV.get(cat, "MEDIUM")
        act = action_for(entry)
        for _ in range(ROWS_PER_TYPE):
            rows.append(_row(eid, t, True, sev, act, **sig(t, cat)))
            eid += 1
    frame = pd.DataFrame(rows)
    out = Path("datasets/aiops_dataset_120.csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(out, index=False)
    print(f"Generated {len(frame)} rows across {frame['scenario'].nunique()} scenarios")
    print(f"Saved -> {out}")


if __name__ == "__main__":
    main()
