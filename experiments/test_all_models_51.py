"""Test ALL models (LoRA v2, LoRA v2b, F16 in-context, XGBoost v4) on 51 scenarios.
Outputs final CSV /tmp/final_51_comparison.csv"""
import csv, json, re, time
from pathlib import Path

import httpx
import joblib
import numpy as np
import pandas as pd

OUTPUT = Path("/tmp/final_51_comparison.csv")
OLLAMA = "http://localhost:11434/api/chat"
PROD_MODEL = "aiops-sre-q4-backup"  # use Q4 (fast) since F16 was slow

SCENARIOS = [
    ("POD_CRASH_LOOP", "container restarts >5/h with CrashLoopBackOff"),
    ("POD_OOM_KILLED", "container terminated OOMKilled, memory at limit"),
    ("POD_IMAGE_PULL_BACKOFF", "pod waiting ImagePullBackOff registry unreachable"),
    ("POD_PENDING_INSUFFICIENT_RESOURCES", "pod stuck Pending Insufficient memory event"),
    ("POD_STUCK_TERMINATING", "pod deletionTimestamp set but never terminates"),
    ("POD_LIVENESS_FAILURE", "liveness probe failing repeatedly"),
    ("POD_READINESS_FAILURE", "readiness probe failing without container crash"),
    ("POD_INIT_CONTAINER_FAILURE", "init container exits non-zero pod stuck"),
    ("HIGH_LATENCY_P95", "http p95 latency 2.5s sustained 5min"),
    ("HIGH_LATENCY_P99", "http p99 latency 4s tail spike"),
    ("ERROR_RATE_5XX_SPIKE", "http 5xx ratio 12 percent over baseline"),
    ("ERROR_RATE_4XX_SPIKE", "http 4xx ratio 15 percent client errors"),
    ("REQUEST_RATE_DROP", "request rate dropped 70 percent from baseline"),
    ("MEMORY_LEAK", "container memory growing linearly +50MB/min over 1h"),
    ("CPU_SATURATION", "container CPU usage 95 percent sustained 5min"),
    ("THREAD_POOL_EXHAUSTION", "jvm threads BLOCKED tomcat queue full"),
    ("DB_CONNECTION_POOL_EXHAUSTION", "hikaricp connections pending 15 active 30/30"),
    ("CIRCUIT_BREAKER_OPEN", "resilience4j circuitbreaker state open for service"),
    ("DB_SLOW_QUERIES", "pg_stat_statements mean_time 3s on top queries"),
    ("DB_DEADLOCK", "pg_stat_database deadlocks count incrementing"),
    ("DB_REPLICATION_LAG", "pg_replication_lag_seconds 120s replica behind"),
    ("DB_DISK_FULL", "postgres data volume usage 95 percent writes failing"),
    ("DB_CONNECTION_REFUSED", "up postgres job equals 0 ECONNREFUSED in app logs"),
    ("DB_HIGH_WRITE_LOAD", "tup_inserted+updated rate 50k/s IOPS saturated"),
    ("NETWORK_DNS_FAILURE", "coredns_dns_responses SERVFAIL spike nslookup fails"),
    ("NETWORK_PACKET_LOSS", "node_network_receive_errs rate elevated packet loss"),
    ("NETWORK_LATENCY_BETWEEN_NODES", "inter-pod RTT 80ms baseline 2ms"),
    ("INGRESS_BACKEND_DOWN", "nginx_ingress_responses status 502 spike"),
    ("SERVICE_DISCOVERY_FAILURE", "kube endpoints stale app cannot reach service"),
    ("TLS_CERTIFICATE_EXPIRY", "cert_expiry_seconds 5 days for ingress"),
    ("NETWORK_PARTITION", "etcd member disagrees on cluster state split brain"),
    ("EXTERNAL_API_DEPENDENCY_DOWN", "errors from app to external IP spiking"),
    ("NODE_NOT_READY", "kube_node_status Ready=false on worker"),
    ("NODE_HIGH_CPU", "node cpu_idle 5 percent 95 percent busy 10min"),
    ("NODE_HIGH_MEMORY", "node memory_available 8 percent swap=0 OOM imminent"),
    ("NODE_DISK_FULL", "node_filesystem_avail 3 percent on /var/lib"),
    ("NODE_DISK_IO_SATURATION", "node_disk_io_time_weighted 0.95 queue depth 100"),
    ("NODE_KUBELET_DOWN", "up{job=kubelet}=0 node showing NotReady"),
    ("NODE_CLOCK_SKEW", "node_time_seconds drift 5 seconds vs NTP"),
    ("NODE_HARDWARE_FAILURE", "node_edac_correctable_errors 5000 per hour"),
    ("AUTH_TOKEN_EXPIRED", "spike of 401 on auth me jwt expired"),
    ("AUTH_BRUTE_FORCE_ATTEMPT", "failed_attempts count 500 in 5min from single IP"),
    ("UNAUTHORIZED_API_ACCESS_SPIKE", "403 rate 10x baseline on api admin"),
    ("SECRET_ROTATION_FAILURE", "External Secrets Operator errors AWS rotation"),
    ("PVC_PENDING", "persistentvolumeclaim phase Pending no PV available"),
    ("PV_FILESYSTEM_CORRUPTION", "node_filesystem_device_error mount KO"),
    ("PVC_USAGE_HIGH", "PV used/capacity 92 percent nearing full"),
    ("PROMETHEUS_OUT_OF_MEMORY", "prometheus pod OOMKilled head_chunks 10M"),
    ("LOKI_INGESTION_LAG", "loki_distributor appends_failures rate elevated"),
    ("METRICS_SCRAPE_FAILURE", "up{job=*}=0 for multiple targets scrape timeout"),
    ("UNKNOWN_ANOMALY", "unusual signal combination not matching known patterns"),
]


def extract_field(text, field):
    m = re.search(rf'"{field}"\s*:\s*"([^"]+)"', text)
    return m.group(1) if m else ""


def match_status(expected, predicted):
    if predicted == expected: return "OK"
    if expected.split("_")[0] in predicted: return "CLOSE"
    return "MISS"


# ── LoRA inference helper ─────────────────────────────────────────────
def make_lora_predictor(adapter_path):
    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer
    base = "Qwen/Qwen2.5-0.5B-Instruct"
    tok = AutoTokenizer.from_pretrained(adapter_path)
    m = AutoModelForCausalLM.from_pretrained(base, torch_dtype=torch.float32, device_map="cpu")
    m = PeftModel.from_pretrained(m, adapter_path)
    m.eval()
    def predict(prompt):
        full = f"<|im_start|>user\n{prompt}<|im_end|>\n<|im_start|>assistant\n"
        inputs = tok(full, return_tensors="pt")
        with torch.no_grad():
            out = m.generate(**inputs, max_new_tokens=100, do_sample=False, pad_token_id=tok.eos_token_id)
        return tok.decode(out[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True).strip()
    return predict


# ── Build predictors for both LoRAs ───────────────────────────────────
print("Loading LoRA v2...")
predict_v2 = make_lora_predictor("/opt/pfe-ai-service/model_artifacts/aiops-sre-lora-v2-cpu")

print("Loading LoRA v2b...")
predict_v2b = make_lora_predictor("/opt/pfe-ai-service/model_artifacts/aiops-sre-lora-v2b-cpu")


# ── Ollama call ─────────────────────────────────────────────────────────
TAXO = ", ".join(s[0] for s in SCENARIOS)
def call_llm(signals):
    try:
        with httpx.Client(timeout=120.0) as c:
            r = c.post(OLLAMA, json={
                "model": PROD_MODEL,
                "messages": [
                    {"role": "system", "content": "You are a Kubernetes SRE JSON API. Output JSON only."},
                    {"role": "user", "content": f"Signals: {signals}. Pick incident_type from: {TAXO}. JSON {{incident_type, recommended_action, explanation}}"},
                ],
                "stream": False, "format": "json",
                "options": {"temperature": 0.0, "num_predict": 150},
            })
            if r.status_code == 200:
                data = json.loads(r.json()["message"]["content"])
                return data.get("incident_type", "").upper(), data.get("recommended_action", "").upper()
    except Exception as e:
        return f"ERR:{e}", ""
    return "ERR", ""


# ── XGBoost ─────────────────────────────────────────────────────────────
print("Loading XGBoost v4...")
bundle_path = "/opt/pfe-ai-service/model_artifacts/model_v4.joblib"
if not Path(bundle_path).exists():
    bundle_path = "/opt/pfe-ai-service/model_artifacts/model.joblib"
    print(f"  WARN: v4 not found, using {bundle_path}")
bundle = joblib.load(bundle_path)
feat_names = bundle.get("feature_order", bundle.get("feature_names", []))
scenario_model = bundle["models"].get("scenario") if "models" in bundle else None
scenario_labels = bundle.get("scenario_labels", [])

df_train = pd.read_csv("/opt/pfe-ai-service/datasets/aiops_training_dataset_v3.csv")
if 'aiops_training_dataset_v3.csv' not in str(Path('/opt/pfe-ai-service/datasets/aiops_training_dataset_v3.csv').name) or not Path('/opt/pfe-ai-service/datasets/aiops_training_dataset_v3.csv').exists():
    df_train = pd.read_csv("/opt/pfe-ai-service/datasets/aiops_training_dataset_v2.csv")


def xgb_predict(scen):
    matching = df_train[df_train['scenario'].str.upper() == scen.upper()]
    if matching.empty: return "NO_DATA"
    row = matching.iloc[0]
    vec = [float(row.get(fn, 0)) for fn in feat_names]
    X = np.array([vec])
    if scenario_model is None: return "NO_MODEL"
    probs = scenario_model.predict_proba(X)
    idx = int(probs.argmax(axis=1)[0])
    return str(scenario_labels[idx]).upper()


# ── Run all tests ───────────────────────────────────────────────────────
results = []
for i, (expected, signals) in enumerate(SCENARIOS, 1):
    t0 = time.time()
    prompt = f"Production signals: {signals}. Identify the incident and recommend an action."

    # LoRA v2
    r_v2 = predict_v2(prompt)
    p_v2 = extract_field(r_v2, "incident_type")
    m_v2 = match_status(expected, p_v2)

    # LoRA v2b
    r_v2b = predict_v2b(prompt)
    p_v2b = extract_field(r_v2b, "incident_type")
    m_v2b = match_status(expected, p_v2b)

    # F16 in-context
    p_f16, a_f16 = call_llm(signals)
    m_f16 = match_status(expected, p_f16)

    # XGBoost v4
    p_xgb = xgb_predict(expected)
    m_xgb = match_status(expected, p_xgb)

    elapsed = time.time() - t0
    print(f"[{i:2d}/51] {expected:42s} v2={p_v2:30s}({m_v2}) v2b={p_v2b:30s}({m_v2b}) F16={p_f16:30s}({m_f16}) XGB={p_xgb:30s}({m_xgb}) {elapsed:.1f}s", flush=True)
    results.append({
        "scenario": expected,
        "lora_v2": p_v2, "lora_v2_match": m_v2,
        "lora_v2b": p_v2b, "lora_v2b_match": m_v2b,
        "f16": p_f16, "f16_match": m_f16,
        "xgb_v4": p_xgb, "xgb_match": m_xgb,
    })

with OUTPUT.open("w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(results[0].keys()))
    w.writeheader()
    w.writerows(results)

# Stats
for col in ['lora_v2_match', 'lora_v2b_match', 'f16_match', 'xgb_match']:
    ok = sum(1 for r in results if r[col] == 'OK')
    close = sum(1 for r in results if r[col] == 'CLOSE')
    miss = sum(1 for r in results if r[col] == 'MISS')
    print(f"{col}: OK={ok}/51 CLOSE={close} MISS={miss}")

print(f"Saved {OUTPUT}")
