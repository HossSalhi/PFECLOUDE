#!/bin/bash
# Orchestrator VM 161 — phase 2 VM side
# 1. Wait test_no_f16 to finish
# 2. Run NO_INCIDENT test (52nd)
# 3. Wait for PC to push Qwen 3B adapter
# 4. Convert HF→GGUF + ollama create
# 5. Run final test 52 × all models
# Marker: /tmp/phase2_vm.done

set -uo pipefail
LOG=/tmp/phase2_vm.log
echo "[$(date)] Phase 2 VM orchestrator started" | tee -a $LOG

VENV=/opt/pfe-ai-service/.venv/bin/python

# Step 1: Wait current test (test_no_f16)
echo "[$(date)] STEP 1: Wait test_no_f16..." | tee -a $LOG
while pgrep -f test_no_f16 > /dev/null; do
  sleep 30
done
echo "[$(date)] STEP 1 DONE" | tee -a $LOG

# Step 2: NO_INCIDENT 52nd test
echo "[$(date)] STEP 2: NO_INCIDENT test..." | tee -a $LOG
$VENV /tmp/test_no_incident.py 2>&1 | tee -a $LOG

# Step 3: Wait for Qwen 3B adapter from PC
echo "[$(date)] STEP 3: Wait Qwen 3B adapter..." | tee -a $LOG
ADAPTER_DIR=/opt/pfe-ai-service/model_artifacts/lora-qwen3b-v3-gpu
while [ ! -f "$ADAPTER_DIR/adapter_model.safetensors" ]; do
  sleep 120
  echo "[$(date)] still waiting for $ADAPTER_DIR/adapter_model.safetensors" | tee -a $LOG
done
echo "[$(date)] STEP 3 DONE - adapter received" | tee -a $LOG

# Step 4: Install llama.cpp if needed + convert + ollama create
echo "[$(date)] STEP 4: GGUF conversion + ollama create..." | tee -a $LOG
if [ ! -d /opt/llama.cpp ]; then
  echo 'kube' | sudo -S git clone https://github.com/ggerganov/llama.cpp /opt/llama.cpp
  echo 'kube' | sudo -S chown -R kube:kube /opt/llama.cpp
fi
# Try simple approach: merge LoRA into base via peft, then convert
cd /opt/llama.cpp
# install conversion deps
/opt/pfe-ai-service/.venv/bin/pip install --quiet -r requirements.txt 2>&1 | tail -3 | tee -a $LOG
$VENV /opt/llama.cpp/convert_hf_to_gguf.py "$ADAPTER_DIR" --outfile /opt/pfe-ai-service/model_artifacts/aiops-sre-v3.gguf 2>&1 | tail -10 | tee -a $LOG

# Create Ollama Modelfile
cat > /tmp/Modelfile_v3 << 'EOF'
FROM /opt/pfe-ai-service/model_artifacts/aiops-sre-v3.gguf
PARAMETER temperature 0.0
PARAMETER num_ctx 4096
EOF
ollama create aiops-sre-v3 -f /tmp/Modelfile_v3 2>&1 | tee -a $LOG

# Step 5: Final test 52 scenarios on all models
echo "[$(date)] STEP 5: Final test 52..." | tee -a $LOG
# Reuse test_all_models_51.py but with PROD_MODEL=aiops-sre-v3
sed 's/aiops-sre-q4-backup/aiops-sre-v3/' /tmp/test_all_models_51.py > /tmp/test_v3_final.py
$VENV /tmp/test_v3_final.py 2>&1 | tee -a $LOG

touch /tmp/phase2_vm.done
echo "[$(date)] PHASE 2 VM COMPLETE" | tee -a $LOG
