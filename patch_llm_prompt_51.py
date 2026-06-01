"""Patch: inject 51-type taxonomy into LLM system+user prompt.
In-context fallback for Phase 3 LoRA (CPU-only VM 161, no GPU).
"""
from pathlib import Path

INFERENCE_PATH = Path("/opt/pfe-ai-service/app/models/inference.py")
content = INFERENCE_PATH.read_text(encoding="utf-8")

if "TAXONOMY_51_CLASSES" in content:
    print("Already patched, skipping")
    raise SystemExit(0)

# Compact taxonomy: code => one-line signature
TAXONOMY_BLOCK = '''            taxonomy = (
                "TAXONOMY_51_CLASSES (pick the BEST match):\\n"
                "POD: POD_CRASH_LOOP(restarts>5), POD_OOM_KILLED(OOMKilled+mem high), "
                "POD_IMAGE_PULL_BACKOFF(image registry), POD_PENDING_INSUFFICIENT_RESOURCES(cluster ressources), "
                "POD_STUCK_TERMINATING(deletion stuck), POD_LIVENESS_FAILURE(probe KO), "
                "POD_READINESS_FAILURE(not ready no crash), POD_INIT_CONTAINER_FAILURE(init failed); "
                "APP: HIGH_LATENCY_P95(p95>1s), HIGH_LATENCY_P99(tail>3s), "
                "ERROR_RATE_5XX_SPIKE(5xx ratio), ERROR_RATE_4XX_SPIKE(4xx ratio), "
                "REQUEST_RATE_DROP(traffic drop), MEMORY_LEAK(mem growing linear), "
                "CPU_SATURATION(cpu>90%), THREAD_POOL_EXHAUSTION(java threads blocked), "
                "DB_CONNECTION_POOL_EXHAUSTION(hikari pending>5), CIRCUIT_BREAKER_OPEN(CB tripped); "
                "DB: DB_SLOW_QUERIES, DB_DEADLOCK, DB_REPLICATION_LAG, DB_DISK_FULL, "
                "DB_CONNECTION_REFUSED(up=0), DB_HIGH_WRITE_LOAD; "
                "NET: NETWORK_DNS_FAILURE(SERVFAIL spike), NETWORK_PACKET_LOSS, "
                "NETWORK_LATENCY_BETWEEN_NODES, INGRESS_BACKEND_DOWN(502 spike), "
                "SERVICE_DISCOVERY_FAILURE, TLS_CERTIFICATE_EXPIRY, NETWORK_PARTITION, "
                "EXTERNAL_API_DEPENDENCY_DOWN; "
                "NODE: NODE_NOT_READY, NODE_HIGH_CPU(VM cpu>90%), NODE_HIGH_MEMORY, "
                "NODE_DISK_FULL, NODE_DISK_IO_SATURATION, NODE_KUBELET_DOWN, "
                "NODE_CLOCK_SKEW, NODE_HARDWARE_FAILURE; "
                "SEC: AUTH_TOKEN_EXPIRED(401 spike on auth/me), AUTH_BRUTE_FORCE_ATTEMPT, "
                "UNAUTHORIZED_API_ACCESS_SPIKE(403 spike), SECRET_ROTATION_FAILURE; "
                "STORAGE: PVC_PENDING, PV_FILESYSTEM_CORRUPTION, PVC_USAGE_HIGH; "
                "OBS: PROMETHEUS_OUT_OF_MEMORY, LOKI_INGESTION_LAG, METRICS_SCRAPE_FAILURE; "
                "OTHER: GENERAL_DEGRADATION, UNKNOWN_ANOMALY, NO_INCIDENT.\\n"
                "ACTIONS (pick one): RESTART_POD, SCALE_DEPLOYMENT, ROLLBACK_DEPLOYMENT, "
                "DELETE_CRASHLOOPING_PODS, FORCE_RECREATE_DEPLOYMENT, SCALE_DOWN, "
                "ESCALATION, NO_ACTION."
            )
'''

# Find old user_msg block and replace
OLD_BLOCK = '''            user_msg = (
                f"Elevated signals detected: {signal_summary}\\n"
                f"(all other signals: NORMAL)\\n\\n"
                "Based on these signals, output JSON:\\n"
                '{"incident": true_or_false, "incident_type": "TYPE", "severity": "SEV", '
                '"confidence": 0.50_to_0.95, "recommended_action": "ACTION", "auto_execute": true_or_false, '
                '"explanation": "one sentence"}\\n\\n'
                "incident_type: pick the BEST match from: CPU_STRESS MEMORY_STRESS APPLICATION_ERROR_STORM "
                "DATABASE_CONNECTIVITY AVAILABILITY_DROP RESTART_LOOP GENERAL_DEGRADATION NO_INCIDENT\\n"
                "severity: LOW MEDIUM HIGH CRITICAL\\n"
                "recommended_action: pick from: SCALE_DEPLOYMENT_AND_CAPTURE_CPU_PROFILE "
                "RESTART_POD_AND_SCALE_MEMORY_LIMITS ROLLBACK_LAST_RELEASE_AND_SHED_TRAFFIC "
                "FAILOVER_DATABASE_AND_RESTART_WORKLOAD SCALE_DEPLOYMENT_AND_REBALANCE "
                "ROLLBACK_RECENT_CHANGE_AND_RESTART CAPTURE_DIAGNOSTICS_AND_INVESTIGATE "
                "RESTART_POD_AND_INVESTIGATE NO_ACTION\\n"
                "auto_execute=true only if confidence>=0.70 AND severity is HIGH (not CRITICAL)"
            )'''

NEW_BLOCK = TAXONOMY_BLOCK + '''
            user_msg = (
                f"Elevated signals detected: {signal_summary}\\n"
                f"(all other signals: NORMAL)\\n\\n"
                f"{taxonomy}\\n"
                "Output JSON only:\\n"
                '{"incident": true_or_false, "incident_type": "TYPE", "severity": "SEV", '
                '"confidence": 0.50_to_0.95, "recommended_action": "ACTION", "auto_execute": true_or_false, '
                '"explanation": "one sentence in english"}\\n\\n'
                "severity: LOW MEDIUM HIGH CRITICAL.\\n"
                "auto_execute=true only if confidence>=0.70 AND severity in (MEDIUM, HIGH)."
            )'''

if OLD_BLOCK not in content:
    raise RuntimeError("Old user_msg block not found - inference.py structure changed")

content = content.replace(OLD_BLOCK, NEW_BLOCK, 1)
INFERENCE_PATH.write_text(content, encoding="utf-8")
print(f"Patched. New size: {INFERENCE_PATH.stat().st_size} bytes")

import py_compile
try:
    py_compile.compile(str(INFERENCE_PATH), doraise=True)
    print("Syntax OK")
except py_compile.PyCompileError as e:
    print(f"SYNTAX ERROR: {e}")
    raise
