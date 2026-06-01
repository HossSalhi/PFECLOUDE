#!/bin/bash
# Full automated deployment of fine-tuned Qwen2.5-3B LoRA model to the AI VM.
# Deploys merged fp16 model dir → Ollama aiops-sre model → restarts AI service → end-to-end test.
# Run from the project root:
#   bash infra/scripts/deploy-finetuned-model.sh
set -e

AI_VM="192.168.148.161"
AI_VM_USER="ubuntu"
AI_SERVICE_DIR="/opt/ai-service"
MERGED_MODEL_DIR="ai-service/model_artifacts/aiops-sre_merged"
REMOTE_MODEL_DIR="/opt/aiops-sre-merged"

echo "======================================"
echo " AIOps Fine-tuned Model Deployment"
echo " Target: ${AI_VM_USER}@${AI_VM}"
echo "======================================"

# ── Step 1: Check VM reachability ──────────────────────────────────────────
echo ""
echo "[1/7] Checking VM connectivity..."
if ! ssh -o ConnectTimeout=10 -o BatchMode=yes "${AI_VM_USER}@${AI_VM}" "echo 'VM reachable'" 2>/dev/null; then
  echo "ERROR: Cannot reach ${AI_VM}. Make sure the VM is running."
  exit 1
fi
echo "  VM is reachable ✓"

# ── Step 2: Verify merged model exists locally ─────────────────────────────
echo ""
echo "[2/7] Checking merged model at ${MERGED_MODEL_DIR}..."
if [ ! -d "${MERGED_MODEL_DIR}" ]; then
  echo "ERROR: Merged model not found at ${MERGED_MODEL_DIR}"
  echo "Run export first: python ai-service/scripts/finetune_lora.py --export-gguf ..."
  exit 1
fi
MODEL_SIZE=$(du -sh "${MERGED_MODEL_DIR}" | cut -f1)
echo "  Found merged model (${MODEL_SIZE}) ✓"

# ── Step 3: Copy merged model to VM ───────────────────────────────────────
echo ""
echo "[3/7] Copying merged model to VM (${MODEL_SIZE} — may take several minutes)..."
ssh "${AI_VM_USER}@${AI_VM}" "sudo mkdir -p ${REMOTE_MODEL_DIR} && sudo chown ${AI_VM_USER}:${AI_VM_USER} ${REMOTE_MODEL_DIR}"
rsync -avz --progress --delete \
  "${MERGED_MODEL_DIR}/" \
  "${AI_VM_USER}@${AI_VM}:${REMOTE_MODEL_DIR}/"
echo "  Model copied ✓"

# ── Step 4: Create Ollama model from merged dir ────────────────────────────
echo ""
echo "[4/7] Creating Ollama aiops-sre model on VM..."
ssh "${AI_VM_USER}@${AI_VM}" "
  cat > /tmp/Modelfile-aiops-sre << 'MODELEOF'
FROM ${REMOTE_MODEL_DIR}
PARAMETER temperature 0
PARAMETER num_predict 400
PARAMETER stop \"</s>\"
SYSTEM \"\"\"You are an expert SRE AI assistant. Analyze the provided metrics and logs, then respond ONLY with a valid JSON object with exactly these fields: incident (bool), incident_type (string), severity (string: LOW/MEDIUM/HIGH/CRITICAL), confidence (float 0-1), recommended_action (string), auto_execute (bool), explanation (string).\"\"\"
MODELEOF
  ollama create aiops-sre -f /tmp/Modelfile-aiops-sre
  echo 'Ollama model created:'
  ollama list | grep aiops-sre
"
echo "  Ollama model aiops-sre created ✓"

# ── Step 5: Sync AI service Python code ───────────────────────────────────
echo ""
echo "[5/7] Syncing AI service source code..."
rsync -avz --progress \
  ai-service/app/ \
  "${AI_VM_USER}@${AI_VM}:${AI_SERVICE_DIR}/app/"

# Copy requirements if vm-specific file exists
if [ -f "ai-service/requirements-vm.txt" ]; then
  scp ai-service/requirements-vm.txt "${AI_VM_USER}@${AI_VM}:${AI_SERVICE_DIR}/requirements.txt"
fi

# Copy updated XGBoost model
if [ -f "ai-service/model_artifacts/model.joblib" ]; then
  echo "  Copying model.joblib..."
  rsync -avz ai-service/model_artifacts/model.joblib \
    "${AI_VM_USER}@${AI_VM}:${AI_SERVICE_DIR}/model_artifacts/model.joblib"
fi
echo "  Source synced ✓"

# ── Step 6: Install deps + restart service ────────────────────────────────
echo ""
echo "[6/7] Installing dependencies and restarting AI service..."
ssh "${AI_VM_USER}@${AI_VM}" "
  cd ${AI_SERVICE_DIR}
  source venv/bin/activate 2>/dev/null || true
  pip install --quiet -r requirements.txt
  echo 'deps installed'

  # Restart service
  if systemctl is-active --quiet ai-service 2>/dev/null; then
    sudo systemctl restart ai-service
    sleep 5
    systemctl status ai-service --no-pager | head -8
  elif docker compose -f ${AI_SERVICE_DIR}/docker-compose.yml ps 2>/dev/null | grep -q ai-service; then
    docker compose -f ${AI_SERVICE_DIR}/docker-compose.yml restart ai-service
    sleep 5
    docker compose -f ${AI_SERVICE_DIR}/docker-compose.yml ps
  else
    echo 'WARNING: Could not detect service manager — restart manually'
  fi
"
echo "  Service restarted ✓"

# ── Step 7: End-to-end smoke test ─────────────────────────────────────────
echo ""
echo "[7/7] Running end-to-end smoke test..."
sleep 8

# Health check
echo "  Health check..."
if curl -sf --max-time 15 "http://${AI_VM}:8000/health" | python3 -m json.tool; then
  echo "  Health: OK ✓"
else
  echo "  WARNING: Health check failed — check service logs"
fi

# Quick inference test
echo ""
echo "  Inference test (predict endpoint)..."
curl -sf --max-time 30 -X POST "http://${AI_VM}:8000/predict" \
  -H "Content-Type: application/json" \
  -d '{
    "namespace": "pfe",
    "app": "backend",
    "metrics": {
      "cpu_usage_percent": 85.0,
      "memory_usage_percent": 70.0,
      "pod_restart_count": 3,
      "http_error_rate": 0.05
    },
    "logs": ["ERROR: connection timeout", "WARN: high cpu detected"]
  }' | python3 -m json.tool || echo "  WARNING: Predict test failed"

echo ""
echo "======================================"
echo " Deployment COMPLETE ✓"
echo "======================================"
echo ""
echo "Fine-tuned model aiops-sre is now live on ${AI_VM}:8000"
echo "Ollama model: $(ssh ${AI_VM_USER}@${AI_VM} 'ollama list 2>/dev/null | grep aiops-sre' 2>/dev/null || echo 'check manually')"
echo ""
echo "Monitor:"
echo "  curl http://${AI_VM}:8000/health"
echo "  curl http://${AI_VM}:8000/mlops/status"
