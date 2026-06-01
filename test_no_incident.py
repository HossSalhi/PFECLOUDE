"""Test NO_INCIDENT (52nd case) on LoRA v2 + v2b + XGBoost v4. Appends to existing CSV."""
import csv, re
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer

OUTPUT = Path("/tmp/final_51_no_f16.csv")  # append the 52nd row

SIGNAL = "All signals normal cpu memory latency http_5xx within baseline no anomalies"
EXPECTED = "NO_INCIDENT"


def extract_field(text, field):
    m = re.search(rf'"{field}"\s*:\s*"([^"]+)"', text)
    return m.group(1) if m else ""


def match_status(expected, predicted):
    if predicted == expected: return "OK"
    if expected.split("_")[0] in predicted: return "CLOSE"
    return "MISS"


def make_predictor(path):
    tok = AutoTokenizer.from_pretrained(path)
    m = AutoModelForCausalLM.from_pretrained("Qwen/Qwen2.5-0.5B-Instruct", torch_dtype=torch.float32, device_map="cpu")
    m = PeftModel.from_pretrained(m, path)
    m.eval()
    def predict(p):
        full = f"<|im_start|>user\n{p}<|im_end|>\n<|im_start|>assistant\n"
        i = tok(full, return_tensors="pt")
        with torch.no_grad():
            o = m.generate(**i, max_new_tokens=100, do_sample=False, pad_token_id=tok.eos_token_id)
        return tok.decode(o[0][i["input_ids"].shape[1]:], skip_special_tokens=True).strip()
    return predict


print("Loading models...")
pv2 = make_predictor("/opt/pfe-ai-service/model_artifacts/aiops-sre-lora-v2-cpu")
pv2b = make_predictor("/opt/pfe-ai-service/model_artifacts/aiops-sre-lora-v2b-cpu")
bundle = joblib.load("/opt/pfe-ai-service/model_artifacts/model_v4.joblib")
feat_names = bundle.get("feature_order", [])
scenario_model = bundle["models"]["scenario"]
scenario_labels = bundle.get("scenario_labels", [])
df = pd.read_csv("/opt/pfe-ai-service/datasets/aiops_training_dataset_v3.csv")

prompt = f"Production signals: {SIGNAL}. Identify the incident and recommend an action."

# v2
p_v2 = extract_field(pv2(prompt), "incident_type") or "?"
# v2b
p_v2b = extract_field(pv2b(prompt), "incident_type") or "?"
# XGBoost
m = df[df['scenario'].str.upper() == EXPECTED]
if not m.empty:
    row = m.iloc[0]
    vec = [float(row.get(fn, 0)) for fn in feat_names]
    probs = scenario_model.predict_proba(np.array([vec]))
    p_xgb = str(scenario_labels[int(probs.argmax(axis=1)[0])]).upper()
else:
    p_xgb = "NO_DATA"

result = {
    "scenario": EXPECTED,
    "lora_v2": p_v2, "lora_v2_match": match_status(EXPECTED, p_v2),
    "lora_v2b": p_v2b, "lora_v2b_match": match_status(EXPECTED, p_v2b),
    "xgb_v4": p_xgb, "xgb_match": match_status(EXPECTED, p_xgb),
}
print(f"[52/52] NO_INCIDENT v2={p_v2}({result['lora_v2_match']}) v2b={p_v2b}({result['lora_v2b_match']}) XGB={p_xgb}({result['xgb_match']})")

# Append to CSV
with OUTPUT.open("a", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(result.keys()))
    w.writerow(result)
print(f"Appended to {OUTPUT}")
