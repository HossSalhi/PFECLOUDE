"""Train XGBoost v7 on v6 dataset (with 69 real captures merged in)."""
import sys
sys.argv = [
    "train_baseline_v2.py",
    "--dataset", "/opt/pfe-ai-service/datasets/aiops_training_dataset_v6.csv",
    "--output-model", "/opt/pfe-ai-service/model_artifacts/model_v7.joblib",
    "--output-metrics", "/opt/pfe-ai-service/model_artifacts/model_metrics_v7.json",
]
exec(open('/opt/pfe-ai-service/scripts/train_baseline_v2.py').read())
print("XGBoost v7 trained")
