"""
Generate a synthetic AIOps training dataset — 1200+ rows across 28 incident types.
Outputs datasets/aiops_training_dataset.csv ready for train_baseline.py.
Run from the ai-service/ directory:  python -m scripts.generate_synthetic_dataset
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


def _log_risk(error_ratio: float, restart_hits: float, log_count: float) -> float:
    return min(
        0.55 * error_ratio
        + 0.25 * min(restart_hits / 5.0, 1.0)
        + 0.20 * min(log_count / 200.0, 1.0),
        1.0,
    )


def _metric_risk(
    cpu: float, mem: float, restarts: float, unavail: float,
    node_not_ready: float, latency: float, err5xx: float,
    mon_up: float, ai_up: float,
) -> float:
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


def _row(
    event_id: int,
    scenario: str,
    incident_label: bool,
    severity_label: str,
    action_label: str,
    cpu: float,
    mem: float,
    restarts: float,
    unavail: float,
    pod_count: float,
    node_not_ready: float,
    mon_up: float,
    ai_up: float,
    latency: float,
    err5xx: float,
    log_count: float,
    error_count: float,
    warning_count: float,
    restart_hits: float,
    db_hits: float,
    # New features — all default to 0.0
    feature_disk_io_bytes: float = 0.0,
    feature_network_rx_bytes: float = 0.0,
    feature_network_tx_bytes: float = 0.0,
    feature_network_errors: float = 0.0,
    feature_open_file_descriptors: float = 0.0,
    feature_jvm_gc_pause_seconds: float = 0.0,
    feature_jvm_threads_live: float = 0.0,
    feature_node_memory_pressure: float = 0.0,
    feature_node_disk_pressure: float = 0.0,
    feature_pvc_used_bytes: float = 0.0,
    feature_http_request_rate: float = 0.0,
    feature_node_not_ready_total: float = 0.0,
) -> dict:
    error_ratio = error_count / max(log_count, 1)
    unique_tpl = max(1, int(log_count * random.uniform(0.3, 0.7)))
    lr = _log_risk(error_ratio, restart_hits, log_count)
    mr = _metric_risk(cpu, mem, restarts, unavail, node_not_ready, latency, err5xx, mon_up, ai_up)

    base: dict = {
        "event_id": event_id,
        "scenario": scenario,
        "namespace": "pfe",
        "app": "backend",
        "window_minutes": 5,
        "end_time": None,
        "incident_label": incident_label,
        "severity_label": severity_label,
        "action_label": action_label,
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
        # New feature columns
        "feature_disk_io_bytes": round(feature_disk_io_bytes, 1),
        "feature_network_rx_bytes": round(feature_network_rx_bytes, 1),
        "feature_network_tx_bytes": round(feature_network_tx_bytes, 1),
        "feature_network_errors": round(feature_network_errors, 3),
        "feature_open_file_descriptors": round(feature_open_file_descriptors, 1),
        "feature_jvm_gc_pause_seconds": round(feature_jvm_gc_pause_seconds, 5),
        "feature_jvm_threads_live": round(feature_jvm_threads_live, 1),
        "feature_node_memory_pressure": round(feature_node_memory_pressure, 1),
        "feature_node_disk_pressure": round(feature_node_disk_pressure, 1),
        "feature_pvc_used_bytes": round(feature_pvc_used_bytes, 1),
        "feature_http_request_rate": round(feature_http_request_rate, 3),
        "feature_node_not_ready_total": round(feature_node_not_ready_total, 1),
    }
    base.update(_embeddings(incident_label))
    return base


def R(a: float, b: float) -> float:
    return random.uniform(a, b)


def RI(a: int, b: int) -> int:
    return random.randint(a, b)


def main() -> None:
    rows: list[dict] = []
    eid = 1

    # ── 1. NORMAL — 115 rows ─────────────────────────────────────────────────
    for _ in range(115):
        rows.append(_row(
            eid, "NORMAL", False, "LOW", "NO_ACTION",
            cpu=R(0.005, 0.14), mem=R(50 * MB, 240 * MB),
            restarts=0, unavail=0, pod_count=RI(1, 3), node_not_ready=0,
            mon_up=1.0, ai_up=1.0, latency=R(0.01, 0.09), err5xx=0.0,
            log_count=RI(5, 45), error_count=RI(0, 1), warning_count=RI(0, 3),
            restart_hits=0, db_hits=0,
            feature_network_rx_bytes=R(1 * MB, 5 * MB),
            feature_network_tx_bytes=R(1 * MB, 5 * MB),
            feature_http_request_rate=R(1, 30),
        ))
        eid += 1

    # ── 2. CPU STRESS — 60 rows ──────────────────────────────────────────────
    for _ in range(60):
        rows.append(_row(
            eid, "CPU_STRESS", True, "HIGH", "RESTART_OR_SCALE",
            cpu=R(0.65, 1.9), mem=R(180 * MB, 380 * MB),
            restarts=RI(0, 1), unavail=0, pod_count=RI(1, 2), node_not_ready=0,
            mon_up=1.0, ai_up=1.0, latency=R(0.3, 2.5), err5xx=R(0.02, 0.12),
            log_count=RI(60, 180), error_count=RI(5, 25), warning_count=RI(3, 15),
            restart_hits=RI(0, 2), db_hits=0,
            feature_network_rx_bytes=R(2 * MB, 10 * MB),
            feature_network_tx_bytes=R(2 * MB, 10 * MB),
            feature_http_request_rate=R(20, 100),
        ))
        eid += 1

    # ── 3. MEMORY STRESS — 60 rows ───────────────────────────────────────────
    for _ in range(60):
        rows.append(_row(
            eid, "MEMORY_STRESS", True, "HIGH", "RESTART_OR_SCALE",
            cpu=R(0.1, 0.5), mem=R(830 * MB, 2100 * MB),
            restarts=RI(0, 2), unavail=RI(0, 1), pod_count=RI(1, 2), node_not_ready=0,
            mon_up=1.0, ai_up=1.0, latency=R(0.15, 1.2), err5xx=R(0.01, 0.08),
            log_count=RI(40, 150), error_count=RI(3, 20), warning_count=RI(2, 10),
            restart_hits=RI(0, 2), db_hits=0,
            feature_jvm_gc_pause_seconds=R(0.1, 0.5),
            feature_jvm_threads_live=R(50, 200),
        ))
        eid += 1

    # ── 4. RESTART LOOP / CRASHLOOP — 55 rows ────────────────────────────────
    for _ in range(55):
        rows.append(_row(
            eid, "RESTART_LOOP", True, "HIGH", "RESTART_OR_SCALE",
            cpu=R(0.1, 0.6), mem=R(100 * MB, 500 * MB),
            restarts=RI(3, 15), unavail=RI(1, 2), pod_count=RI(1, 2), node_not_ready=0,
            mon_up=1.0, ai_up=1.0, latency=R(0.2, 1.5), err5xx=R(0.05, 0.25),
            log_count=RI(80, 220), error_count=RI(15, 60), warning_count=RI(5, 20),
            restart_hits=RI(3, 8), db_hits=0,
            feature_http_request_rate=R(5, 50),
        ))
        eid += 1

    # ── 5. AVAILABILITY DROP — 55 rows ───────────────────────────────────────
    for _ in range(55):
        rows.append(_row(
            eid, "AVAILABILITY_DROP", True, "HIGH", "SCALE_DEPLOYMENT_AND_REBALANCE",
            cpu=R(0.3, 1.2), mem=R(200 * MB, 800 * MB),
            restarts=RI(0, 1), unavail=RI(1, 3), pod_count=RI(0, 1), node_not_ready=0,
            mon_up=1.0, ai_up=1.0, latency=R(0.2, 1.5), err5xx=R(0.03, 0.2),
            log_count=RI(30, 120), error_count=RI(5, 30), warning_count=RI(2, 12),
            restart_hits=RI(0, 2), db_hits=0,
            feature_http_request_rate=R(10, 80),
        ))
        eid += 1

    # ── 6. APPLICATION ERROR STORM — 55 rows ─────────────────────────────────
    for _ in range(55):
        rows.append(_row(
            eid, "APP_ERROR_STORM", True,
            random.choice(["HIGH", "MEDIUM"]), "RESTART_OR_SCALE",
            cpu=R(0.2, 0.8), mem=R(200 * MB, 600 * MB),
            restarts=RI(0, 2), unavail=RI(0, 1), pod_count=RI(1, 2), node_not_ready=0,
            mon_up=1.0, ai_up=1.0, latency=R(0.5, 3.5), err5xx=R(0.12, 0.55),
            log_count=RI(80, 250), error_count=RI(30, 100), warning_count=RI(5, 25),
            restart_hits=RI(0, 3), db_hits=0,
            feature_http_request_rate=R(30, 150),
        ))
        eid += 1

    # ── 7. DATABASE CONNECTIVITY — 55 rows ───────────────────────────────────
    for _ in range(55):
        rows.append(_row(
            eid, "DB_CONNECTIVITY", True, "HIGH", "CHECK_DB_CONNECTIVITY_AND_RESTART",
            cpu=R(0.05, 0.35), mem=R(150 * MB, 400 * MB),
            restarts=RI(0, 3), unavail=RI(0, 1), pod_count=RI(1, 2), node_not_ready=0,
            mon_up=1.0, ai_up=1.0, latency=R(0.3, 2.0), err5xx=R(0.05, 0.3),
            log_count=RI(40, 130), error_count=RI(10, 40), warning_count=RI(3, 15),
            restart_hits=RI(0, 2), db_hits=RI(3, 12),
            feature_http_request_rate=R(5, 40),
        ))
        eid += 1

    # ── 8. GENERAL DEGRADATION — 50 rows ─────────────────────────────────────
    for _ in range(50):
        rows.append(_row(
            eid, "GENERAL_DEGRADATION", True, "MEDIUM", "INVESTIGATE",
            cpu=R(0.2, 0.6), mem=R(200 * MB, 600 * MB),
            restarts=RI(0, 1), unavail=0, pod_count=RI(1, 2), node_not_ready=0,
            mon_up=1.0, ai_up=1.0, latency=R(0.2, 1.5), err5xx=R(0.01, 0.08),
            log_count=RI(30, 100), error_count=RI(5, 20), warning_count=RI(5, 15),
            restart_hits=0, db_hits=0,
            feature_http_request_rate=R(5, 50),
        ))
        eid += 1

    # ── 9. VM DOWN / NODE NOT READY — 45 rows ───────────────────────────────
    for _ in range(45):
        rows.append(_row(
            eid, "VM_DOWN", True, "CRITICAL", "ESCALATE_VM_OUTAGE_AND_FAILOVER",
            cpu=R(0.0, 0.2), mem=R(50 * MB, 300 * MB),
            restarts=RI(1, 5), unavail=RI(2, 4), pod_count=RI(0, 1), node_not_ready=RI(1, 2),
            mon_up=random.choice([0.0, 1.0]), ai_up=random.choice([0.0, 1.0]),
            latency=R(2.0, 8.0), err5xx=R(0.3, 0.9),
            log_count=RI(20, 80), error_count=RI(10, 50), warning_count=RI(5, 20),
            restart_hits=RI(2, 6), db_hits=RI(0, 3),
            feature_node_not_ready_total=float(RI(1, 2)),
            feature_network_errors=R(5, 20),
        ))
        eid += 1

    # ── 10. MEMORY LEAK — 45 rows ────────────────────────────────────────────
    for _ in range(45):
        rows.append(_row(
            eid, "MEMORY_LEAK", True, random.choice(["HIGH", "MEDIUM"]), "RESTART_OR_SCALE",
            cpu=R(0.3, 0.7), mem=R(1100 * MB, 2500 * MB),
            restarts=RI(0, 2), unavail=RI(0, 1), pod_count=RI(1, 2), node_not_ready=0,
            mon_up=1.0, ai_up=1.0, latency=R(0.3, 1.8), err5xx=R(0.01, 0.08),
            log_count=RI(40, 140), error_count=RI(5, 25), warning_count=RI(3, 12),
            restart_hits=RI(0, 2), db_hits=0,
            feature_jvm_gc_pause_seconds=R(0.2, 1.0),
            feature_jvm_threads_live=R(100, 350),
        ))
        eid += 1

    # ── 11. THREAD POOL EXHAUSTION — 45 rows ─────────────────────────────────
    for _ in range(45):
        rows.append(_row(
            eid, "THREAD_POOL_EXHAUSTION", True, "HIGH", "RESTART_OR_SCALE",
            cpu=R(0.55, 1.4), mem=R(700 * MB, 1600 * MB),
            restarts=RI(0, 1), unavail=RI(0, 1), pod_count=RI(1, 2), node_not_ready=0,
            mon_up=1.0, ai_up=1.0, latency=R(1.5, 6.0), err5xx=R(0.10, 0.40),
            log_count=RI(60, 200), error_count=RI(20, 70), warning_count=RI(5, 20),
            restart_hits=RI(0, 2), db_hits=RI(2, 6),
            feature_jvm_threads_live=R(400, 800),
            feature_http_request_rate=R(50, 200),
        ))
        eid += 1

    # ── 12. HEALTH CHECK FAILURE — 45 rows ───────────────────────────────────
    for _ in range(45):
        rows.append(_row(
            eid, "HEALTH_CHECK_FAILURE", True, "HIGH", "RESTART_OR_SCALE",
            cpu=R(0.1, 0.5), mem=R(100 * MB, 600 * MB),
            restarts=RI(2, 8), unavail=RI(1, 2), pod_count=RI(1, 2), node_not_ready=0,
            mon_up=1.0, ai_up=1.0, latency=R(0.1, 0.8), err5xx=R(0.05, 0.20),
            log_count=RI(30, 120), error_count=RI(10, 40), warning_count=RI(3, 15),
            restart_hits=RI(2, 5), db_hits=0,
            feature_http_request_rate=R(5, 40),
        ))
        eid += 1

    # ── 13. STORAGE PRESSURE — 40 rows ───────────────────────────────────────
    for _ in range(40):
        rows.append(_row(
            eid, "STORAGE_PRESSURE", True, "MEDIUM", "INVESTIGATE",
            cpu=R(0.1, 0.4), mem=R(200 * MB, 600 * MB),
            restarts=RI(0, 1), unavail=0, pod_count=RI(1, 2), node_not_ready=0,
            mon_up=1.0, ai_up=1.0, latency=R(0.5, 3.0), err5xx=R(0.02, 0.10),
            log_count=RI(40, 130), error_count=RI(8, 30), warning_count=RI(4, 18),
            restart_hits=0, db_hits=RI(4, 10),
            feature_disk_io_bytes=R(20 * MB, 100 * MB),
            feature_pvc_used_bytes=R(5 * GB, 18 * GB),
        ))
        eid += 1

    # ── 14. CIRCUIT BREAKER OPEN — 40 rows ───────────────────────────────────
    for _ in range(40):
        rows.append(_row(
            eid, "CIRCUIT_BREAKER_OPEN", True, "HIGH", "INVESTIGATE",
            cpu=R(0.15, 0.45), mem=R(250 * MB, 550 * MB),
            restarts=0, unavail=RI(0, 1), pod_count=RI(1, 2), node_not_ready=0,
            mon_up=1.0, ai_up=1.0, latency=R(0.05, 0.3), err5xx=R(0.25, 0.65),
            log_count=RI(50, 160), error_count=RI(15, 55), warning_count=RI(5, 20),
            restart_hits=0, db_hits=RI(2, 6),
            feature_http_request_rate=R(10, 80),
        ))
        eid += 1

    # ── 15. HIGH LATENCY — 40 rows ───────────────────────────────────────────
    for _ in range(40):
        rows.append(_row(
            eid, "HIGH_LATENCY", True, "MEDIUM", "RESTART_OR_SCALE",
            cpu=R(0.25, 0.75), mem=R(200 * MB, 550 * MB),
            restarts=RI(0, 1), unavail=RI(0, 1), pod_count=RI(1, 2), node_not_ready=0,
            mon_up=1.0, ai_up=1.0, latency=R(1.5, 8.0), err5xx=R(0.05, 0.3),
            log_count=RI(30, 100), error_count=RI(5, 20), warning_count=RI(3, 15),
            restart_hits=RI(0, 2), db_hits=0,
            feature_http_request_rate=R(10, 60),
        ))
        eid += 1

    # ── 16. MEDIUM DEGRADATION — 35 rows ─────────────────────────────────────
    for _ in range(35):
        rows.append(_row(
            eid, "MEDIUM_DEGRADATION", True, "MEDIUM",
            random.choice(["INVESTIGATE", "RESTART_OR_SCALE"]),
            cpu=R(0.15, 0.55), mem=R(180 * MB, 520 * MB),
            restarts=RI(0, 1), unavail=0, pod_count=RI(1, 2), node_not_ready=0,
            mon_up=1.0, ai_up=1.0, latency=R(0.3, 1.8), err5xx=R(0.02, 0.12),
            log_count=RI(20, 90), error_count=RI(3, 18), warning_count=RI(4, 14),
            restart_hits=0, db_hits=0,
            feature_http_request_rate=R(5, 40),
        ))
        eid += 1

    # ── 17. MONITORING DOWN — 25 rows ────────────────────────────────────────
    for _ in range(25):
        rows.append(_row(
            eid, "MONITORING_DOWN", True, "HIGH", "ESCALATE_VM_OUTAGE_AND_FAILOVER",
            cpu=R(0.05, 0.3), mem=R(100 * MB, 400 * MB),
            restarts=RI(0, 2), unavail=RI(0, 2), pod_count=RI(1, 2), node_not_ready=0,
            mon_up=0.0, ai_up=1.0, latency=R(0.1, 1.0), err5xx=R(0.0, 0.1),
            log_count=RI(10, 60), error_count=RI(2, 15), warning_count=RI(2, 8),
            restart_hits=0, db_hits=0,
        ))
        eid += 1

    # ── 18. NETWORK PARTITION — 40 rows ──────────────────────────────────────
    for _ in range(40):
        rows.append(_row(
            eid, "NETWORK_PARTITION", True, "CRITICAL", "ESCALATE_VM_OUTAGE_AND_FAILOVER",
            cpu=R(0.05, 0.25), mem=R(100 * MB, 400 * MB),
            restarts=RI(0, 2), unavail=RI(1, 3), pod_count=RI(1, 2), node_not_ready=RI(1, 2),
            mon_up=1.0, ai_up=1.0, latency=R(4.0, 9.0), err5xx=R(0.45, 0.85),
            log_count=RI(30, 100), error_count=RI(15, 50), warning_count=RI(5, 20),
            restart_hits=RI(0, 2), db_hits=RI(0, 3),
            feature_network_errors=R(5, 25),
            feature_network_rx_bytes=R(10 * MB, 50 * MB),
            feature_network_tx_bytes=R(10 * MB, 50 * MB),
            feature_disk_io_bytes=0.0,
            feature_node_not_ready_total=float(RI(1, 2)),
        ))
        eid += 1

    # ── 19. DNS FAILURE — 35 rows ────────────────────────────────────────────
    for _ in range(35):
        rows.append(_row(
            eid, "DNS_FAILURE", True, "HIGH", "INVESTIGATE",
            cpu=R(0.05, 0.30), mem=R(100 * MB, 350 * MB),
            restarts=RI(0, 3), unavail=RI(0, 1), pod_count=RI(1, 2), node_not_ready=0,
            mon_up=1.0, ai_up=1.0, latency=R(3.0, 7.0), err5xx=R(0.30, 0.60),
            log_count=RI(30, 100), error_count=RI(10, 40), warning_count=RI(5, 20),
            restart_hits=RI(0, 2), db_hits=RI(0, 3),
            feature_network_errors=R(2, 10),
            feature_network_rx_bytes=R(1 * MB, 10 * MB),
            feature_network_tx_bytes=R(1 * MB, 5 * MB),
        ))
        eid += 1

    # ── 20. CERTIFICATE EXPIRY — 25 rows ─────────────────────────────────────
    for _ in range(25):
        rows.append(_row(
            eid, "CERTIFICATE_EXPIRY", True, "HIGH", "INVESTIGATE",
            cpu=R(0.05, 0.25), mem=R(100 * MB, 400 * MB),
            restarts=RI(0, 1), unavail=RI(0, 1), pod_count=RI(1, 2), node_not_ready=0,
            mon_up=1.0, ai_up=1.0, latency=R(0.5, 2.5), err5xx=R(0.25, 0.55),
            log_count=RI(20, 80), error_count=RI(5, 25), warning_count=RI(3, 15),
            restart_hits=RI(0, 1), db_hits=0,
            feature_network_errors=R(0, 3),
            feature_http_request_rate=R(5, 50),
        ))
        eid += 1

    # ── 21. IMAGE PULL FAILURE — 25 rows ─────────────────────────────────────
    for _ in range(25):
        rows.append(_row(
            eid, "IMAGE_PULL_FAILURE", True, "HIGH", "INVESTIGATE",
            cpu=R(0.0, 0.05), mem=R(50 * MB, 200 * MB),
            restarts=RI(3, 10), unavail=RI(2, 4), pod_count=RI(0, 1), node_not_ready=0,
            mon_up=1.0, ai_up=1.0, latency=R(0.5, 2.0), err5xx=R(0.3, 0.7),
            log_count=RI(20, 80), error_count=RI(10, 40), warning_count=RI(5, 20),
            restart_hits=RI(2, 6), db_hits=0,
        ))
        eid += 1

    # ── 22. NODE PRESSURE — 35 rows ──────────────────────────────────────────
    for _ in range(35):
        has_mem_pressure = random.random() > 0.4
        has_disk_pressure = random.random() > 0.5
        rows.append(_row(
            eid, "NODE_PRESSURE", True, "CRITICAL", "ESCALATE_VM_OUTAGE_AND_FAILOVER",
            cpu=R(0.1, 0.4), mem=R(200 * MB, 700 * MB),
            restarts=RI(0, 2), unavail=RI(1, 3), pod_count=RI(1, 2), node_not_ready=RI(0, 1),
            mon_up=1.0, ai_up=1.0, latency=R(1.0, 4.0), err5xx=R(0.1, 0.4),
            log_count=RI(30, 100), error_count=RI(10, 40), warning_count=RI(5, 20),
            restart_hits=RI(0, 2), db_hits=RI(0, 3),
            feature_node_memory_pressure=float(RI(1, 2)) if has_mem_pressure else 0.0,
            feature_node_disk_pressure=float(RI(1, 2)) if has_disk_pressure else 0.0,
            feature_pvc_used_bytes=R(5 * GB, 20 * GB),
            feature_node_not_ready_total=float(RI(1, 2)),
        ))
        eid += 1

    # ── 23. RESOURCE QUOTA EXCEEDED — 25 rows ────────────────────────────────
    for _ in range(25):
        rows.append(_row(
            eid, "RESOURCE_QUOTA_EXCEEDED", True, "HIGH", "INVESTIGATE",
            cpu=R(0.1, 0.3), mem=R(200 * MB, 600 * MB),
            restarts=RI(0, 2), unavail=RI(2, 4), pod_count=RI(0, 1), node_not_ready=0,
            mon_up=1.0, ai_up=1.0, latency=R(0.5, 2.0), err5xx=R(0.1, 0.35),
            log_count=RI(30, 100), error_count=RI(10, 40), warning_count=RI(5, 20),
            restart_hits=RI(0, 2), db_hits=0,
            feature_http_request_rate=R(100, 500),
        ))
        eid += 1

    # ── 24. JVM GC PRESSURE — 40 rows ────────────────────────────────────────
    for _ in range(40):
        rows.append(_row(
            eid, "JVM_GC_PRESSURE", True, "HIGH", "RESTART_OR_SCALE",
            cpu=R(0.3, 0.7), mem=R(900 * MB, 2200 * MB),
            restarts=RI(0, 1), unavail=RI(0, 1), pod_count=RI(1, 2), node_not_ready=0,
            mon_up=1.0, ai_up=1.0, latency=R(1.5, 5.0), err5xx=R(0.05, 0.20),
            log_count=RI(50, 160), error_count=RI(10, 40), warning_count=RI(5, 20),
            restart_hits=RI(0, 1), db_hits=0,
            feature_jvm_gc_pause_seconds=R(0.5, 2.5),
            feature_jvm_threads_live=R(200, 500),
        ))
        eid += 1

    # ── 25. FILE DESCRIPTOR EXHAUSTION — 25 rows ─────────────────────────────
    for _ in range(25):
        rows.append(_row(
            eid, "FILE_DESCRIPTOR_EXHAUSTION", True, "HIGH", "RESTART_OR_SCALE",
            cpu=R(0.2, 0.6), mem=R(300 * MB, 800 * MB),
            restarts=RI(0, 2), unavail=RI(0, 1), pod_count=RI(1, 2), node_not_ready=0,
            mon_up=1.0, ai_up=1.0, latency=R(1.0, 4.0), err5xx=R(0.1, 0.35),
            log_count=RI(40, 140), error_count=RI(10, 40), warning_count=RI(5, 20),
            restart_hits=RI(0, 2), db_hits=0,
            feature_open_file_descriptors=float(RI(900, 1024)),
            feature_network_errors=R(1, 5),
        ))
        eid += 1

    # ── 26. NETWORK SATURATION — 30 rows ─────────────────────────────────────
    for _ in range(30):
        rows.append(_row(
            eid, "NETWORK_SATURATION", True, "HIGH", "SCALE_DEPLOYMENT_AND_REBALANCE",
            cpu=R(0.3, 0.7), mem=R(200 * MB, 600 * MB),
            restarts=RI(0, 1), unavail=RI(0, 1), pod_count=RI(1, 2), node_not_ready=0,
            mon_up=1.0, ai_up=1.0, latency=R(1.5, 6.0), err5xx=R(0.05, 0.25),
            log_count=RI(40, 140), error_count=RI(5, 30), warning_count=RI(3, 15),
            restart_hits=RI(0, 1), db_hits=0,
            feature_network_rx_bytes=R(50 * MB, 200 * MB),
            feature_network_tx_bytes=R(50 * MB, 200 * MB),
            feature_http_request_rate=R(200, 1000),
        ))
        eid += 1

    # ── 27. DISK IO SATURATION — 30 rows ─────────────────────────────────────
    for _ in range(30):
        rows.append(_row(
            eid, "DISK_IO_SATURATION", True, "HIGH", "INVESTIGATE",
            cpu=R(0.2, 0.5), mem=R(200 * MB, 600 * MB),
            restarts=RI(0, 1), unavail=RI(0, 1), pod_count=RI(1, 2), node_not_ready=0,
            mon_up=1.0, ai_up=1.0, latency=R(1.5, 5.0), err5xx=R(0.05, 0.20),
            log_count=RI(40, 140), error_count=RI(5, 25), warning_count=RI(3, 15),
            restart_hits=RI(0, 1), db_hits=RI(5, 15),
            feature_disk_io_bytes=R(80 * MB, 300 * MB),
            feature_pvc_used_bytes=R(8 * GB, 25 * GB),
        ))
        eid += 1

    # ── 28. VM WATCHDOG RESTART — 25 rows ────────────────────────────────────
    for _ in range(25):
        rows.append(_row(
            eid, "VM_WATCHDOG_RESTART", True, "CRITICAL", "ESCALATE_VM_OUTAGE_AND_FAILOVER",
            cpu=R(0.0, 0.1), mem=R(0 * MB, 100 * MB),
            restarts=RI(2, 5), unavail=RI(2, 4), pod_count=RI(0, 1), node_not_ready=RI(1, 2),
            mon_up=random.choice([0.0, 1.0]), ai_up=random.choice([0.0, 1.0]),
            latency=R(6.0, 12.0), err5xx=R(0.5, 0.90),
            log_count=RI(10, 60), error_count=RI(10, 40), warning_count=RI(3, 15),
            restart_hits=RI(2, 5), db_hits=RI(0, 3),
            feature_node_not_ready_total=float(RI(1, 3)),
            feature_network_errors=R(10, 50),
        ))
        eid += 1

    frame = pd.DataFrame(rows)
    out = Path("datasets/aiops_training_dataset.csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(out, index=False)

    total = len(frame)
    n_incident = int(frame["incident_label"].sum())
    print(f"Generated {total} rows ({n_incident} incident / {total - n_incident} normal)")
    print(f"\nScenario distribution:\n{frame['scenario'].value_counts().to_string()}")
    print(f"\nAction distribution:\n{frame['action_label'].value_counts().to_string()}")
    print(f"\nSeverity distribution:\n{frame['severity_label'].value_counts().to_string()}")
    print(f"\nSaved -> {out}")


if __name__ == "__main__":
    main()
