#!/bin/bash
# Deploys the custom aiops-sre Ollama model on the AI VM.
# Run this once after the VM is up: bash deploy-aiops-model.sh
set -e

AI_VM="192.168.148.161"
AI_VM_USER="${AI_VM_USER:-ubuntu}"
AI_SERVICE_DIR="/opt/ai-service"

echo "=== AIOps SRE Model Deployment ==="

# Step 1: Pull base model if not present
echo "[1/4] Pulling qwen2.5:3b base model..."
ssh "${AI_VM_USER}@${AI_VM}" "ollama pull qwen2.5:3b"

# Step 2: Copy Modelfile to VM
echo "[2/4] Copying Modelfile..."
scp "$(dirname "$0")/../ai-service/Modelfile" "${AI_VM_USER}@${AI_VM}:/tmp/Modelfile-aiops-sre"

# Step 3: Create specialized model
echo "[3/4] Creating aiops-sre:latest model..."
ssh "${AI_VM_USER}@${AI_VM}" "ollama create aiops-sre -f /tmp/Modelfile-aiops-sre"

# Step 4: Quick smoke test
echo "[4/4] Smoke test..."
ssh "${AI_VM_USER}@${AI_VM}" "ollama run aiops-sre 'Output JSON: {\"incident\":false,\"incident_type\":\"NO_INCIDENT\",\"severity\":\"LOW\",\"confidence\":0.10,\"recommended_action\":\"NO_ACTION\",\"auto_execute\":false,\"explanation\":\"test\"}' --nowordwrap" | head -5

echo ""
echo "=== Done! Model aiops-sre:latest is ready ==="
echo "Update AIOPS_LLM_MODEL=aiops-sre in your environment to use it."
