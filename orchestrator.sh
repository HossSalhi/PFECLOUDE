#!/bin/bash
# HELIOS PFE — Orchestrator final
# Runs sequentially on VM 161 after chaos capture finishes
# Logs to /tmp/orchestrator.log

set -uo pipefail
LOG=/tmp/orchestrator.log
echo "[$(date)] Orchestrator started" | tee -a $LOG

VENV=/opt/pfe-ai-service/.venv/bin/python
DS_DIR=/opt/pfe-ai-service/datasets
MA=/opt/pfe-ai-service/model_artifacts

# ── Step 1: Wait for chaos capture to finish ─────────────────────────────
echo "[$(date)] STEP 1: Wait chaos capture..." | tee -a $LOG
while pgrep -f capture_v3.py > /dev/null; do
  sleep 60
  echo "[$(date)] capture still running ($(wc -l < $DS_DIR/real_chaos_capture.csv 2>/dev/null || echo 0) rows)..." | tee -a $LOG
done
echo "[$(date)] STEP 1 DONE - capture rows: $(wc -l < $DS_DIR/real_chaos_capture.csv)" | tee -a $LOG

# ── Step 2: Generate enriched synthetic dataset (v3) ─────────────────────
echo "[$(date)] STEP 2: Generate enriched synth dataset..." | tee -a $LOG
$VENV /tmp/gen_enriched_synth.py 2>&1 | tee -a $LOG

# ── Step 3: Train XGBoost v4 hybrid ──────────────────────────────────────
echo "[$(date)] STEP 3: Train XGBoost v4..." | tee -a $LOG
$VENV /tmp/train_xgb_v4.py 2>&1 | tee -a $LOG

# ── Step 4: Train Qwen 0.5B v2b on real+synth ────────────────────────────
echo "[$(date)] STEP 4: Train Qwen 0.5B v2b (10 epochs)..." | tee -a $LOG
$VENV /tmp/retrain_lora_v2b.py 2>&1 | tee -a $LOG

# ── Step 5: Test all models on 51 scenarios ──────────────────────────────
echo "[$(date)] STEP 5: Test 51 scenarios on all models..." | tee -a $LOG
$VENV /tmp/test_all_models_51.py 2>&1 | tee -a $LOG

echo "[$(date)] ORCHESTRATOR COMPLETE" | tee -a $LOG
echo "Results CSVs in /tmp/" | tee -a $LOG
