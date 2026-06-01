"""Generate 1000 LoRA training prompts based on 51-class taxonomy.
Format: JSONL Alpaca-style for transformers/peft training.
"""
import json
import random
from pathlib import Path

random.seed(42)
OUT = Path("/opt/pfe-ai-service/datasets/lora_instructions_v2.jsonl")
OUT.parent.mkdir(parents=True, exist_ok=True)

# 51 incident types with signatures and recommended actions
TAXONOMY = [
    # (type, family, signals_desc, action, explanation)
    ("POD_CRASH_LOOP", "POD", "container restarts >5/h, OOMKilled or CrashLoopBackOff", "RESTART_POD", "Repeated container restarts indicate startup failure or unhealthy probes."),
    ("POD_OOM_KILLED", "POD", "OOMKilled termination, memory at limit", "SCALE_UP_MEMORY", "Container exceeded memory limit; raise limits or investigate leak."),
    ("POD_IMAGE_PULL_BACKOFF", "POD", "ImagePullBackOff event, registry unreachable", "ESCALATION", "Image pull failure; verify registry credentials and connectivity."),
    ("POD_PENDING_INSUFFICIENT_RESOURCES", "POD", "Pending phase + Insufficient memory/cpu event", "SCALE_DOWN", "Cluster lacks capacity; free workload or add a node."),
    ("POD_STUCK_TERMINATING", "POD", "deletionTimestamp set but pod persists", "FORCE_RECREATE_DEPLOYMENT", "Pod stuck terminating; force-recreate the deployment."),
    ("POD_LIVENESS_FAILURE", "POD", "liveness probe failing repeatedly", "RESTART_POD", "Liveness probe failure; restart and check app health."),
    ("POD_READINESS_FAILURE", "POD", "readiness probe failing without crash", "ESCALATION", "Pod runs but not ready; investigate dependencies."),
    ("POD_INIT_CONTAINER_FAILURE", "POD", "init container terminated non-zero", "ESCALATION", "Init container failed; fix init configuration."),
    ("HIGH_LATENCY_P95", "APP", "p95 HTTP latency >1s sustained", "SCALE_DEPLOYMENT", "p95 latency elevated; scale out replicas."),
    ("HIGH_LATENCY_P99", "APP", "p99 latency >3s, tail latency", "SCALE_DEPLOYMENT", "Tail latency spike; investigate slow path."),
    ("ERROR_RATE_5XX_SPIKE", "APP", "5xx error ratio >5%", "ROLLBACK_DEPLOYMENT", "Server error spike; roll back recent release."),
    ("ERROR_RATE_4XX_SPIKE", "APP", "4xx client error ratio elevated", "ESCALATION", "Client errors spike; verify API compatibility."),
    ("REQUEST_RATE_DROP", "APP", "traffic drop >50% vs baseline", "ESCALATION", "Sudden traffic drop; verify ingress, DNS, CDN."),
    ("MEMORY_LEAK", "APP", "memory growing linearly over an hour", "RESTART_POD", "Memory leak suspected; restart and capture heap."),
    ("CPU_SATURATION", "APP", "CPU usage >90% sustained 5min", "SCALE_DEPLOYMENT", "CPU saturated; scale out to relieve load."),
    ("THREAD_POOL_EXHAUSTION", "APP", "JVM threads in BLOCKED state", "RESTART_POD", "Thread pool blocked; restart pod and trace deadlock."),
    ("DB_CONNECTION_POOL_EXHAUSTION", "APP", "HikariCP connections_pending >5", "SCALE_DEPLOYMENT", "DB pool exhausted; raise pool or kill slow queries."),
    ("CIRCUIT_BREAKER_OPEN", "APP", "Resilience4j circuitbreaker state=open", "ESCALATION", "Circuit breaker open; fix downstream dependency."),
    ("DB_SLOW_QUERIES", "DB", "pg_stat_statements mean_time >1s", "ESCALATION", "Slow queries detected; add indexes or rewrite."),
    ("DB_DEADLOCK", "DB", "pg_stat_database deadlocks increasing", "ESCALATION", "Deadlock detected; refactor transactions."),
    ("DB_REPLICATION_LAG", "DB", "pg_replication_lag >30s", "ESCALATION", "Replication lag; check network and write load."),
    ("DB_DISK_FULL", "DB", "DB volume usage >90%", "ESCALATION", "DB disk near full; expand PVC or archive."),
    ("DB_CONNECTION_REFUSED", "DB", "up{job=postgres}=0", "RESTART_POD", "DB unreachable; restart DB pod."),
    ("DB_HIGH_WRITE_LOAD", "DB", "tup_inserted+updated rate spike", "SCALE_DEPLOYMENT", "Write storm; rate-limit or scale up DB."),
    ("NETWORK_DNS_FAILURE", "NET", "CoreDNS SERVFAIL spike", "RESTART_POD", "DNS resolution failing; restart CoreDNS."),
    ("NETWORK_PACKET_LOSS", "NET", "node_network_receive_errs rate elevated", "ESCALATION", "Packet loss inter-pod; check CNI."),
    ("NETWORK_LATENCY_BETWEEN_NODES", "NET", "inter-pod RTT >50ms", "ESCALATION", "Inter-node latency; check hypervisor."),
    ("INGRESS_BACKEND_DOWN", "NET", "nginx_ingress responses 502 spike", "RESTART_POD", "Ingress 502s; restart upstream pod."),
    ("SERVICE_DISCOVERY_FAILURE", "NET", "kube_endpoint_address_available mismatch", "RESTART_POD", "Stale endpoints; restart kube-proxy."),
    ("TLS_CERTIFICATE_EXPIRY", "NET", "cert_expiry_seconds <7d", "ESCALATION", "TLS cert expiring soon; renew."),
    ("NETWORK_PARTITION", "NET", "etcd / CNI disagreement on node status", "ESCALATION", "Possible network partition; investigate control plane."),
    ("EXTERNAL_API_DEPENDENCY_DOWN", "NET", "errors from app to external IP spiking", "ESCALATION", "External dependency down; activate circuit breaker."),
    ("NODE_NOT_READY", "NODE", "kube_node_status_condition Ready=false", "ESCALATION", "Worker NotReady; drain or restart VM."),
    ("NODE_HIGH_CPU", "NODE", "node CPU >90% 5min", "SCALE_DOWN", "Node CPU saturated; scale workloads off."),
    ("NODE_HIGH_MEMORY", "NODE", "node memory available <10%", "SCALE_DOWN", "Node memory saturated; drain or bump RAM."),
    ("NODE_DISK_FULL", "NODE", "node_filesystem_avail <5%", "ESCALATION", "Node disk full; cleanup or expand."),
    ("NODE_DISK_IO_SATURATION", "NODE", "node_disk_io_time_weighted high", "ESCALATION", "Disk I/O saturated; identify heavy reader."),
    ("NODE_KUBELET_DOWN", "NODE", "up{job=kubelet}=0", "ESCALATION", "Kubelet down; restart kubelet on node."),
    ("NODE_CLOCK_SKEW", "NODE", "node_time_seconds drift >1s", "ESCALATION", "Clock skew; sync NTP."),
    ("NODE_HARDWARE_FAILURE", "NODE", "node_edac_correctable_errors_total", "ESCALATION", "Hardware errors; migrate workloads and ticket infra."),
    ("AUTH_TOKEN_EXPIRED", "SEC", "401 spike on /api/auth/me", "ESCALATION", "Auth tokens expiring; rotate secrets."),
    ("AUTH_BRUTE_FORCE_ATTEMPT", "SEC", "failed_attempts count spike", "ESCALATION", "Brute force attempt; block IP and alert SOC."),
    ("UNAUTHORIZED_API_ACCESS_SPIKE", "SEC", "403 rate >5x baseline", "ESCALATION", "Unauthorized access spike; investigate SIEM."),
    ("SECRET_ROTATION_FAILURE", "SEC", "External Secrets Operator errors", "ESCALATION", "Secret rotation failing; investigate Vault."),
    ("PVC_PENDING", "STORAGE", "PVC Pending phase", "ESCALATION", "PVC not bound; verify StorageClass."),
    ("PV_FILESYSTEM_CORRUPTION", "STORAGE", "node_filesystem_device_error", "ESCALATION", "Filesystem corruption; run fsck and restore."),
    ("PVC_USAGE_HIGH", "STORAGE", "PV used/capacity >85%", "ESCALATION", "PV near full; expand PVC."),
    ("PROMETHEUS_OUT_OF_MEMORY", "OBS", "Prometheus OOMKilled, head_chunks high", "SCALE_UP_MEMORY", "Prometheus OOM; scale up or reduce retention."),
    ("LOKI_INGESTION_LAG", "OBS", "loki_distributor_ingester_appends_failures high", "RESTART_POD", "Loki ingester lagging; restart."),
    ("METRICS_SCRAPE_FAILURE", "OBS", "up{job=*}=0 + scrape_duration_seconds high", "ESCALATION", "Scrape failures; verify ServiceMonitor."),
    ("UNKNOWN_ANOMALY", "OTHER", "signals elevated but no known signature", "ESCALATION", "Unrecognized anomaly; capture diagnostics."),
]

INSTRUCTION_TEMPLATES = [
    "An SRE alert reports: {signals}. Classify the incident and recommend an action.",
    "Kubernetes alert: {signals}. What is the incident type and remediation?",
    "Production signals: {signals}. Identify the incident and action.",
    "Monitoring detects: {signals}. Output incident_type and recommended_action.",
    "Observed in cluster: {signals}. Provide classification and remediation.",
    "Symptom: {signals}. Diagnose and act.",
    "AIOps trigger: {signals}. Categorize and respond.",
    "Cluster anomaly: {signals}. What incident and what to do?",
    "Telemetry: {signals}. Classify type and action.",
    "Event: {signals}. Identify root cause family and action.",
    "Operator on-call sees: {signals}. Recommend.",
    "Incident: {signals}. Type and remediation.",
    "Alert payload: {signals}. Classify and act.",
    "Sensor: {signals}. Output JSON with type and action.",
    "Notification: {signals}. Diagnose.",
    "Pager: {signals}. Categorize and remediate.",
    "Trace shows: {signals}. Pick the best type and action.",
    "Alarm: {signals}. Type, severity, action.",
    "Health check: {signals}. Diagnose.",
    "Dashboard alert: {signals}. Classify and recommend.",
]

NO_INCIDENT_TEMPLATES = [
    "All signals normal: CPU=NORMAL, memory=NORMAL, latency=NORMAL. Classify.",
    "Health check: cluster nominal, no anomalies. Output.",
    "Steady state observation. Classify.",
    "No deviations from baseline. Type and action.",
    "Healthy cluster snapshot. Diagnose.",
]

rows = []
for typ, family, signals, action, explanation in TAXONOMY:
    n_per_type = 20 if typ != "UNKNOWN_ANOMALY" else 5
    templates = random.sample(INSTRUCTION_TEMPLATES, min(n_per_type, len(INSTRUCTION_TEMPLATES)))
    if len(templates) < n_per_type:
        templates = templates + random.choices(INSTRUCTION_TEMPLATES, k=n_per_type - len(templates))
    for tmpl in templates[:n_per_type]:
        instruction = tmpl.format(signals=signals)
        output = json.dumps({
            "incident": True,
            "incident_type": typ,
            "severity": "HIGH" if family in ("POD", "DB", "SEC", "NODE") else "MEDIUM",
            "recommended_action": action,
            "explanation": explanation,
        })
        rows.append({"instruction": instruction, "input": "", "output": output})

for tmpl in NO_INCIDENT_TEMPLATES * 10:
    output = json.dumps({
        "incident": False,
        "incident_type": "NO_INCIDENT",
        "severity": "LOW",
        "recommended_action": "NO_ACTION",
        "explanation": "All signals within normal ranges.",
    })
    rows.append({"instruction": tmpl, "input": "", "output": output})

random.shuffle(rows)

with OUT.open("w", encoding="utf-8") as f:
    for r in rows:
        f.write(json.dumps(r) + "\n")

print(f"Wrote {len(rows)} examples to {OUT}")
print(f"Family breakdown: {len(TAXONOMY)} types + {len(NO_INCIDENT_TEMPLATES)*10} no-incident")
