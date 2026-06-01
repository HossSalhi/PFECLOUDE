#!/bin/bash
# Deploy updated AI service code + model to 192.168.148.161.
# Run from the project root: bash infra/scripts/deploy-ai-service-update.sh [--gguf path/to/aiops-sre.gguf]
set -e

AI_VM="192.168.148.161"
AI_VM_USER="ubuntu"
AI_SERVICE_DIR="/opt/ai-service"
CHROMA_DIR="/data/chroma"
MODEL_ARTIFACT="ai-service/model_artifacts/model.joblib"

GGUF_PATH=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --gguf) GGUF_PATH="$2"; shift 2 ;;
    *) shift ;;
  esac
done

echo "=== AI Service Update Deployment ==="
echo "Target: ${AI_VM_USER}@${AI_VM}:${AI_SERVICE_DIR}"

# Step 1: Sync updated Python source files
echo ""
echo "[1/6] Syncing Python source files..."
rsync -avz --progress \
  ai-service/app/ \
  "${AI_VM_USER}@${AI_VM}:${AI_SERVICE_DIR}/app/"

# Step 2: Copy requirements
echo ""
echo "[2/6] Syncing requirements..."
scp ai-service/requirements-vm.txt "${AI_VM_USER}@${AI_VM}:${AI_SERVICE_DIR}/requirements.txt"

# Step 3: Copy model artifact
if [ -f "$MODEL_ARTIFACT" ]; then
  echo ""
  echo "[3/6] Copying model.joblib ($(du -sh "$MODEL_ARTIFACT" | cut -f1))..."
  rsync -avz "$MODEL_ARTIFACT" "${AI_VM_USER}@${AI_VM}:${AI_SERVICE_DIR}/model_artifacts/model.joblib"
else
  echo "[3/6] model.joblib not found locally — skipping model update"
fi

# Step 4: Install/update Python deps including chromadb
echo ""
echo "[4/6] Installing/updating Python dependencies..."
ssh "${AI_VM_USER}@${AI_VM}" "
  cd ${AI_SERVICE_DIR} && \
  source venv/bin/activate && \
  pip install --quiet -r requirements.txt && \
  echo 'deps OK'
"

# Step 5: Ensure ChromaDB data directory exists
echo ""
echo "[5/6] Creating ChromaDB persist directory..."
ssh "${AI_VM_USER}@${AI_VM}" "sudo mkdir -p ${CHROMA_DIR} && sudo chown ${AI_VM_USER}:${AI_VM_USER} ${CHROMA_DIR}"

# Step 6: Optionally deploy new GGUF model to Ollama
if [ -n "$GGUF_PATH" ] && [ -f "$GGUF_PATH" ]; then
  echo ""
  echo "[6/6] Deploying fine-tuned GGUF to Ollama..."
  REMOTE_GGUF="/tmp/aiops-sre-$(date +%Y%m%d).gguf"
  scp "$GGUF_PATH" "${AI_VM_USER}@${AI_VM}:${REMOTE_GGUF}"
  ssh "${AI_VM_USER}@${AI_VM}" "
    cat > /tmp/Modelfile-aiops-sre << 'MODELEOF'
FROM ${REMOTE_GGUF}
PARAMETER temperature 0
PARAMETER num_predict 400
PARAMETER stop \"</s>\"
MODELEOF
    ollama create aiops-sre -f /tmp/Modelfile-aiops-sre && \
    echo 'Ollama model aiops-sre updated' && \
    ollama list | grep aiops-sre
  "
else
  echo "[6/6] No GGUF provided — keeping existing Ollama model"
fi

# Restart AI service
echo ""
echo "[restart] Restarting AI service..."
ssh "${AI_VM_USER}@${AI_VM}" "
  if systemctl is-active --quiet ai-service 2>/dev/null; then
    sudo systemctl restart ai-service
    sleep 3
    systemctl status ai-service --no-pager | head -10
  elif [ -f /opt/ai-service/docker-compose.yml ]; then
    cd /opt/ai-service && docker compose restart ai-service
    sleep 3
    docker compose ps
  else
    echo 'WARNING: Unknown service manager — restart manually'
  fi
"

# Smoke test
echo ""
echo "[smoke] Testing /health endpoint..."
sleep 5
curl -sf "http://${AI_VM}:8000/health" | python3 -m json.tool || echo "WARNING: health check failed"

echo ""
echo "=== Deployment complete ==="
echo "Memory stats: curl http://${AI_VM}:8000/memory/stats"
