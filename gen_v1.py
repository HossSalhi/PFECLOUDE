"""Generate enriched LoRA dataset: 1055 synth + 69 real captures as Alpaca prompts."""
import json
import csv
import random
from pathlib import Path

SYNTH = Path("c:/Users/houss/IdeaProjects/PFE-Orange/datasets/lora_instructions_v2.jsonl")
REAL = Path("c:/Users/houss/IdeaProjects/PFE-Orange/datasets/real_chaos_capture.csv")
OUT = Path("c:/Users/houss/IdeaProjects/PFE-Orange/datasets/lora_v3_real.jsonl")

ACTION_MAP = {
    "POD_CRASH_LOOP": "RESTART_POD", "POD_OOM_KILLED": "SCALE_UP_MEMORY",
    "POD_IMAGE_PULL_BACKOFF": "ESCALATION", "POD_PENDING_INSUFFICIENT_RESOURCES": "SCALE_DOWN",
    "POD_STUCK_TERMINATING": "FORCE_RECREATE_DEPLOYMENT", "POD_LIVENESS_FAILURE": "RESTART_POD",
    "POD_READINESS_FAILURE": "ESCALATION", "POD_INIT_CONTAINER_FAILURE": "ESCALATION",
    "HIGH_LATENCY_P95": "SCALE_DEPLOYMENT", "HIGH_LATENCY_P99": "SCALE_DEPLOYMENT",
    "ERROR_RATE_5XX_SPIKE": "ROLLBACK_DEPLOYMENT", "ERROR_RATE_4XX_SPIKE": "ESCALATION",
    "REQUEST_RATE_DROP": "ESCALATION", "MEMORY_LEAK": "RESTART_POD",
    "CPU_SATURATION": "SCALE_DEPLOYMENT", "THREAD_POOL_EXHAUSTION": "RESTART_POD",
    "DB_CONNECTION_POOL_EXHAUSTION": "SCALE_DEPLOYMENT", "CIRCUIT_BREAKER_OPEN": "ESCALATION",
    "DB_SLOW_QUERIES": "ESCALATION", "DB_DEADLOCK": "ESCALATION", "DB_REPLICATION_LAG": "ESCALATION",
    "DB_DISK_FULL": "ESCALATION", "DB_CONNECTION_REFUSED": "RESTART_POD",
    "DB_HIGH_WRITE_LOAD": "SCALE_DEPLOYMENT", "NETWORK_DNS_FAILURE": "RESTART_POD",
    "NETWORK_PACKET_LOSS": "ESCALATION", "NETWORK_LATENCY_BETWEEN_NODES": "ESCALATION",
    "INGRESS_BACKEND_DOWN": "RESTART_POD", "SERVICE_DISCOVERY_FAILURE": "RESTART_POD",
    "TLS_CERTIFICATE_EXPIRY": "ESCALATION", "NETWORK_PARTITION": "ESCALATION",
    "EXTERNAL_API_DEPENDENCY_DOWN": "ESCALATION", "NODE_NOT_READY": "ESCALATION",
    "NODE_HIGH_CPU": "SCALE_DOWN", "NODE_HIGH_MEMORY": "SCALE_DOWN", "NODE_DISK_FULL": "ESCALATION",
    "NODE_DISK_IO_SATURATION": "ESCALATION", "NODE_KUBELET_DOWN": "ESCALATION",
    "NODE_CLOCK_SKEW": "ESCALATION", "NODE_HARDWARE_FAILURE": "ESCALATION",
    "AUTH_TOKEN_EXPIRED": "ESCALATION", "AUTH_BRUTE_FORCE_ATTEMPT": "ESCALATION",
    "UNAUTHORIZED_API_ACCESS_SPIKE": "ESCALATION", "SECRET_ROTATION_FAILURE": "ESCALATION",
    "PVC_PENDING": "ESCALATION", "PV_FILESYSTEM_CORRUPTION": "ESCALATION", "PVC_USAGE_HIGH": "ESCALATION",
    "PROMETHEUS_OUT_OF_MEMORY": "SCALE_UP_MEMORY", "LOKI_INGESTION_LAG": "RESTART_POD",
    "METRICS_SCRAPE_FAILURE": "ESCALATION", "UNKNOWN_ANOMALY": "ESCALATION",
    "NO_INCIDENT": "NO_ACTION",
}

records = []
with SYNTH.open("r", encoding="utf-8") as f:
    for line in f:
        records.append(json.loads(line))
print(f"Loaded synth: {len(records)}")

real_added = 0
with REAL.open("r", encoding="utf-8") as f:
    reader = csv.DictReader(f)
    for row in reader:
        scen = str(row.get('scenario', '')).upper()
        if scen not in ACTION_MAP:
            continue
        try:
            cpu = float(row.get('cpu_usage_cores', 0))
            mem_gb = float(row.get('memory_working_set_bytes', 0)) / 1e9
            restarts = float(row.get('pod_restarts_total', 0))
            http5xx = float(row.get('http_5xx_ratio', 0))
            lat = float(row.get('http_latency_p95_seconds', 0))
            unavail = float(row.get('unavailable_replicas', 0))
            req_rate = float(row.get('http_request_rate', 0))
        except (ValueError, TypeError):
            continue
        instruction = (
            f"Real Prometheus metrics: cpu={cpu:.3f}cores, memory={mem_gb:.2f}GB, "
            f"pod_restarts_5min={restarts:.0f}, http_5xx_ratio={http5xx:.3f}, "
            f"latency_p95={lat:.3f}s, unavailable_replicas={unavail:.0f}, "
            f"req_rate={req_rate:.1f}/s. Identify the incident and action."
        )
        sev = "LOW" if scen == "NO_INCIDENT" else ("HIGH" if scen.startswith(("POD_", "DB_", "NODE_", "AUTH_", "SECRET_")) else "MEDIUM")
        output = json.dumps({
            "incident": scen != "NO_INCIDENT",
            "incident_type": scen,
            "severity": sev,
            "recommended_action": ACTION_MAP[scen],
            "explanation": f"Real metrics matched {scen} signature."
        })
        records.append({"instruction": instruction, "input": "", "output": output})
        real_added += 1

print(f"Real prompts added: {real_added}")
print(f"Total: {len(records)}")

random.seed(42)
random.shuffle(records)

with OUT.open("w", encoding="utf-8") as f:
    for r in records:
        f.write(json.dumps(r) + "\n")
print(f"Saved: {OUT}")
