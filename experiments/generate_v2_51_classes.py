"""
Generate a synthetic AIOps training dataset — 51 incident types as per INCIDENT_TAXONOMY.md
Outputs datasets/aiops_training_dataset_v2.csv ready for train_baseline.py.
Run from the ai-service/ directory:  python -m scripts.generate_v2_51_classes
"""
from __future__ import annotations

import random
from pathlib import Path

import numpy as np
import pandas as pd

SEED = 42
random.seed(SEED)
np.random.seed(SEED)

N_EMBEDDING = 384
EMBEDDING_COLS = [f"feature_log_embedding_{i:03d}" for i in range(N_EMBEDDING)]
MB = 1_000_000
GB = 1_000_000_000


# ── helpers identiques au générateur v1 ──────────────────────────────────────

def _log_risk(error_ratio: float, restart_hits: float, log_count: float) -> float:
    return min(
        0.55 * error_ratio
        + 0.25 * min(restart_hits / 5.0, 1.0)
        + 0.20 * min(log_count / 200.0, 1.0),
        1.0,
    )


def _metric_risk(cpu, mem, restarts, unavail, node_not_ready, latency, err5xx, mon_up, ai_up):
    score = (
        0.18 * min(cpu / 2.0, 1.0)
        + 0.22 * min(mem / 2e9, 1.0)
        + 0.12 * min(restarts / 10.0, 1.0)
        + 0.12 * min(unavail / 3.0, 1.0)
        + 0.09 * min(node_not_ready / 2.0, 1.0)
        + 0.09 * min(latency / 5.0, 1.0)
        + 0.10 * min(err5xx, 1.0)
        + 0.04 * (1.0 - mon_up)
        + 0.04 * (1.0 - ai_up)
    )
    return min(score, 1.0)


def _embeddings(incident: bool) -> dict[str, float]:
    std = 0.08 if incident else 0.02
    vec = np.random.normal(0, std, N_EMBEDDING).astype(np.float32)
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec /= norm
    return {col: float(v) for col, v in zip(EMBEDDING_COLS, vec)}


def R(a: float, b: float) -> float:
    return random.uniform(a, b)


def RI(a: int, b: int) -> int:
    return random.randint(a, b)


def _row(eid, scenario, incident_label, severity, action, cpu, mem, restarts, unavail,
         pod_count, node_not_ready, mon_up, ai_up, latency, err5xx,
         log_count, error_count, warning_count, restart_hits, db_hits):
    error_ratio = error_count / max(log_count, 1)
    unique_tpl = max(1, int(log_count * random.uniform(0.3, 0.7)))
    lr = _log_risk(error_ratio, restart_hits, log_count)
    mr = _metric_risk(cpu, mem, restarts, unavail, node_not_ready, latency, err5xx, mon_up, ai_up)

    base = {
        "event_id": eid,
        "scenario": scenario,
        "namespace": "pfe",
        "app": "backend",
        "window_minutes": 5,
        "end_time": None,
        "incident_label": incident_label,
        "severity_label": severity,
        "action_label": action,
        "feature_log_line_count": float(log_count),
        "feature_error_line_count": float(error_count),
        "feature_warning_line_count": float(warning_count),
        "feature_restart_keyword_hits": float(restart_hits),
        "feature_db_keyword_hits": float(db_hits),
        "feature_unique_log_templates": float(unique_tpl),
        "feature_error_ratio": round(error_ratio, 5),
        "feature_cpu_usage_cores": round(cpu, 5),
        "feature_memory_working_set_bytes": round(mem, 1),
        "feature_pod_restarts_total": float(restarts),
        "feature_unavailable_replicas": float(unavail),
        "feature_pod_count": float(pod_count),
        "feature_node_not_ready_count": float(node_not_ready),
        "feature_monitoring_vm_up": float(mon_up),
        "feature_ai_vm_up": float(ai_up),
        "feature_http_latency_p95_seconds": round(latency, 5),
        "feature_http_5xx_ratio": round(err5xx, 5),
        "feature_log_risk_score": round(lr, 5),
        "feature_metric_risk_score": round(mr, 5),
        "feature_overall_risk_score": round(0.5 * lr + 0.5 * mr, 5),
    }
    base.update(_embeddings(incident_label))
    return base


def add_scenario(rows, eid_start, scenario, count, **kwargs):
    """Compact scenario generator: takes feature ranges and generates N rows."""
    for _ in range(count):
        rows.append(_row(
            eid_start, scenario,
            kwargs["incident_label"], kwargs["severity"], kwargs["action"],
            cpu=R(*kwargs["cpu"]),
            mem=R(*kwargs["mem"]),
            restarts=RI(*kwargs["restarts"]),
            unavail=RI(*kwargs["unavail"]),
            pod_count=RI(*kwargs.get("pod_count", (1, 2))),
            node_not_ready=RI(*kwargs.get("node_not_ready", (0, 0))),
            mon_up=kwargs.get("mon_up", 1.0),
            ai_up=kwargs.get("ai_up", 1.0),
            latency=R(*kwargs["latency"]),
            err5xx=R(*kwargs["err5xx"]),
            log_count=RI(*kwargs["log_count"]),
            error_count=RI(*kwargs["error_count"]),
            warning_count=RI(*kwargs.get("warning_count", (0, 5))),
            restart_hits=RI(*kwargs.get("restart_hits", (0, 0))),
            db_hits=RI(*kwargs.get("db_hits", (0, 0))),
        ))
        eid_start += 1
    return eid_start


def main():
    rows = []
    eid = 1

    # ════════════════════════════════════════════════════════════════
    # NORMAL baseline (200 rows pour bien apprendre le "rien à signaler")
    # ════════════════════════════════════════════════════════════════
    eid = add_scenario(rows, eid, "NO_INCIDENT", 200,
        incident_label=False, severity="LOW", action="NO_ACTION",
        cpu=(0.005, 0.14), mem=(50*MB, 240*MB),
        restarts=(0, 0), unavail=(0, 0),
        latency=(0.01, 0.09), err5xx=(0.0, 0.0),
        log_count=(5, 45), error_count=(0, 1))

    # ════════════════════════════════════════════════════════════════
    # CATÉGORIE A — POD-LEVEL (8 types × 90 rows)
    # ════════════════════════════════════════════════════════════════
    eid = add_scenario(rows, eid, "POD_CRASH_LOOP", 90,
        incident_label=True, severity="HIGH", action="RESTART_POD",
        cpu=(0.1, 0.6), mem=(100*MB, 500*MB),
        restarts=(5, 15), unavail=(1, 2),
        latency=(0.2, 1.5), err5xx=(0.05, 0.25),
        log_count=(80, 220), error_count=(15, 60),
        restart_hits=(3, 8))

    eid = add_scenario(rows, eid, "POD_OOM_KILLED", 90,
        incident_label=True, severity="CRITICAL", action="SCALE_DEPLOYMENT",
        cpu=(0.05, 0.4), mem=(2.5*GB, 3.8*GB),
        restarts=(3, 12), unavail=(1, 2),
        latency=(0.5, 3.0), err5xx=(0.10, 0.40),
        log_count=(100, 250), error_count=(20, 80),
        restart_hits=(2, 6))

    eid = add_scenario(rows, eid, "POD_IMAGE_PULL_BACKOFF", 90,
        incident_label=True, severity="HIGH", action="ESCALATION",
        cpu=(0.0, 0.05), mem=(0, 50*MB),
        restarts=(0, 1), unavail=(1, 3),
        pod_count=(0, 1), latency=(0.0, 0.1), err5xx=(0.0, 0.0),
        log_count=(5, 30), error_count=(0, 3))

    eid = add_scenario(rows, eid, "POD_PENDING_INSUFFICIENT_RESOURCES", 90,
        incident_label=True, severity="HIGH", action="SCALE_DOWN",
        cpu=(0.0, 0.05), mem=(0, 50*MB),
        restarts=(0, 0), unavail=(1, 3),
        pod_count=(0, 1), latency=(0.0, 0.1), err5xx=(0.0, 0.0),
        log_count=(5, 30), error_count=(0, 2))

    eid = add_scenario(rows, eid, "POD_STUCK_TERMINATING", 90,
        incident_label=True, severity="MEDIUM", action="FORCE_RECREATE_DEPLOYMENT",
        cpu=(0.05, 0.3), mem=(100*MB, 400*MB),
        restarts=(0, 1), unavail=(0, 1),
        latency=(0.1, 0.5), err5xx=(0.0, 0.05),
        log_count=(20, 80), error_count=(2, 10))

    eid = add_scenario(rows, eid, "POD_LIVENESS_FAILURE", 90,
        incident_label=True, severity="HIGH", action="RESTART_POD",
        cpu=(0.1, 0.5), mem=(150*MB, 450*MB),
        restarts=(2, 6), unavail=(0, 1),
        latency=(0.3, 2.0), err5xx=(0.05, 0.20),
        log_count=(50, 150), error_count=(8, 30),
        restart_hits=(2, 5))

    eid = add_scenario(rows, eid, "POD_READINESS_FAILURE", 90,
        incident_label=True, severity="MEDIUM", action="INVESTIGATION",
        cpu=(0.1, 0.5), mem=(150*MB, 450*MB),
        restarts=(0, 1), unavail=(1, 2),
        latency=(0.2, 1.2), err5xx=(0.02, 0.10),
        log_count=(40, 120), error_count=(3, 15))

    eid = add_scenario(rows, eid, "POD_INIT_CONTAINER_FAILURE", 90,
        incident_label=True, severity="HIGH", action="ESCALATION",
        cpu=(0.0, 0.1), mem=(0, 100*MB),
        restarts=(1, 5), unavail=(1, 2),
        pod_count=(0, 1), latency=(0.0, 0.1), err5xx=(0.0, 0.0),
        log_count=(10, 50), error_count=(2, 10))

    # ════════════════════════════════════════════════════════════════
    # CATÉGORIE B — APPLICATION-LEVEL (10 types × 90 rows)
    # ════════════════════════════════════════════════════════════════
    eid = add_scenario(rows, eid, "HIGH_LATENCY_P95", 90,
        incident_label=True, severity="MEDIUM", action="SCALE_DEPLOYMENT",
        cpu=(0.25, 0.75), mem=(200*MB, 550*MB),
        restarts=(0, 1), unavail=(0, 1),
        latency=(1.5, 5.0), err5xx=(0.02, 0.15),
        log_count=(30, 100), error_count=(3, 15))

    eid = add_scenario(rows, eid, "HIGH_LATENCY_P99", 90,
        incident_label=True, severity="HIGH", action="INVESTIGATION",
        cpu=(0.3, 0.85), mem=(250*MB, 600*MB),
        restarts=(0, 1), unavail=(0, 1),
        latency=(3.0, 8.0), err5xx=(0.05, 0.25),
        log_count=(40, 130), error_count=(5, 25))

    eid = add_scenario(rows, eid, "ERROR_RATE_5XX_SPIKE", 90,
        incident_label=True, severity="CRITICAL", action="ROLLBACK_DEPLOYMENT",
        cpu=(0.2, 0.8), mem=(200*MB, 600*MB),
        restarts=(0, 2), unavail=(0, 1),
        latency=(0.5, 3.5), err5xx=(0.20, 0.80),
        log_count=(80, 250), error_count=(40, 150))

    eid = add_scenario(rows, eid, "ERROR_RATE_4XX_SPIKE", 90,
        incident_label=True, severity="MEDIUM", action="INVESTIGATION",
        cpu=(0.15, 0.6), mem=(180*MB, 500*MB),
        restarts=(0, 0), unavail=(0, 0),
        latency=(0.2, 1.2), err5xx=(0.02, 0.10),
        log_count=(50, 180), error_count=(20, 80))

    eid = add_scenario(rows, eid, "REQUEST_RATE_DROP", 90,
        incident_label=True, severity="HIGH", action="INVESTIGATION",
        cpu=(0.0, 0.1), mem=(80*MB, 300*MB),
        restarts=(0, 0), unavail=(0, 1),
        latency=(0.0, 0.2), err5xx=(0.0, 0.05),
        log_count=(2, 15), error_count=(0, 2))

    eid = add_scenario(rows, eid, "MEMORY_LEAK", 90,
        incident_label=True, severity="HIGH", action="RESTART_POD",
        cpu=(0.1, 0.45), mem=(1.5*GB, 2.8*GB),
        restarts=(0, 1), unavail=(0, 1),
        latency=(0.2, 1.0), err5xx=(0.01, 0.10),
        log_count=(40, 120), error_count=(2, 10))

    eid = add_scenario(rows, eid, "CPU_SATURATION", 90,
        incident_label=True, severity="HIGH", action="SCALE_DEPLOYMENT",
        cpu=(1.4, 2.5), mem=(180*MB, 380*MB),
        restarts=(0, 1), unavail=(0, 0),
        latency=(0.3, 2.5), err5xx=(0.02, 0.15),
        log_count=(60, 180), error_count=(5, 25))

    eid = add_scenario(rows, eid, "THREAD_POOL_EXHAUSTION", 90,
        incident_label=True, severity="HIGH", action="RESTART_POD",
        cpu=(0.6, 1.2), mem=(300*MB, 700*MB),
        restarts=(0, 1), unavail=(0, 1),
        latency=(2.0, 6.0), err5xx=(0.10, 0.35),
        log_count=(80, 200), error_count=(15, 60),
        restart_hits=(0, 2))

    eid = add_scenario(rows, eid, "DB_CONNECTION_POOL_EXHAUSTION", 90,
        incident_label=True, severity="HIGH", action="RESTART_POD",
        cpu=(0.1, 0.4), mem=(200*MB, 500*MB),
        restarts=(0, 2), unavail=(0, 1),
        latency=(1.5, 5.0), err5xx=(0.15, 0.50),
        log_count=(60, 180), error_count=(20, 70),
        db_hits=(8, 20))

    eid = add_scenario(rows, eid, "CIRCUIT_BREAKER_OPEN", 90,
        incident_label=True, severity="HIGH", action="INVESTIGATION",
        cpu=(0.05, 0.3), mem=(150*MB, 400*MB),
        restarts=(0, 0), unavail=(0, 0),
        latency=(0.1, 0.5), err5xx=(0.30, 0.70),
        log_count=(40, 120), error_count=(15, 50))

    # ════════════════════════════════════════════════════════════════
    # CATÉGORIE C — DATABASE (6 types × 90 rows)
    # ════════════════════════════════════════════════════════════════
    eid = add_scenario(rows, eid, "DB_SLOW_QUERIES", 90,
        incident_label=True, severity="MEDIUM", action="INVESTIGATION",
        cpu=(0.15, 0.5), mem=(200*MB, 500*MB),
        restarts=(0, 0), unavail=(0, 0),
        latency=(1.5, 4.0), err5xx=(0.02, 0.10),
        log_count=(40, 120), error_count=(2, 10),
        db_hits=(5, 15))

    eid = add_scenario(rows, eid, "DB_DEADLOCK", 90,
        incident_label=True, severity="HIGH", action="INVESTIGATION",
        cpu=(0.1, 0.4), mem=(180*MB, 450*MB),
        restarts=(0, 0), unavail=(0, 0),
        latency=(0.5, 2.5), err5xx=(0.05, 0.20),
        log_count=(60, 160), error_count=(10, 40),
        db_hits=(8, 18))

    eid = add_scenario(rows, eid, "DB_REPLICATION_LAG", 90,
        incident_label=True, severity="MEDIUM", action="INVESTIGATION",
        cpu=(0.1, 0.4), mem=(200*MB, 500*MB),
        restarts=(0, 0), unavail=(0, 0),
        latency=(0.5, 2.0), err5xx=(0.02, 0.08),
        log_count=(30, 100), error_count=(3, 12),
        db_hits=(4, 12))

    eid = add_scenario(rows, eid, "DB_DISK_FULL", 90,
        incident_label=True, severity="CRITICAL", action="ESCALATION",
        cpu=(0.05, 0.3), mem=(150*MB, 400*MB),
        restarts=(0, 2), unavail=(0, 1),
        latency=(1.0, 4.0), err5xx=(0.15, 0.50),
        log_count=(50, 150), error_count=(20, 70),
        db_hits=(8, 20))

    eid = add_scenario(rows, eid, "DB_CONNECTION_REFUSED", 90,
        incident_label=True, severity="CRITICAL", action="RESTART_POD",
        cpu=(0.05, 0.25), mem=(150*MB, 400*MB),
        restarts=(0, 3), unavail=(0, 2),
        latency=(2.0, 6.0), err5xx=(0.40, 0.90),
        log_count=(60, 180), error_count=(30, 100),
        db_hits=(10, 25))

    eid = add_scenario(rows, eid, "DB_HIGH_WRITE_LOAD", 90,
        incident_label=True, severity="HIGH", action="SCALE_DEPLOYMENT",
        cpu=(0.3, 0.8), mem=(300*MB, 700*MB),
        restarts=(0, 0), unavail=(0, 0),
        latency=(1.0, 3.5), err5xx=(0.05, 0.20),
        log_count=(80, 200), error_count=(8, 30),
        db_hits=(15, 30))

    # ════════════════════════════════════════════════════════════════
    # CATÉGORIE D — NETWORK (8 types × 90 rows)
    # ════════════════════════════════════════════════════════════════
    eid = add_scenario(rows, eid, "NETWORK_DNS_FAILURE", 90,
        incident_label=True, severity="HIGH", action="INVESTIGATION",
        cpu=(0.1, 0.4), mem=(180*MB, 450*MB),
        restarts=(0, 1), unavail=(0, 1),
        latency=(2.0, 8.0), err5xx=(0.20, 0.70),
        log_count=(70, 200), error_count=(25, 80))

    eid = add_scenario(rows, eid, "NETWORK_PACKET_LOSS", 90,
        incident_label=True, severity="HIGH", action="ESCALATION",
        cpu=(0.1, 0.5), mem=(200*MB, 500*MB),
        restarts=(0, 1), unavail=(0, 1),
        latency=(1.5, 6.0), err5xx=(0.10, 0.40),
        log_count=(50, 150), error_count=(10, 40))

    eid = add_scenario(rows, eid, "NETWORK_LATENCY_BETWEEN_NODES", 90,
        incident_label=True, severity="MEDIUM", action="INVESTIGATION",
        cpu=(0.15, 0.5), mem=(200*MB, 500*MB),
        restarts=(0, 0), unavail=(0, 0),
        latency=(1.0, 3.5), err5xx=(0.03, 0.15),
        log_count=(30, 100), error_count=(3, 15))

    eid = add_scenario(rows, eid, "INGRESS_BACKEND_DOWN", 90,
        incident_label=True, severity="CRITICAL", action="RESTART_POD",
        cpu=(0.0, 0.1), mem=(100*MB, 300*MB),
        restarts=(1, 4), unavail=(1, 3),
        pod_count=(0, 1), latency=(3.0, 10.0), err5xx=(0.60, 0.95),
        log_count=(40, 150), error_count=(20, 80))

    eid = add_scenario(rows, eid, "SERVICE_DISCOVERY_FAILURE", 90,
        incident_label=True, severity="HIGH", action="ESCALATION",
        cpu=(0.05, 0.25), mem=(100*MB, 350*MB),
        restarts=(0, 1), unavail=(1, 2),
        latency=(2.0, 6.0), err5xx=(0.30, 0.70),
        log_count=(40, 130), error_count=(15, 50))

    eid = add_scenario(rows, eid, "TLS_CERTIFICATE_EXPIRY", 90,
        incident_label=True, severity="HIGH", action="ESCALATION",
        cpu=(0.05, 0.2), mem=(150*MB, 350*MB),
        restarts=(0, 0), unavail=(0, 0),
        latency=(0.1, 0.5), err5xx=(0.50, 0.90),
        log_count=(30, 100), error_count=(15, 60))

    eid = add_scenario(rows, eid, "NETWORK_PARTITION", 90,
        incident_label=True, severity="CRITICAL", action="ESCALATION",
        cpu=(0.05, 0.3), mem=(150*MB, 400*MB),
        restarts=(0, 2), unavail=(1, 3),
        node_not_ready=(1, 2), latency=(3.0, 10.0), err5xx=(0.40, 0.90),
        log_count=(50, 150), error_count=(20, 80))

    eid = add_scenario(rows, eid, "EXTERNAL_API_DEPENDENCY_DOWN", 90,
        incident_label=True, severity="HIGH", action="INVESTIGATION",
        cpu=(0.1, 0.4), mem=(180*MB, 450*MB),
        restarts=(0, 0), unavail=(0, 0),
        latency=(2.0, 7.0), err5xx=(0.25, 0.70),
        log_count=(60, 180), error_count=(20, 70))

    # ════════════════════════════════════════════════════════════════
    # CATÉGORIE E — VM/NODE (8 types × 90 rows)
    # ════════════════════════════════════════════════════════════════
    eid = add_scenario(rows, eid, "NODE_NOT_READY", 90,
        incident_label=True, severity="CRITICAL", action="ESCALATION",
        cpu=(0.1, 0.5), mem=(100*MB, 500*MB),
        restarts=(1, 5), unavail=(2, 4),
        node_not_ready=(1, 2), latency=(1.0, 5.0), err5xx=(0.20, 0.80),
        log_count=(50, 150), error_count=(10, 50),
        restart_hits=(1, 5))

    eid = add_scenario(rows, eid, "NODE_HIGH_CPU", 90,
        incident_label=True, severity="HIGH", action="SCALE_DOWN",
        cpu=(1.8, 2.0), mem=(300*MB, 700*MB),
        restarts=(0, 1), unavail=(0, 1),
        latency=(1.0, 3.0), err5xx=(0.05, 0.20),
        log_count=(40, 130), error_count=(5, 20))

    eid = add_scenario(rows, eid, "NODE_HIGH_MEMORY", 90,
        incident_label=True, severity="HIGH", action="SCALE_DOWN",
        cpu=(0.2, 0.6), mem=(1.8*GB, 2.5*GB),
        restarts=(0, 2), unavail=(0, 1),
        latency=(0.5, 2.0), err5xx=(0.02, 0.15),
        log_count=(40, 120), error_count=(3, 15))

    eid = add_scenario(rows, eid, "NODE_DISK_FULL", 90,
        incident_label=True, severity="CRITICAL", action="ESCALATION",
        cpu=(0.1, 0.5), mem=(200*MB, 600*MB),
        restarts=(0, 2), unavail=(0, 1),
        latency=(0.5, 3.0), err5xx=(0.10, 0.40),
        log_count=(50, 180), error_count=(15, 60))

    eid = add_scenario(rows, eid, "NODE_DISK_IO_SATURATION", 90,
        incident_label=True, severity="HIGH", action="INVESTIGATION",
        cpu=(0.2, 0.6), mem=(300*MB, 700*MB),
        restarts=(0, 0), unavail=(0, 0),
        latency=(2.0, 5.0), err5xx=(0.05, 0.20),
        log_count=(40, 130), error_count=(5, 25))

    eid = add_scenario(rows, eid, "NODE_KUBELET_DOWN", 90,
        incident_label=True, severity="CRITICAL", action="ESCALATION",
        cpu=(0.05, 0.3), mem=(150*MB, 400*MB),
        restarts=(0, 3), unavail=(2, 4),
        node_not_ready=(1, 2), latency=(2.0, 8.0), err5xx=(0.30, 0.80),
        log_count=(30, 120), error_count=(10, 50))

    eid = add_scenario(rows, eid, "NODE_CLOCK_SKEW", 90,
        incident_label=True, severity="MEDIUM", action="ESCALATION",
        cpu=(0.1, 0.4), mem=(180*MB, 450*MB),
        restarts=(0, 1), unavail=(0, 0),
        latency=(0.5, 2.0), err5xx=(0.05, 0.25),
        log_count=(30, 100), error_count=(5, 25))

    eid = add_scenario(rows, eid, "NODE_HARDWARE_FAILURE", 90,
        incident_label=True, severity="CRITICAL", action="ESCALATION",
        cpu=(0.0, 0.2), mem=(100*MB, 400*MB),
        restarts=(0, 5), unavail=(2, 4),
        node_not_ready=(1, 2), latency=(2.0, 10.0), err5xx=(0.40, 0.95),
        log_count=(20, 100), error_count=(15, 80),
        restart_hits=(1, 5))

    # ════════════════════════════════════════════════════════════════
    # CATÉGORIE F — SECURITY (4 types × 90 rows)
    # ════════════════════════════════════════════════════════════════
    eid = add_scenario(rows, eid, "AUTH_TOKEN_EXPIRED", 90,
        incident_label=True, severity="MEDIUM", action="ESCALATION",
        cpu=(0.1, 0.4), mem=(150*MB, 400*MB),
        restarts=(0, 0), unavail=(0, 0),
        latency=(0.05, 0.3), err5xx=(0.0, 0.05),
        log_count=(40, 130), error_count=(10, 50))

    eid = add_scenario(rows, eid, "AUTH_BRUTE_FORCE_ATTEMPT", 90,
        incident_label=True, severity="HIGH", action="ESCALATION",
        cpu=(0.2, 0.5), mem=(180*MB, 400*MB),
        restarts=(0, 0), unavail=(0, 0),
        latency=(0.1, 0.5), err5xx=(0.0, 0.05),
        log_count=(150, 400), error_count=(50, 200))

    eid = add_scenario(rows, eid, "UNAUTHORIZED_API_ACCESS_SPIKE", 90,
        incident_label=True, severity="HIGH", action="ESCALATION",
        cpu=(0.15, 0.5), mem=(180*MB, 450*MB),
        restarts=(0, 0), unavail=(0, 0),
        latency=(0.05, 0.3), err5xx=(0.0, 0.05),
        log_count=(80, 250), error_count=(30, 120))

    eid = add_scenario(rows, eid, "SECRET_ROTATION_FAILURE", 90,
        incident_label=True, severity="HIGH", action="ESCALATION",
        cpu=(0.05, 0.3), mem=(150*MB, 400*MB),
        restarts=(0, 2), unavail=(0, 1),
        latency=(0.5, 3.0), err5xx=(0.20, 0.60),
        log_count=(40, 130), error_count=(15, 60))

    # ════════════════════════════════════════════════════════════════
    # CATÉGORIE G — STORAGE (3 types × 90 rows)
    # ════════════════════════════════════════════════════════════════
    eid = add_scenario(rows, eid, "PVC_PENDING", 90,
        incident_label=True, severity="HIGH", action="ESCALATION",
        cpu=(0.0, 0.05), mem=(0, 50*MB),
        restarts=(0, 0), unavail=(1, 3),
        pod_count=(0, 1), latency=(0.0, 0.1), err5xx=(0.0, 0.0),
        log_count=(5, 30), error_count=(0, 2))

    eid = add_scenario(rows, eid, "PV_FILESYSTEM_CORRUPTION", 90,
        incident_label=True, severity="CRITICAL", action="ESCALATION",
        cpu=(0.05, 0.3), mem=(150*MB, 400*MB),
        restarts=(0, 3), unavail=(0, 2),
        latency=(1.0, 5.0), err5xx=(0.20, 0.60),
        log_count=(40, 130), error_count=(15, 60))

    eid = add_scenario(rows, eid, "PVC_USAGE_HIGH", 90,
        incident_label=True, severity="MEDIUM", action="INVESTIGATION",
        cpu=(0.1, 0.4), mem=(200*MB, 500*MB),
        restarts=(0, 0), unavail=(0, 0),
        latency=(0.3, 1.5), err5xx=(0.02, 0.10),
        log_count=(30, 100), error_count=(3, 15))

    # ════════════════════════════════════════════════════════════════
    # CATÉGORIE H — OBSERVABILITY (3 types × 90 rows)
    # ════════════════════════════════════════════════════════════════
    eid = add_scenario(rows, eid, "PROMETHEUS_OUT_OF_MEMORY", 90,
        incident_label=True, severity="HIGH", action="SCALE_DEPLOYMENT",
        cpu=(0.2, 0.6), mem=(2.0*GB, 3.0*GB),
        restarts=(1, 4), unavail=(0, 1),
        mon_up=0.0, latency=(0.3, 2.0), err5xx=(0.10, 0.40),
        log_count=(40, 130), error_count=(10, 50))

    eid = add_scenario(rows, eid, "LOKI_INGESTION_LAG", 90,
        incident_label=True, severity="MEDIUM", action="INVESTIGATION",
        cpu=(0.2, 0.5), mem=(300*MB, 700*MB),
        restarts=(0, 0), unavail=(0, 0),
        mon_up=1.0, latency=(0.5, 2.0), err5xx=(0.05, 0.20),
        log_count=(50, 150), error_count=(5, 25))

    eid = add_scenario(rows, eid, "METRICS_SCRAPE_FAILURE", 90,
        incident_label=True, severity="MEDIUM", action="INVESTIGATION",
        cpu=(0.1, 0.4), mem=(150*MB, 400*MB),
        restarts=(0, 2), unavail=(0, 1),
        mon_up=0.0, latency=(0.1, 1.0), err5xx=(0.05, 0.20),
        log_count=(30, 100), error_count=(5, 20))

    # ════════════════════════════════════════════════════════════════
    # CATCH-ALL — UNKNOWN_ANOMALY (90 rows aléatoires hors patterns connus)
    # ════════════════════════════════════════════════════════════════
    eid = add_scenario(rows, eid, "UNKNOWN_ANOMALY", 90,
        incident_label=True, severity="MEDIUM", action="INVESTIGATION",
        cpu=(0.3, 1.5), mem=(300*MB, 1.5*GB),
        restarts=(0, 3), unavail=(0, 2),
        latency=(0.8, 5.0), err5xx=(0.10, 0.50),
        log_count=(40, 200), error_count=(10, 80))

    # ════════════════════════════════════════════════════════════════
    # Output
    # ════════════════════════════════════════════════════════════════
    frame = pd.DataFrame(rows)
    out = Path("datasets/aiops_training_dataset_v2.csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(out, index=False)

    total = len(frame)
    n_incident = int(frame["incident_label"].sum())
    print(f"Generated {total} rows ({n_incident} incident / {total - n_incident} normal)")
    print()
    print("Scenarios distribution:")
    print(frame["scenario"].value_counts().to_string())
    print()
    print(f"Action distribution:\n{frame['action_label'].value_counts().to_string()}")
    print(f"Severity distribution:\n{frame['severity_label'].value_counts().to_string()}")
    print(f"\nSaved -> {out}")


if __name__ == "__main__":
    main()
