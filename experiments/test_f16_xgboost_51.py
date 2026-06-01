"""Test all 51 scenarios on F16 (production LLM) + XGBoost ML.
F16 via Ollama API. XGBoost via direct model.joblib inference using training dataset signatures.
"""
import csv
import json
import re
import time
from pathlib import Path

import httpx
import joblib
import numpy as np
import pandas as pd

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "aiops-sre-q4"
MODEL_BUNDLE = "/opt/pfe-ai-service/model_artifacts/model.joblib"
DATASET_CSV = "/opt/pfe-ai-service/datasets/aiops_training_dataset_v2.csv"
OUTPUT = Path("/tmp/f16_xgboost_51.csv")

# Same 51 scenarios as LoRA test
SCENARIOS = [
    ("POD_CRASH_LOOP", "restarts=HIGH, unavailable_replicas=ELEVATED"),
    ("POD_OOM_KILLED", "memory=CRITICAL, restarts=HIGH"),
    ("POD_IMAGE_PULL_BACKOFF", "unavailable_replicas=HIGH, restarts=NORMAL"),
    ("POD_PENDING_INSUFFICIENT_RESOURCES", "unavailable_replicas=HIGH, memory=NORMAL"),
    ("POD_STUCK_TERMINATING", "unavailable_replicas=HIGH, restarts=NORMAL"),
    ("POD_LIVENESS_FAILURE", "restarts=HIGH, http_5xx=NORMAL"),
    ("POD_READINESS_FAILURE", "unavailable_replicas=HIGH, http_5xx=NORMAL"),
    ("POD_INIT_CONTAINER_FAILURE", "unavailable_replicas=HIGH, restarts=ELEVATED"),
    ("HIGH_LATENCY_P95", "latency=CRITICAL, http_5xx=NORMAL"),
    ("HIGH_LATENCY_P99", "latency=CRITICAL, http_5xx=NORMAL"),
    ("ERROR_RATE_5XX_SPIKE", "http_5xx=CRITICAL, latency=ELEVATED"),
    ("ERROR_RATE_4XX_SPIKE", "http_5xx=NORMAL, latency=NORMAL"),
    ("REQUEST_RATE_DROP", "latency=NORMAL, http_5xx=NORMAL"),
    ("MEMORY_LEAK", "memory=CRITICAL, cpu=ELEVATED, restarts=NORMAL"),
    ("CPU_SATURATION", "cpu=CRITICAL, latency=HIGH"),
    ("THREAD_POOL_EXHAUSTION", "latency=HIGH, http_5xx=ELEVATED"),
    ("DB_CONNECTION_POOL_EXHAUSTION", "db_pool_pending=HIGH, db_keyword_hits=HIGH"),
    ("CIRCUIT_BREAKER_OPEN", "http_5xx=HIGH, latency=NORMAL"),
    ("DB_SLOW_QUERIES", "latency=HIGH, db_keyword_hits=ELEVATED"),
    ("DB_DEADLOCK", "db_keyword_hits=HIGH, http_5xx=ELEVATED"),
    ("DB_REPLICATION_LAG", "db_keyword_hits=HIGH"),
    ("DB_DISK_FULL", "db_keyword_hits=HIGH"),
    ("DB_CONNECTION_REFUSED", "db_pool_pending=HIGH, db_keyword_hits=HIGH"),
    ("DB_HIGH_WRITE_LOAD", "db_keyword_hits=HIGH, cpu=ELEVATED"),
    ("NETWORK_DNS_FAILURE", "http_5xx=HIGH, restart_keyword_hits=ELEVATED"),
    ("NETWORK_PACKET_LOSS", "http_5xx=ELEVATED, latency=ELEVATED"),
    ("NETWORK_LATENCY_BETWEEN_NODES", "latency=HIGH"),
    ("INGRESS_BACKEND_DOWN", "http_5xx=CRITICAL, unavailable_replicas=NORMAL"),
    ("SERVICE_DISCOVERY_FAILURE", "http_5xx=HIGH"),
    ("TLS_CERTIFICATE_EXPIRY", "http_5xx=ELEVATED"),
    ("NETWORK_PARTITION", "unavailable_replicas=HIGH, http_5xx=HIGH"),
    ("EXTERNAL_API_DEPENDENCY_DOWN", "http_5xx=HIGH, latency=HIGH"),
    ("NODE_NOT_READY", "unavailable_replicas=HIGH"),
    ("NODE_HIGH_CPU", "cpu=CRITICAL, latency=HIGH"),
    ("NODE_HIGH_MEMORY", "memory=CRITICAL, restarts=ELEVATED"),
    ("NODE_DISK_FULL", "restart_keyword_hits=HIGH, http_5xx=ELEVATED"),
    ("NODE_DISK_IO_SATURATION", "latency=HIGH"),
    ("NODE_KUBELET_DOWN", "unavailable_replicas=HIGH"),
    ("NODE_CLOCK_SKEW", "http_5xx=ELEVATED"),
    ("NODE_HARDWARE_FAILURE", "memory=HIGH, restarts=ELEVATED"),
    ("AUTH_TOKEN_EXPIRED", "http_5xx=NORMAL"),
    ("AUTH_BRUTE_FORCE_ATTEMPT", "http_5xx=NORMAL"),
    ("UNAUTHORIZED_API_ACCESS_SPIKE", "http_5xx=NORMAL"),
    ("SECRET_ROTATION_FAILURE", "restart_keyword_hits=ELEVATED"),
    ("PVC_PENDING", "unavailable_replicas=ELEVATED"),
    ("PV_FILESYSTEM_CORRUPTION", "restart_keyword_hits=HIGH"),
    ("PVC_USAGE_HIGH", "memory=NORMAL"),
    ("PROMETHEUS_OUT_OF_MEMORY", "memory=CRITICAL"),
    ("LOKI_INGESTION_LAG", "latency=NORMAL"),
    ("METRICS_SCRAPE_FAILURE", "http_5xx=NORMAL"),
    ("UNKNOWN_ANOMALY", "cpu=ELEVATED, memory=ELEVATED, latency=ELEVATED, http_5xx=ELEVATED"),
]

TAXONOMY_STR = ",".join(s[0] for s in SCENARIOS)

SYS = (
    "You are a Kubernetes SRE JSON API. You receive pre-classified signals "
    "(NORMAL/ELEVATED/HIGH/CRITICAL) and output ONLY a JSON object."
)

def build_user_msg(signals: str) -> str:
    return (
        f"Elevated signals: {signals}. (all other signals: NORMAL).\n\n"
        f"Pick incident_type from this exact list: {TAXONOMY_STR}.\n"
        f"Pick recommended_action from: RESTART_POD, SCALE_DEPLOYMENT, ROLLBACK_DEPLOYMENT, "
        f"DELETE_CRASHLOOPING_PODS, FORCE_RECREATE_DEPLOYMENT, SCALE_DOWN, ESCALATION, NO_ACTION.\n\n"
        '{"incident_type":"...","severity":"HIGH","confidence":0.8,"recommended_action":"...","explanation":"..."}'
    )


def call_f16(signals: str, retries: int = 2) -> dict:
    for _ in range(retries):
        try:
            with httpx.Client(timeout=180.0) as c:
                r = c.post(OLLAMA_URL, json={
                    "model": MODEL,
                    "messages": [
                        {"role": "system", "content": SYS},
                        {"role": "user", "content": build_user_msg(signals)},
                    ],
                    "stream": False,
                    "format": "json",
                    "options": {"temperature": 0.0, "num_predict": 200},
                })
                if r.status_code == 200:
                    raw = r.json().get("message", {}).get("content", "")
                    return json.loads(raw)
        except Exception as exc:
            print(f"  retry: {exc}")
            time.sleep(2)
    return {"incident_type": "ERR", "recommended_action": "ERR"}


def load_xgboost():
    print(f"Loading XGBoost bundle {MODEL_BUNDLE}...")
    bundle = joblib.load(MODEL_BUNDLE)
    return bundle


def xgb_predict_from_training_row(bundle, expected_type: str, df: pd.DataFrame) -> str:
    """Get an exemplar feature vector from training data for this scenario,
    then run XGBoost scenario classifier on it."""
    matching = df[df["scenario"].astype(str).str.upper() == expected_type.upper()]
    if len(matching) == 0:
        return "NO_TRAINING_DATA"
    row = matching.iloc[0]
    # Build feature vector matching the bundle's expected features
    feature_names = bundle.get("feature_names")
    if not feature_names:
        return "NO_FEATURE_NAMES"
    vec = []
    for fn in feature_names:
        v = row.get(fn, 0.0)
        try:
            vec.append(float(v))
        except (ValueError, TypeError):
            vec.append(0.0)
    X = np.array([vec])
    scenario_model = bundle["models"].get("scenario") if "models" in bundle else bundle.get("scenario_model")
    scenario_labels = bundle.get("scenario_labels", [])
    if scenario_model is None or not scenario_labels:
        return "NO_SCENARIO_MODEL"
    try:
        probs = scenario_model.predict_proba(X)
        idx = int(np.argmax(probs[0]))
        return str(scenario_labels[idx]).upper()
    except Exception as e:
        return f"ERR:{e}"


print("Loading XGBoost + dataset...")
bundle = load_xgboost()
print(f"Bundle keys: {list(bundle.keys())}")
df = pd.read_csv(DATASET_CSV)
print(f"Dataset: {len(df)} rows, columns: {list(df.columns)[:10]}... scenarios: {df['scenario'].nunique() if 'scenario' in df.columns else 'N/A'}")

results = []
print(f"\nTesting {len(SCENARIOS)} scenarios on F16 + XGBoost...")
for i, (expected_type, signals) in enumerate(SCENARIOS, 1):
    t0 = time.time()
    f16_resp = call_f16(signals)
    f16_type = (f16_resp.get("incident_type") or "").strip().upper()
    f16_action = (f16_resp.get("recommended_action") or "").strip().upper()
    xgb_type = xgb_predict_from_training_row(bundle, expected_type, df)
    elapsed = time.time() - t0
    f16_ok = "OK" if f16_type == expected_type else ("CLOSE" if expected_type.split("_")[0] in f16_type else "MISS")
    xgb_ok = "OK" if xgb_type == expected_type else ("CLOSE" if expected_type.split("_")[0] in xgb_type else "MISS")
    print(f"[{i:2d}/51] {expected_type:42s} F16={f16_type:32s}({f16_ok}) XGB={xgb_type:32s}({xgb_ok}) {elapsed:.1f}s")
    results.append({
        "scenario": expected_type,
        "signals": signals,
        "f16_pred": f16_type,
        "f16_action": f16_action,
        "f16_match": f16_ok,
        "xgb_pred": xgb_type,
        "xgb_match": xgb_ok,
        "elapsed_s": round(elapsed, 1),
    })

with OUTPUT.open("w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(results[0].keys()))
    w.writeheader()
    w.writerows(results)

f16_ok_cnt = sum(1 for r in results if r["f16_match"] == "OK")
f16_close_cnt = sum(1 for r in results if r["f16_match"] == "CLOSE")
f16_miss_cnt = sum(1 for r in results if r["f16_match"] == "MISS")
xgb_ok_cnt = sum(1 for r in results if r["xgb_match"] == "OK")
xgb_close_cnt = sum(1 for r in results if r["xgb_match"] == "CLOSE")
xgb_miss_cnt = sum(1 for r in results if r["xgb_match"] == "MISS")
print(f"\nF16 SUMMARY: OK={f16_ok_cnt}/{len(SCENARIOS)} CLOSE={f16_close_cnt} MISS={f16_miss_cnt}")
print(f"XGB SUMMARY: OK={xgb_ok_cnt}/{len(SCENARIOS)} CLOSE={xgb_close_cnt} MISS={xgb_miss_cnt}")
print(f"Saved {OUTPUT}")
