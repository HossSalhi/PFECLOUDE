"""Test XGBoost v6 on REAL chaos captures (real Prometheus metrics, not synthetic)."""
import joblib
import numpy as np
import pandas as pd

REAL_CSV = "/opt/pfe-ai-service/datasets/real_chaos_capture.csv"
MODEL = "/opt/pfe-ai-service/model_artifacts/model_v7.joblib"
TRAIN_CSV = "/opt/pfe-ai-service/datasets/aiops_training_dataset_v5.csv"

bundle = joblib.load(MODEL)
feat_names = bundle.get("feature_order", [])
scenario_model = bundle["models"]["scenario"]
scenario_labels = bundle.get("scenario_labels", [])

real = pd.read_csv(REAL_CSV)
print(f"Real captures: {len(real)} rows, scenarios: {real['scenario'].unique().tolist()}")

# Real CSV has columns like cpu_usage_cores, memory_working_set_bytes, etc.
# Training has feature_cpu_usage_cores etc. Map them.
MAPPING = {
    'cpu_usage_cores': 'feature_cpu_usage_cores',
    'memory_working_set_bytes': 'feature_memory_working_set_bytes',
    'pod_restarts_total': 'feature_pod_restarts_total',
    'http_5xx_ratio': 'feature_http_5xx_ratio',
    'http_latency_p95_seconds': 'feature_http_latency_p95_seconds',
    'http_request_rate': 'feature_http_request_rate',
    'unavailable_replicas': 'feature_unavailable_replicas',
    'up_min': 'feature_up_min',
    'db_pool_pending': 'feature_db_pool_pending',
    'db_pool_active': 'feature_db_pool_active',
    'node_cpu_busy': 'feature_node_cpu_busy',
    'node_disk_free_pct': 'feature_node_disk_free_pct',
    'node_memory_free_pct': 'feature_node_memory_free_pct',
}

# For each real row, build a 416-feature vector
results = []
for _, row in real.iterrows():
    expected = str(row['scenario']).upper()
    vec = []
    for fn in feat_names:
        # Try mapping back
        v = None
        for real_col, train_col in MAPPING.items():
            if train_col == fn and real_col in row.index:
                try:
                    v = float(row[real_col])
                except:
                    v = 0.0
                break
        if v is None:
            v = 0.0  # missing feature
        vec.append(v)
    probs = scenario_model.predict_proba(np.array([vec]))
    pred = str(scenario_labels[int(probs.argmax(axis=1)[0])]).upper()
    conf = float(probs.max(axis=1)[0])
    match = "OK" if pred == expected else ("CLOSE" if expected.split('_')[0] in pred else "MISS")
    results.append({"scenario": expected, "predicted": pred, "confidence": round(conf, 3), "match": match})

# Per-scenario accuracy
df_res = pd.DataFrame(results)
print(f"\n{'='*70}")
print("Per scenario accuracy on REAL captures:")
for scen, g in df_res.groupby('scenario'):
    ok = sum(1 for _, r in g.iterrows() if r['match'] == 'OK')
    total = len(g)
    print(f"  {scen:42s} {ok}/{total} OK = {100*ok/total:.0f}%")
print(f"\n{'='*70}")
total_ok = (df_res['match'] == 'OK').sum()
total_close = (df_res['match'] == 'CLOSE').sum()
total_miss = (df_res['match'] == 'MISS').sum()
print(f"TOTAL: OK={total_ok}/{len(df_res)} CLOSE={total_close} MISS={total_miss}  ({100*total_ok/len(df_res):.1f}% accuracy on REAL data)")
df_res.to_csv('/opt/pfe-ai-service/datasets/test_xgb_v6_on_real.csv', index=False)
print("Saved /opt/pfe-ai-service/datasets/test_xgb_v6_on_real.csv")

# Show mismatches
print("\nMismatches:")
for _, r in df_res[df_res['match'] != 'OK'].iterrows():
    print(f"  {r['scenario']:42s} -> {r['predicted']:30s} ({r['match']}) conf={r['confidence']}")
