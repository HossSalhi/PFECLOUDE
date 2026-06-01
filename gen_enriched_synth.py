"""Generate enriched synthetic dataset v3 — 6320 rows, +30% noise, force non-zero features for hard scenarios."""
import sys
sys.path.insert(0, '/opt/pfe-ai-service/scripts')
import os, random, csv
from pathlib import Path

random.seed(42)
OUT_DIR = Path("/opt/pfe-ai-service/datasets")
OUT_FILE = OUT_DIR / "aiops_training_dataset_v3.csv"

# Load existing v2 and amplify it
import pandas as pd
v2 = pd.read_csv(OUT_DIR / "aiops_training_dataset_v2.csv")
print(f"Loaded v2: {len(v2)} rows, scenarios={v2['scenario'].nunique()}")

# Add 30 more rows per scenario via noise on existing rows
enriched_rows = [v2]
for scenario, group in v2.groupby('scenario'):
    base_samples = group.sample(min(30, len(group)), random_state=42, replace=True)
    noised = base_samples.copy()
    feature_cols = [c for c in v2.columns if c.startswith('feature_')]
    for col in feature_cols:
        if v2[col].dtype.kind in 'fi':
            noise = (1 + (pd.Series([random.uniform(-0.3, 0.3) for _ in range(len(noised))], index=noised.index)))
            noised[col] = (noised[col] * noise).clip(lower=0)
    # Force non-zero for POD_CRASH_LOOP and CPU_SATURATION
    if scenario in ('POD_CRASH_LOOP', 'CPU_SATURATION'):
        if 'feature_cpu_usage_cores' in noised.columns:
            noised['feature_cpu_usage_cores'] = noised['feature_cpu_usage_cores'].clip(lower=0.5)
        if 'feature_pod_restarts_total' in noised.columns and scenario == 'POD_CRASH_LOOP':
            noised['feature_pod_restarts_total'] = noised['feature_pod_restarts_total'].clip(lower=3)
    enriched_rows.append(noised)

enriched = pd.concat(enriched_rows, ignore_index=True)
enriched.to_csv(OUT_FILE, index=False)
print(f"Enriched v3 saved: {len(enriched)} rows, scenarios={enriched['scenario'].nunique()}")

# Also merge with real chaos captures if available
real_csv = OUT_DIR / "real_chaos_capture.csv"
if real_csv.exists():
    real = pd.read_csv(real_csv)
    print(f"Found real captures: {len(real)} rows")
    # Map real columns to v2 feature_* columns where possible
    # Real has: scenario, timestamp, cpu_usage_cores, memory_working_set_bytes, http_5xx_ratio, etc.
    # v2 has: feature_cpu_usage_cores, feature_memory_working_set_bytes, etc.
    mapping = {
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
    real_renamed = real.rename(columns=mapping)
    # Add scenario column with same name
    # Add label columns by lookup from v2
    label_lookup = v2.drop_duplicates('scenario').set_index('scenario')[['incident_label', 'severity_label', 'action_label']]
    real_renamed = real_renamed.merge(label_lookup, left_on='scenario', right_index=True, how='left')
    # Fill other columns with 0
    for col in v2.columns:
        if col not in real_renamed.columns:
            real_renamed[col] = 0
    real_renamed = real_renamed[v2.columns]
    combined = pd.concat([enriched, real_renamed], ignore_index=True)
    combined.to_csv(OUT_FILE, index=False)
    print(f"Combined (with real): {len(combined)} rows")
else:
    print("No real captures yet; using synth only")
