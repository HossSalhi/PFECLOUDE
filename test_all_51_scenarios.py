"""Test all 51 incident scenarios on LoRA 0.5B + XGBoost ML.
Outputs a CSV table with predictions per model.
"""
import csv
import json
import re
from pathlib import Path

# 51 scenarios with their signal signatures (input to LLM)
SCENARIOS = [
    ("POD_CRASH_LOOP", "container restarts >5/h with CrashLoopBackOff"),
    ("POD_OOM_KILLED", "container terminated with reason OOMKilled, memory at limit"),
    ("POD_IMAGE_PULL_BACKOFF", "pod waiting with ImagePullBackOff, registry unreachable"),
    ("POD_PENDING_INSUFFICIENT_RESOURCES", "pod stuck Pending with Insufficient memory event"),
    ("POD_STUCK_TERMINATING", "pod deletionTimestamp set but never terminates"),
    ("POD_LIVENESS_FAILURE", "liveness probe failing repeatedly on healthy port"),
    ("POD_READINESS_FAILURE", "readiness probe failing without container crash"),
    ("POD_INIT_CONTAINER_FAILURE", "init container exits non-zero, pod stuck"),
    ("HIGH_LATENCY_P95", "http_server_requests p95 latency 2.5s sustained 5min"),
    ("HIGH_LATENCY_P99", "http p99 latency 4s tail spike"),
    ("ERROR_RATE_5XX_SPIKE", "http 5xx ratio 12% over baseline 0.5%"),
    ("ERROR_RATE_4XX_SPIKE", "http 4xx ratio 15% client errors spike"),
    ("REQUEST_RATE_DROP", "request rate dropped 70% from baseline"),
    ("MEMORY_LEAK", "container memory growing linearly +50MB/min over 1h"),
    ("CPU_SATURATION", "container CPU usage 95% sustained 5min"),
    ("THREAD_POOL_EXHAUSTION", "jvm_threads BLOCKED state count high, tomcat queue full"),
    ("DB_CONNECTION_POOL_EXHAUSTION", "hikaricp_connections_pending 15 active 30/30"),
    ("CIRCUIT_BREAKER_OPEN", "resilience4j circuitbreaker_state=open for orderservice"),
    ("DB_SLOW_QUERIES", "pg_stat_statements mean_time 3s on top queries"),
    ("DB_DEADLOCK", "pg_stat_database deadlocks count incrementing"),
    ("DB_REPLICATION_LAG", "pg_replication_lag_seconds 120s replica behind"),
    ("DB_DISK_FULL", "postgres data volume usage 95%, writes failing"),
    ("DB_CONNECTION_REFUSED", "up{job=postgres}=0, ECONNREFUSED in app logs"),
    ("DB_HIGH_WRITE_LOAD", "tup_inserted+updated rate 50k/s, IOPS saturated"),
    ("NETWORK_DNS_FAILURE", "coredns_dns_responses SERVFAIL spike, nslookup fails"),
    ("NETWORK_PACKET_LOSS", "node_network_receive_errs rate elevated, iperf packet loss"),
    ("NETWORK_LATENCY_BETWEEN_NODES", "inter-pod RTT 80ms (baseline 2ms)"),
    ("INGRESS_BACKEND_DOWN", "nginx_ingress_responses status=502 spike, upstream down"),
    ("SERVICE_DISCOVERY_FAILURE", "kube endpoints stale, app cannot reach service"),
    ("TLS_CERTIFICATE_EXPIRY", "cert_expiry_seconds 5d for ingress wildcard"),
    ("NETWORK_PARTITION", "etcd member disagrees on cluster state, split brain"),
    ("EXTERNAL_API_DEPENDENCY_DOWN", "errors from app to stripe.com IP spiking 100%"),
    ("NODE_NOT_READY", "kube_node_status Ready=false on worker1"),
    ("NODE_HIGH_CPU", "node cpu_idle 5%, 95% busy 10min"),
    ("NODE_HIGH_MEMORY", "node memory_available 8%, swap=0 OOM imminent"),
    ("NODE_DISK_FULL", "node_filesystem_avail 3% on /var/lib/containerd"),
    ("NODE_DISK_IO_SATURATION", "node_disk_io_time_weighted 0.95, queue depth 100"),
    ("NODE_KUBELET_DOWN", "up{job=kubelet}=0, node showing NotReady"),
    ("NODE_CLOCK_SKEW", "node_time_seconds drift 5s vs NTP server"),
    ("NODE_HARDWARE_FAILURE", "node_edac_correctable_errors 5000/h, RAM degrading"),
    ("AUTH_TOKEN_EXPIRED", "spike of 401 on /api/auth/me, jwt expired"),
    ("AUTH_BRUTE_FORCE_ATTEMPT", "failed_attempts count 500 in 5min from single IP"),
    ("UNAUTHORIZED_API_ACCESS_SPIKE", "403 rate 10x baseline on /api/admin"),
    ("SECRET_ROTATION_FAILURE", "External Secrets Operator errors on AWS rotation"),
    ("PVC_PENDING", "persistentvolumeclaim phase=Pending, no PV available"),
    ("PV_FILESYSTEM_CORRUPTION", "node_filesystem_device_error, mount KO"),
    ("PVC_USAGE_HIGH", "PV used/capacity 92% nearing full"),
    ("PROMETHEUS_OUT_OF_MEMORY", "prometheus pod OOMKilled, head_chunks 10M"),
    ("LOKI_INGESTION_LAG", "loki_distributor appends_failures rate elevated"),
    ("METRICS_SCRAPE_FAILURE", "up{job=*}=0 for multiple targets, scrape timeout"),
    ("UNKNOWN_ANOMALY", "unusual signal combination not matching known patterns"),
]

OUTPUT_CSV = Path("/tmp/lora_51_results.csv")


def call_lora(prompt: str) -> str:
    """Call LoRA 0.5B locally."""
    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    if not hasattr(call_lora, "model"):
        adapter = "/opt/pfe-ai-service/model_artifacts/aiops-sre-lora-v2-cpu"
        base = "Qwen/Qwen2.5-0.5B-Instruct"
        call_lora.tok = AutoTokenizer.from_pretrained(adapter)
        m = AutoModelForCausalLM.from_pretrained(base, torch_dtype=torch.float32, device_map="cpu")
        call_lora.model = PeftModel.from_pretrained(m, adapter)
        call_lora.model.eval()

    full = f"<|im_start|>user\n{prompt}<|im_end|>\n<|im_start|>assistant\n"
    inputs = call_lora.tok(full, return_tensors="pt")
    with torch.no_grad():
        out = call_lora.model.generate(
            **inputs, max_new_tokens=120, do_sample=False,
            pad_token_id=call_lora.tok.eos_token_id,
        )
    return call_lora.tok.decode(out[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True).strip()


def extract_json_field(text: str, field: str) -> str:
    m = re.search(rf'"{field}"\s*:\s*"([^"]+)"', text)
    return m.group(1) if m else ""


print(f"Testing {len(SCENARIOS)} scenarios on LoRA 0.5B...")
results = []
for i, (expected_type, signals) in enumerate(SCENARIOS, 1):
    prompt = f"Production signals: {signals}. Identify the incident and recommend an action."
    try:
        response = call_lora(prompt)
        pred_type = extract_json_field(response, "incident_type")
        pred_action = extract_json_field(response, "recommended_action")
        pred_severity = extract_json_field(response, "severity")
        match = "OK" if pred_type == expected_type else ("CLOSE" if expected_type.split("_")[0] in pred_type else "MISS")
    except Exception as e:
        pred_type = f"ERR: {e}"
        pred_action = ""
        pred_severity = ""
        match = "ERR"

    print(f"[{i:2d}/51] {expected_type:42s} -> {pred_type:30s} ({match})")
    results.append({
        "scenario": expected_type,
        "signals": signals,
        "lora_pred_type": pred_type,
        "lora_pred_action": pred_action,
        "lora_pred_severity": pred_severity,
        "match": match,
    })

with OUTPUT_CSV.open("w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(results[0].keys()))
    w.writeheader()
    w.writerows(results)

ok = sum(1 for r in results if r["match"] == "OK")
close = sum(1 for r in results if r["match"] == "CLOSE")
miss = sum(1 for r in results if r["match"] == "MISS")
err = sum(1 for r in results if r["match"] == "ERR")
print(f"\nSUMMARY: OK={ok}/{len(SCENARIOS)} CLOSE={close} MISS={miss} ERR={err}")
print(f"Saved to {OUTPUT_CSV}")
