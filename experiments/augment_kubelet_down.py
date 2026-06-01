"""Add 69 real chaos captures to training dataset → v6 for XGBoost v7 retrain."""
import pandas as pd
import numpy as np

SRC = "/opt/pfe-ai-service/datasets/aiops_training_dataset_v5.csv"
REAL = "/opt/pfe-ai-service/datasets/real_chaos_capture.csv"
DST = "/opt/pfe-ai-service/datasets/aiops_training_dataset_v6.csv"

df = pd.read_csv(SRC)
real = pd.read_csv(REAL)
print(f"Source v5: {len(df)} rows, real: {len(real)} rows")

# Map real CSV columns to feature_* training columns
MAP = {
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

# Use existing training rows of same scenario as template, then overwrite with real values
new_rows = []
for _, real_row in real.iterrows():
    scen = str(real_row['scenario']).upper()
    matches = df[df['scenario'].str.upper() == scen]
    if matches.empty:
        continue
    # Copy template from training (preserves all labels + embeddings)
    base = matches.sample(1, random_state=42).iloc[0].copy()
    # Overwrite with real captured features
    for real_col, train_col in MAP.items():
        if real_col in real_row.index and train_col in base.index:
            try:
                base[train_col] = float(real_row[real_col])
            except (ValueError, TypeError):
                pass
    new_rows.append(base.to_dict())

aug_df = pd.DataFrame(new_rows)
out = pd.concat([df, aug_df], ignore_index=True)
out.to_csv(DST, index=False)
print(f"v6 saved: {len(out)} rows (+{len(aug_df)} real)")
print(f"Scenarios with real data: {sorted(set(r['scenario'].upper() for _, r in real.iterrows() if pd.notna(r['scenario'])))}")
