"""Train XGBoost v4 on enriched v3 dataset (real+synth) — fixed CLI args."""
import sys
sys.argv = [
    "train_baseline_v2.py",
    "--dataset", "/opt/pfe-ai-service/datasets/aiops_training_dataset_v3.csv",
    "--output-model", "/opt/pfe-ai-service/model_artifacts/model_v4.joblib",
    "--output-metrics", "/opt/pfe-ai-service/model_artifacts/model_metrics_v4.json",
]
exec(open('/opt/pfe-ai-service/scripts/train_baseline_v2.py').read())
print("XGBoost v4 trained")
