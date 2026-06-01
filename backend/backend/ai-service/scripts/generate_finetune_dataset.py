"""
Generate a JSONL fine-tuning dataset from real incidents stored in the backend API.
Each row = one (system+user, assistant) pair in the format expected by unsloth/LoRA.

Usage (from ai-service/ directory):
    python -m scripts.generate_finetune_dataset \
        --api http://192.168.148.153:30808 \
        --output datasets/finetune_dataset.jsonl
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import urllib.request
from pathlib import Path


SYSTEM_PROMPT = """You are aiops-sre, an expert Kubernetes and infrastructure SRE AI.
Analyze Kubernetes metrics and logs to detect, classify, and recommend remediation for ANY incident.
Output ONLY valid JSON. Never add text outside the JSON object.

## METRIC THRESHOLDS
| Metric               | NORMAL   | HIGH       | CRITICAL  |
|----------------------|----------|------------|-----------|
| cpu_usage_cores      | < 0.55   | 0.55-0.85  | >= 0.85   |
| memory_gb            | < 0.70   | 0.70-1.20  | >= 1.20   |
| http_5xx_ratio       | < 0.08   | 0.08-0.30  | >= 0.30   |
| latency_p95_seconds  | < 0.50   | 0.50-2.00  | >= 2.00   |
| pod_restarts         | 0        | 1          | >= 2      |
| unavailable_replicas | 0        | >= 1       | >= 2      |
| db_keyword_hits      | 0-1      | 2-4        | >= 5      |
| db_pool_pending      | 0        | >= 1       | >= 3      |
| network_errors       | 0        | 1-4        | >= 5      |
| disk_io_bytes        | < 10MB   | 10-80MB    | >= 80MB   |
| jvm_gc_pause_seconds | < 0.10   | 0.10-0.50  | >= 0.50   |

## KNOWN INCIDENT TYPES
CPU_STRESS, MEMORY_STRESS, MEMORY_LEAK, APPLICATION_ERROR_STORM,
DATABASE_CONNECTIVITY, AVAILABILITY_DROP, RESTART_LOOP, GENERAL_DEGRADATION,
NETWORK_PARTITION, DNS_FAILURE, CERTIFICATE_EXPIRY, IMAGE_PULL_FAILURE,
THREAD_POOL_EXHAUSTION, CACHE_FAILURE, STORAGE_PRESSURE, NODE_PRESSURE,
RESOURCE_QUOTA_EXCEEDED, SECURITY_ANOMALY, CIRCUIT_BREAKER_OPEN,
HEALTH_CHECK_FAILURE, EVICTION_PRESSURE, CONFIG_DRIFT, VM_DOWN,
JVM_GC_PRESSURE, FILE_DESCRIPTOR_EXHAUSTION, NETWORK_SATURATION,
DISK_IO_SATURATION, VM_WATCHDOG_RESTART, NO_INCIDENT

## EXECUTABLE ACTIONS
- RESTART_POD       : restart the failing pod (safe, auto-executable)
- RESTART_VM        : restart the underlying VM/node (for VM_DOWN or NODE_PRESSURE with node_not_ready_total>=1)
- SCALE_DEPLOYMENT  : add replicas to handle load (safe, auto-executable)
- INVESTIGATE       : collect diagnostics, no cluster change
- ESCALATE          : alert human operator (for CRITICAL or unknown)
- NO_ACTION         : no intervention needed

## AUTO-EXECUTION RULES
auto_execute=true ONLY when ALL conditions are met:
1. confidence >= 0.55
2. severity = HIGH (never CRITICAL)
3. executable = RESTART_POD or SCALE_DEPLOYMENT

## SPECIAL RULES
- VM_DOWN or NODE_PRESSURE with node_not_ready_total >= 1: prefer executable=RESTART_VM
- NETWORK_PARTITION with multiple node_not_ready: severity=CRITICAL, executable=ESCALATE
- JVM_GC_PRESSURE with jvm_gc_pause_seconds >= 0.5: escalate to HIGH, consider RESTART_POD

## LEARNING
When past incidents are provided, prefer actions that previously RESOLVED similar incidents.
Avoid actions that previously FAILED. Use history to adjust confidence.

## OUTPUT FORMAT (strict JSON only)
{
  "incident": true or false,
  "incident_type": "PRECISE_TYPE_IN_SNAKE_CASE",
  "severity": "LOW|MEDIUM|HIGH|CRITICAL",
  "confidence": 0.50-0.95,
  "recommended_action": "descriptive action text",
  "executable": "RESTART_POD|RESTART_VM|SCALE_DEPLOYMENT|INVESTIGATE|ESCALATE|NO_ACTION",
  "auto_execute": true or false,
  "explanation": "one concise sentence"
}"""

# Map incident types to synthetic signal profiles for user prompt generation
SIGNAL_PROFILES = {
    "RESTART_LOOP": {
        "cpu": 0.32, "memory_gb": 0.45, "http_5xx": 0.08, "latency": 0.9,
        "restarts": 3, "unavailable": 1, "db_hits": 1, "db_pending": 0, "risk": 0.55,
        "log_signals": "multiple OOMKilled and CrashLoopBackOff events in logs",
    },
    "CPU_STRESS": {
        "cpu": 0.82, "memory_gb": 0.50, "http_5xx": 0.03, "latency": 0.6,
        "restarts": 0, "unavailable": 0, "db_hits": 0, "db_pending": 0, "risk": 0.50,
        "log_signals": "high CPU throttling detected, slow response times",
    },
    "MEMORY_STRESS": {
        "cpu": 0.30, "memory_gb": 1.10, "http_5xx": 0.05, "latency": 0.4,
        "restarts": 0, "unavailable": 0, "db_hits": 2, "db_pending": 0, "risk": 0.48,
        "log_signals": "memory pressure warnings, GC overhead limit exceeded",
    },
    "DATABASE_CONNECTIVITY": {
        "cpu": 0.25, "memory_gb": 0.40, "http_5xx": 0.15, "latency": 2.1,
        "restarts": 0, "unavailable": 0, "db_hits": 8, "db_pending": 3, "risk": 0.60,
        "log_signals": "connection pool exhausted, JDBC timeout errors, db connection refused",
    },
    "AVAILABILITY_DROP": {
        "cpu": 0.35, "memory_gb": 0.55, "http_5xx": 0.22, "latency": 1.5,
        "restarts": 0, "unavailable": 1, "db_hits": 1, "db_pending": 0, "risk": 0.52,
        "log_signals": "pod NotReady, readiness probe failures, service endpoints unavailable",
    },
    "APPLICATION_ERROR_STORM": {
        "cpu": 0.55, "memory_gb": 0.60, "http_5xx": 0.35, "latency": 0.8,
        "restarts": 1, "unavailable": 0, "db_hits": 2, "db_pending": 0, "risk": 0.65,
        "log_signals": "500 errors spike, NullPointerException, unhandled exceptions in logs",
    },
    "GENERAL_DEGRADATION": {
        "cpu": 0.50, "memory_gb": 0.70, "http_5xx": 0.06, "latency": 0.9,
        "restarts": 0, "unavailable": 0, "db_hits": 3, "db_pending": 1, "risk": 0.40,
        "log_signals": "increased error rate, slow responses, degraded throughput",
    },
    "VM_DOWN": {
        "cpu": 0.0, "memory_gb": 0.0, "http_5xx": 0.60, "latency": 5.0,
        "restarts": 2, "unavailable": 2, "db_hits": 5, "db_pending": 2, "risk": 0.80,
        "log_signals": "node NotReady, monitoring VM unreachable, infrastructure alerts",
    },
    "THREAD_POOL_EXHAUSTION": {
        "cpu": 0.70, "memory_gb": 0.85, "http_5xx": 0.25, "latency": 3.5,
        "restarts": 0, "unavailable": 0, "db_hits": 4, "db_pending": 2, "risk": 0.58,
        "log_signals": "thread pool full, task queue overflow, executor rejected execution",
    },
    "MEMORY_LEAK": {
        "cpu": 0.40, "memory_gb": 1.35, "http_5xx": 0.04, "latency": 0.5,
        "restarts": 1, "unavailable": 0, "db_hits": 1, "db_pending": 0, "risk": 0.55,
        "log_signals": "gradual memory increase over time, heap dump warnings, OOM approaching",
    },
    "HEALTH_CHECK_FAILURE": {
        "cpu": 0.20, "memory_gb": 0.35, "http_5xx": 0.10, "latency": 0.3,
        "restarts": 2, "unavailable": 1, "db_hits": 0, "db_pending": 0, "risk": 0.50,
        "log_signals": "liveness probe failed, readiness probe timeout, pod restarted by kubelet",
    },
    "STORAGE_PRESSURE": {
        "cpu": 0.30, "memory_gb": 0.40, "http_5xx": 0.05, "latency": 1.2,
        "restarts": 0, "unavailable": 0, "db_hits": 6, "db_pending": 0, "risk": 0.45,
        "log_signals": "disk full, PVC write errors, no space left on device",
    },
    "CIRCUIT_BREAKER_OPEN": {
        "cpu": 0.25, "memory_gb": 0.45, "http_5xx": 0.40, "latency": 0.2,
        "restarts": 0, "unavailable": 0, "db_hits": 3, "db_pending": 1, "risk": 0.55,
        "log_signals": "circuit breaker open, downstream service unavailable, fallback triggered",
    },
    "NO_INCIDENT": {
        "cpu": 0.15, "memory_gb": 0.30, "http_5xx": 0.01, "latency": 0.12,
        "restarts": 0, "unavailable": 0, "db_hits": 0, "db_pending": 0, "risk": 0.05,
        "log_signals": "all systems nominal, normal traffic patterns",
    },
    # ── 11 NEW INCIDENT TYPES ────────────────────────────────────────────────
    "NETWORK_PARTITION": {
        "cpu": 0.15, "memory_gb": 0.25, "http_5xx": 0.65, "latency": 6.5,
        "restarts": 1, "unavailable": 2, "db_hits": 1, "db_pending": 0, "risk": 0.82,
        "log_signals": "network timeout, connection refused to peers, node_not_ready=2, network_errors=15, partition detected in cluster",
    },
    "DNS_FAILURE": {
        "cpu": 0.18, "memory_gb": 0.22, "http_5xx": 0.45, "latency": 5.0,
        "restarts": 1, "unavailable": 1, "db_hits": 0, "db_pending": 0, "risk": 0.68,
        "log_signals": "DNS resolution failed, NXDOMAIN errors, service discovery timeout, network_errors=6",
    },
    "CERTIFICATE_EXPIRY": {
        "cpu": 0.15, "memory_gb": 0.25, "http_5xx": 0.40, "latency": 1.5,
        "restarts": 0, "unavailable": 0, "db_hits": 0, "db_pending": 0, "risk": 0.55,
        "log_signals": "TLS handshake failed, certificate expired or expiring soon, SSL error in logs, http_request_rate=30",
    },
    "IMAGE_PULL_FAILURE": {
        "cpu": 0.02, "memory_gb": 0.12, "http_5xx": 0.50, "latency": 1.2,
        "restarts": 6, "unavailable": 3, "db_hits": 0, "db_pending": 0, "risk": 0.70,
        "log_signals": "ErrImagePull, ImagePullBackOff, failed to pull image from registry, pod stuck Pending",
    },
    "NODE_PRESSURE": {
        "cpu": 0.25, "memory_gb": 0.45, "http_5xx": 0.25, "latency": 2.5,
        "restarts": 1, "unavailable": 2, "db_hits": 1, "db_pending": 0, "risk": 0.78,
        "log_signals": "node memory pressure=1, node disk pressure=1, node_not_ready_total=1, eviction threshold reached, pvc_used=12GB",
    },
    "RESOURCE_QUOTA_EXCEEDED": {
        "cpu": 0.20, "memory_gb": 0.40, "http_5xx": 0.22, "latency": 1.2,
        "restarts": 1, "unavailable": 3, "db_hits": 0, "db_pending": 0, "risk": 0.62,
        "log_signals": "exceeded quota for namespace, pod cannot be scheduled, resource quota exceeded, http_request_rate=300",
    },
    "JVM_GC_PRESSURE": {
        "cpu": 0.50, "memory_gb": 1.55, "http_5xx": 0.12, "latency": 3.2,
        "restarts": 0, "unavailable": 0, "db_hits": 0, "db_pending": 0, "risk": 0.65,
        "log_signals": "GC pause duration high jvm_gc_pause_seconds=1.8, full GC storm, jvm_threads_live=350, heap pressure warning",
    },
    "FILE_DESCRIPTOR_EXHAUSTION": {
        "cpu": 0.40, "memory_gb": 0.55, "http_5xx": 0.22, "latency": 2.5,
        "restarts": 1, "unavailable": 0, "db_hits": 0, "db_pending": 0, "risk": 0.60,
        "log_signals": "too many open files, open_file_descriptors=980, socket creation failed, EMFILE error in logs",
    },
    "NETWORK_SATURATION": {
        "cpu": 0.50, "memory_gb": 0.40, "http_5xx": 0.15, "latency": 3.8,
        "restarts": 0, "unavailable": 1, "db_hits": 0, "db_pending": 0, "risk": 0.68,
        "log_signals": "network bandwidth saturated, network_rx=120MB, network_tx=110MB, http_request_rate=600, packet drops detected",
    },
    "DISK_IO_SATURATION": {
        "cpu": 0.35, "memory_gb": 0.40, "http_5xx": 0.12, "latency": 3.5,
        "restarts": 0, "unavailable": 0, "db_hits": 10, "db_pending": 0, "risk": 0.62,
        "log_signals": "disk_io_bytes=180MB, I/O wait high, pvc_used=16GB, slow disk writes detected, db query latency elevated",
    },
    "VM_WATCHDOG_RESTART": {
        "cpu": 0.05, "memory_gb": 0.05, "http_5xx": 0.70, "latency": 9.0,
        "restarts": 3, "unavailable": 3, "db_hits": 1, "db_pending": 0, "risk": 0.88,
        "log_signals": "watchdog triggered VM restart, node_not_ready_total=2, network_errors=30, service completely unavailable during restart",
    },
}

# Expected output for each incident type
EXPECTED_OUTPUTS = {
    "RESTART_LOOP": {
        "incident": True, "incident_type": "RESTART_LOOP", "severity": "HIGH",
        "confidence": 0.82, "recommended_action": "RESTART_POD_AND_INVESTIGATE",
        "executable": "RESTART_POD", "auto_execute": True,
        "explanation": "Pod crash loop detected — restart to restore service while investigating root cause.",
    },
    "CPU_STRESS": {
        "incident": True, "incident_type": "CPU_STRESS", "severity": "HIGH",
        "confidence": 0.78, "recommended_action": "SCALE_DEPLOYMENT_AND_CAPTURE_CPU_PROFILE",
        "executable": "SCALE_DEPLOYMENT", "auto_execute": True,
        "explanation": "CPU usage critical — scale deployment to distribute load and relieve throttling.",
    },
    "MEMORY_STRESS": {
        "incident": True, "incident_type": "MEMORY_STRESS", "severity": "HIGH",
        "confidence": 0.75, "recommended_action": "RESTART_POD_AND_TUNE_MEMORY_LIMITS",
        "executable": "RESTART_POD", "auto_execute": True,
        "explanation": "Memory usage near limit — restart pod to reclaim memory and prevent OOM kill.",
    },
    "DATABASE_CONNECTIVITY": {
        "incident": True, "incident_type": "DATABASE_CONNECTIVITY", "severity": "HIGH",
        "confidence": 0.80, "recommended_action": "CHECK_DATABASE_CONNECTION_POOL_AND_RESTART",
        "executable": "RESTART_POD", "auto_execute": True,
        "explanation": "Database connection pool exhausted — restart application to reset connections.",
    },
    "AVAILABILITY_DROP": {
        "incident": True, "incident_type": "AVAILABILITY_DROP", "severity": "HIGH",
        "confidence": 0.77, "recommended_action": "SCALE_DEPLOYMENT_AND_REBALANCE",
        "executable": "SCALE_DEPLOYMENT", "auto_execute": True,
        "explanation": "Replicas unavailable — scale deployment to restore service availability.",
    },
    "APPLICATION_ERROR_STORM": {
        "incident": True, "incident_type": "APPLICATION_ERROR_STORM", "severity": "CRITICAL",
        "confidence": 0.85, "recommended_action": "ROLLBACK_LAST_RELEASE_AND_SHED_TRAFFIC",
        "executable": "ESCALATE", "auto_execute": False,
        "explanation": "Massive HTTP 5xx spike — human review required before rollback.",
    },
    "GENERAL_DEGRADATION": {
        "incident": True, "incident_type": "GENERAL_DEGRADATION", "severity": "MEDIUM",
        "confidence": 0.60, "recommended_action": "CAPTURE_DIAGNOSTICS_AND_INVESTIGATE",
        "executable": "INVESTIGATE", "auto_execute": False,
        "explanation": "Multiple mild anomalies — collect diagnostics before taking action.",
    },
    "VM_DOWN": {
        "incident": True, "incident_type": "VM_DOWN", "severity": "CRITICAL",
        "confidence": 0.90, "recommended_action": "ESCALATE_VM_OUTAGE_AND_FAILOVER",
        "executable": "RESTART_VM", "auto_execute": False,
        "explanation": "Infrastructure VM unreachable — immediate human escalation and VM restart required.",
    },
    "THREAD_POOL_EXHAUSTION": {
        "incident": True, "incident_type": "THREAD_POOL_EXHAUSTION", "severity": "HIGH",
        "confidence": 0.73, "recommended_action": "RESTART_POD_AND_SCALE_MEMORY_LIMITS",
        "executable": "RESTART_POD", "auto_execute": True,
        "explanation": "Thread pool saturated — restart to drain queues and restore processing.",
    },
    "MEMORY_LEAK": {
        "incident": True, "incident_type": "MEMORY_LEAK", "severity": "HIGH",
        "confidence": 0.70, "recommended_action": "RESTART_POD_AND_TUNE_MEMORY_LIMITS",
        "executable": "RESTART_POD", "auto_execute": True,
        "explanation": "Memory leak detected — restart pod to prevent OOM kill and plan fix.",
    },
    "HEALTH_CHECK_FAILURE": {
        "incident": True, "incident_type": "HEALTH_CHECK_FAILURE", "severity": "HIGH",
        "confidence": 0.76, "recommended_action": "RESTART_POD_AND_INVESTIGATE",
        "executable": "RESTART_POD", "auto_execute": True,
        "explanation": "Health checks failing repeatedly — restart pod to recover service.",
    },
    "STORAGE_PRESSURE": {
        "incident": True, "incident_type": "STORAGE_PRESSURE", "severity": "HIGH",
        "confidence": 0.72, "recommended_action": "INVESTIGATE_AND_CLEAN_STORAGE",
        "executable": "INVESTIGATE", "auto_execute": False,
        "explanation": "Storage pressure detected — investigate disk usage before automated action.",
    },
    "CIRCUIT_BREAKER_OPEN": {
        "incident": True, "incident_type": "CIRCUIT_BREAKER_OPEN", "severity": "HIGH",
        "confidence": 0.74, "recommended_action": "RESTART_POD_AND_INVESTIGATE",
        "executable": "RESTART_POD", "auto_execute": True,
        "explanation": "Circuit breaker open — restart to reset state and restore downstream calls.",
    },
    "NO_INCIDENT": {
        "incident": False, "incident_type": "NO_INCIDENT", "severity": "LOW",
        "confidence": 0.10, "recommended_action": "NO_ACTION",
        "executable": "NO_ACTION", "auto_execute": False,
        "explanation": "All Kubernetes signals within normal ranges — no incident detected.",
    },
    # ── 11 NEW EXPECTED OUTPUTS ──────────────────────────────────────────────
    "NETWORK_PARTITION": {
        "incident": True, "incident_type": "NETWORK_PARTITION", "severity": "CRITICAL",
        "confidence": 0.88, "recommended_action": "ESCALATE_VM_OUTAGE_AND_FAILOVER",
        "executable": "ESCALATE", "auto_execute": False,
        "explanation": "Network partition detected with multiple unreachable nodes — escalate immediately for infrastructure failover.",
    },
    "DNS_FAILURE": {
        "incident": True, "incident_type": "DNS_FAILURE", "severity": "HIGH",
        "confidence": 0.79, "recommended_action": "INVESTIGATE_DNS_AND_RESTART_COREDNS",
        "executable": "INVESTIGATE", "auto_execute": False,
        "explanation": "DNS resolution failures causing service discovery breakdown — investigate CoreDNS and network configuration.",
    },
    "CERTIFICATE_EXPIRY": {
        "incident": True, "incident_type": "CERTIFICATE_EXPIRY", "severity": "HIGH",
        "confidence": 0.80, "recommended_action": "INVESTIGATE_AND_RENEW_CERTIFICATE",
        "executable": "INVESTIGATE", "auto_execute": False,
        "explanation": "TLS certificate expired or expiring — manual certificate renewal required to restore HTTPS.",
    },
    "IMAGE_PULL_FAILURE": {
        "incident": True, "incident_type": "IMAGE_PULL_FAILURE", "severity": "HIGH",
        "confidence": 0.82, "recommended_action": "INVESTIGATE_REGISTRY_AND_IMAGE_PULL_SECRET",
        "executable": "INVESTIGATE", "auto_execute": False,
        "explanation": "Pods stuck in ImagePullBackOff — investigate registry access and image pull secrets.",
    },
    "NODE_PRESSURE": {
        "incident": True, "incident_type": "NODE_PRESSURE", "severity": "CRITICAL",
        "confidence": 0.85, "recommended_action": "ESCALATE_AND_RESTART_VM",
        "executable": "RESTART_VM", "auto_execute": False,
        "explanation": "Node memory/disk pressure with node not ready — restart VM to recover node and prevent pod evictions.",
    },
    "RESOURCE_QUOTA_EXCEEDED": {
        "incident": True, "incident_type": "RESOURCE_QUOTA_EXCEEDED", "severity": "HIGH",
        "confidence": 0.76, "recommended_action": "INVESTIGATE_QUOTA_AND_SCALE",
        "executable": "INVESTIGATE", "auto_execute": False,
        "explanation": "Namespace resource quota exceeded — investigate quota limits and request increase or scale down usage.",
    },
    "JVM_GC_PRESSURE": {
        "incident": True, "incident_type": "JVM_GC_PRESSURE", "severity": "HIGH",
        "confidence": 0.77, "recommended_action": "RESTART_POD_AND_TUNE_JVM_HEAP",
        "executable": "RESTART_POD", "auto_execute": True,
        "explanation": "JVM GC pause duration critical — restart pod and tune heap settings to reduce GC frequency.",
    },
    "FILE_DESCRIPTOR_EXHAUSTION": {
        "incident": True, "incident_type": "FILE_DESCRIPTOR_EXHAUSTION", "severity": "HIGH",
        "confidence": 0.78, "recommended_action": "RESTART_POD_AND_INCREASE_FD_LIMIT",
        "executable": "RESTART_POD", "auto_execute": True,
        "explanation": "File descriptor limit nearly exhausted — restart pod to release FDs and increase ulimit.",
    },
    "NETWORK_SATURATION": {
        "incident": True, "incident_type": "NETWORK_SATURATION", "severity": "HIGH",
        "confidence": 0.76, "recommended_action": "SCALE_DEPLOYMENT_AND_REBALANCE",
        "executable": "SCALE_DEPLOYMENT", "auto_execute": True,
        "explanation": "Network bandwidth saturated — scale deployment to distribute traffic load across more pods.",
    },
    "DISK_IO_SATURATION": {
        "incident": True, "incident_type": "DISK_IO_SATURATION", "severity": "HIGH",
        "confidence": 0.74, "recommended_action": "INVESTIGATE_DISK_IO_AND_OPTIMIZE",
        "executable": "INVESTIGATE", "auto_execute": False,
        "explanation": "Disk I/O saturated with high PVC usage — investigate workload causing excessive I/O before acting.",
    },
    "VM_WATCHDOG_RESTART": {
        "incident": True, "incident_type": "VM_WATCHDOG_RESTART", "severity": "CRITICAL",
        "confidence": 0.91, "recommended_action": "ESCALATE_VM_OUTAGE_AND_FAILOVER",
        "executable": "RESTART_VM", "auto_execute": False,
        "explanation": "VM watchdog triggered restart — escalate immediately, verify node recovery and failover application traffic.",
    },
}


def build_user_prompt(profile: dict, past_exp: str = "") -> str:
    elevated = {}
    if profile["cpu"] >= 0.55:
        elevated["cpu"] = "CRITICAL" if profile["cpu"] >= 0.85 else "HIGH"
    if profile["memory_gb"] >= 0.70:
        elevated["memory"] = "CRITICAL" if profile["memory_gb"] >= 1.20 else "HIGH"
    if profile["http_5xx"] >= 0.08:
        elevated["http_5xx"] = "CRITICAL" if profile["http_5xx"] >= 0.30 else "HIGH"
    if profile["latency"] >= 0.50:
        elevated["latency"] = "CRITICAL" if profile["latency"] >= 2.00 else "HIGH"
    if profile["restarts"] >= 1:
        elevated["restarts"] = "CRITICAL" if profile["restarts"] >= 2 else "ELEVATED"
    if profile["unavailable"] >= 1:
        elevated["unavailable_replicas"] = "HIGH"
    if profile["db_hits"] >= 2:
        elevated["db_keyword_hits"] = "CRITICAL" if profile["db_hits"] >= 5 else "HIGH"
    if profile["db_pending"] >= 1:
        elevated["db_pool_pending"] = "HIGH"

    if not elevated:
        signal_summary = "all signals NORMAL"
    else:
        signal_summary = ", ".join(f"{k}={v}" for k, v in elevated.items())

    prompt = (
        f"Elevated signals: {signal_summary}\n"
        f"Raw metrics: cpu={profile['cpu']:.3f} cores, memory={profile['memory_gb']:.2f}GB, "
        f"http_5xx={profile['http_5xx']:.3f}, latency_p95={profile['latency']:.3f}s, "
        f"restarts={profile['restarts']:.0f}, unavailable={profile['unavailable']:.0f}, "
        f"db_hits={profile['db_hits']:.0f}, db_pool_pending={profile['db_pending']:.0f}, "
        f"overall_risk={profile['risk']:.3f}\n"
        f"Log signals: {profile['log_signals']}"
    )
    if past_exp:
        prompt += f"\n\nLearned from past incidents:\n{past_exp}"
    prompt += "\n\nOutput JSON:"
    return prompt


def fetch_real_incidents(api_base: str) -> list[dict]:
    creds = base64.b64encode(b"operator:operator-change-me").decode()
    incidents = []
    page = 0
    while True:
        req = urllib.request.Request(f"{api_base}/api/incidents?size=100&page={page}&sort=id,asc")
        req.add_header("Authorization", f"Basic {creds}")
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.load(r)
        items = data.get("content", [])
        if not items:
            break
        for inc in items:
            if inc.get("source") == "AI":
                desc = inc.get("description", "")
                m_type = re.search(r'type=([A-Z_]+)', desc)
                m_action = re.search(r'action=([A-Z_]+)', desc)
                m_mode = re.search(r'executionMode=([A-Z_]+)', desc)
                m_conf = re.search(r'confidence=([\d.]+)', desc)
                incidents.append({
                    "id": inc["id"],
                    "incident_type": m_type.group(1) if m_type else "GENERAL_DEGRADATION",
                    "action": m_action.group(1) if m_action else "INVESTIGATE",
                    "execution_mode": m_mode.group(1) if m_mode else "RECOMMEND_ONLY",
                    "severity": inc.get("severity", "MEDIUM"),
                    "status": inc.get("status", "RESOLVED"),
                    "confidence": float(m_conf.group(1).rstrip('.')) if m_conf else 0.5,
                })
        total_pages = data.get("page", {}).get("totalPages", 1)
        page += 1
        if page >= total_pages:
            break
    return incidents


def incidents_to_finetune_rows(incidents: list[dict]) -> list[dict]:
    rows = []
    for inc in incidents:
        inc_type = inc["incident_type"]
        if inc_type not in SIGNAL_PROFILES:
            inc_type = "GENERAL_DEGRADATION"

        profile = SIGNAL_PROFILES[inc_type]
        expected = EXPECTED_OUTPUTS.get(inc_type, EXPECTED_OUTPUTS["GENERAL_DEGRADATION"])

        # Adjust expected output based on actual severity/action from real incident
        output = dict(expected)
        sev = inc.get("severity", "MEDIUM")
        if sev in ("LOW", "MEDIUM", "HIGH", "CRITICAL"):
            output["severity"] = sev
        # If actual status was RESOLVED with action = auto-executable, mark as such
        if inc["status"] == "RESOLVED" and inc["execution_mode"] == "AUTO_EXECUTE_CANDIDATE":
            output["auto_execute"] = True
            output["confidence"] = max(output["confidence"], 0.60)

        user_prompt = build_user_prompt(profile)
        rows.append({
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
                {"role": "assistant", "content": json.dumps(output, ensure_ascii=False)},
            ]
        })
    return rows


def synthetic_rows_for_all_types() -> list[dict]:
    """Generate 30 synthetic rows per incident type with slight signal variations."""
    import random
    random.seed(42)
    rows = []
    for inc_type, base_profile in SIGNAL_PROFILES.items():
        expected = EXPECTED_OUTPUTS.get(inc_type, EXPECTED_OUTPUTS["GENERAL_DEGRADATION"])
        for i in range(30):
            profile = {
                k: max(0.0, v * random.uniform(0.85, 1.15)) if isinstance(v, float) else v
                for k, v in base_profile.items()
            }
            profile["restarts"] = max(0, int(base_profile["restarts"] + random.randint(-1, 1)))
            profile["unavailable"] = max(0, int(base_profile["unavailable"] + random.randint(0, 1)))
            profile["db_hits"] = max(0, int(base_profile["db_hits"] + random.randint(-1, 2)))
            profile["db_pending"] = max(0, int(base_profile["db_pending"] + random.randint(0, 1)))

            # Add past experience context for half the rows
            past_exp = ""
            if i % 2 == 0:
                outcome = "RESOLVED" if expected.get("auto_execute") else "FAILED"
                past_exp = f"  - {inc_type}: action={expected['executable']} → {outcome} in 8min (app=backend)"

            user_prompt = build_user_prompt(profile, past_exp)
            output = dict(expected)
            rows.append({
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                    {"role": "assistant", "content": json.dumps(output, ensure_ascii=False)},
                ]
            })
    return rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate LLM fine-tuning dataset from real incidents.")
    parser.add_argument("--api", default="http://192.168.148.153:30808", help="Backend API base URL")
    parser.add_argument("--output", default="datasets/finetune_dataset.jsonl", help="Output JSONL path")
    parser.add_argument("--no-real", action="store_true", help="Skip real incidents, use synthetic only")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    rows = []

    # Synthetic rows for all types (25 types × 30 = 750 rows)
    print("Generating synthetic rows...")
    synth = synthetic_rows_for_all_types()
    rows.extend(synth)
    print(f"  {len(synth)} synthetic rows generated")

    # Real incident rows
    if not args.no_real:
        print("Fetching real incidents from API...")
        try:
            real_incidents = fetch_real_incidents(args.api)
            real_rows = incidents_to_finetune_rows(real_incidents)
            rows.extend(real_rows)
            print(f"  {len(real_rows)} real incident rows added")
        except Exception as e:
            print(f"  Warning: could not fetch real incidents: {e}")

    # Shuffle
    import random
    random.shuffle(rows)

    # Write JSONL
    with open(output_path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"\nDataset saved: {output_path}")
    print(f"Total rows: {len(rows)}")
    type_dist = {}
    for row in rows:
        try:
            assistant = json.loads(row["messages"][2]["content"])
            t = assistant.get("incident_type", "?")
            type_dist[t] = type_dist.get(t, 0) + 1
        except Exception:
            pass
    print("Distribution by incident type:")
    for k, v in sorted(type_dist.items(), key=lambda x: -x[1]):
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
