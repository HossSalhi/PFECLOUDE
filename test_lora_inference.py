"""Test the trained LoRA adapter on sample SRE prompts."""
import json
from pathlib import Path

import torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer

BASE = "Qwen/Qwen2.5-0.5B-Instruct"
ADAPTER = "/opt/pfe-ai-service/model_artifacts/aiops-sre-lora-v2-cpu"

print("Loading base + adapter...")
tok = AutoTokenizer.from_pretrained(ADAPTER)
model = AutoModelForCausalLM.from_pretrained(BASE, torch_dtype=torch.float32, device_map="cpu")
model = PeftModel.from_pretrained(model, ADAPTER)
model.eval()

TESTS = [
    "Production signals: container restarts >5/h, OOMKilled or CrashLoopBackOff. Identify the incident and action.",
    "Kubernetes alert: OOMKilled termination, memory at limit. What is the incident type and remediation?",
    "Alert payload: CoreDNS SERVFAIL spike. Classify and act.",
    "Cluster anomaly: HikariCP connections_pending >5. What incident and what to do?",
    "Telemetry: CPU usage >90% sustained 5min. Classify type and action.",
    "All signals normal: CPU=NORMAL, memory=NORMAL, latency=NORMAL. Classify.",
]

for i, q in enumerate(TESTS, 1):
    prompt = f"<|im_start|>user\n{q}<|im_end|>\n<|im_start|>assistant\n"
    inputs = tok(prompt, return_tensors="pt")
    with torch.no_grad():
        out = model.generate(
            **inputs,
            max_new_tokens=120,
            do_sample=False,
            pad_token_id=tok.eos_token_id,
        )
    text = tok.decode(out[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True)
    print(f"\n[{i}] Q: {q[:80]}")
    print(f"    A: {text.strip()[:300]}")
