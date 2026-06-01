"""Train XGBoost v4 on enriched v3 dataset (real+synth)."""
import sys, os
sys.path.insert(0, '/opt/pfe-ai-service/scripts')

# Reuse train_baseline_v2 with v3 dataset path
os.environ['AIOPS_DATASET_PATH'] = '/opt/pfe-ai-service/datasets/aiops_training_dataset_v3.csv'
os.environ['AIOPS_MODEL_OUT'] = '/opt/pfe-ai-service/model_artifacts/model_v4.joblib'

# Load the existing training script logic
exec(open('/opt/pfe-ai-service/scripts/train_baseline_v2.py').read())

print("XGBoost v4 trained")
