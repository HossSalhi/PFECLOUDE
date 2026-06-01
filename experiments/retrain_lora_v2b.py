"""LoRA Qwen 0.5B v2b — 10 epochs, real+synth dataset, target loss < 0.4."""
import json, os
from pathlib import Path

os.environ.setdefault("HF_HOME", "/opt/pfe-ai-service/.cache/huggingface")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

import torch
from datasets import Dataset
from peft import LoraConfig, get_peft_model
from transformers import (
    AutoModelForCausalLM, AutoTokenizer,
    TrainingArguments, Trainer,
    DataCollatorForLanguageModeling,
)

BASE_MODEL = "Qwen/Qwen2.5-0.5B-Instruct"
DATASET_SYNTH = Path("/opt/pfe-ai-service/datasets/lora_instructions_v2.jsonl")
DATASET_REAL_CSV = Path("/opt/pfe-ai-service/datasets/real_chaos_capture.csv")
OUTPUT_DIR = Path("/opt/pfe-ai-service/model_artifacts/aiops-sre-lora-v2b-cpu")

print("[1/6] Loading tokenizer + base...")
tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL)
if tokenizer.pad_token is None:
    tokenizer.pad_token = tokenizer.eos_token

model = AutoModelForCausalLM.from_pretrained(BASE_MODEL, torch_dtype=torch.float32, device_map="cpu", low_cpu_mem_usage=True)
model.config.use_cache = False

print("[2/6] LoRA r=8 alpha=16...")
lora_config = LoraConfig(r=8, lora_alpha=16, lora_dropout=0.05, bias="none",
                        target_modules=["q_proj", "v_proj"], task_type="CAUSAL_LM")
model = get_peft_model(model, lora_config)
model.print_trainable_parameters()

print("[3/6] Build dataset (synth + real)...")
records = []
with DATASET_SYNTH.open("r") as f:
    for line in f:
        rec = json.loads(line)
        prompt = f"<|im_start|>user\n{rec['instruction']}<|im_end|>\n<|im_start|>assistant\n{rec['output']}<|im_end|>"
        records.append({"text": prompt})

if DATASET_REAL_CSV.exists():
    import pandas as pd
    real = pd.read_csv(DATASET_REAL_CSV)
    print(f"  real chaos rows: {len(real)}")
    for _, row in real.iterrows():
        scen = row['scenario']
        cpu = row.get('cpu_usage_cores', 0)
        mem = row.get('memory_working_set_bytes', 0) / 1e9
        http5xx = row.get('http_5xx_ratio', 0)
        lat = row.get('http_latency_p95_seconds', 0)
        unavail = row.get('unavailable_replicas', 0)
        instr = f"Real signal: cpu={cpu:.3f}cores, memory={mem:.2f}GB, http_5xx_ratio={http5xx:.3f}, latency_p95={lat:.3f}s, unavailable_replicas={unavail:.0f}. Identify the incident."
        output = json.dumps({
            "incident": scen != "NO_INCIDENT",
            "incident_type": scen,
            "severity": "HIGH" if scen != "NO_INCIDENT" else "LOW",
            "recommended_action": {
                "CPU_SATURATION": "SCALE_DEPLOYMENT",
                "POD_CRASH_LOOP": "RESTART_POD",
                "NETWORK_DNS_FAILURE": "RESTART_POD",
                "INGRESS_BACKEND_DOWN": "RESTART_POD",
                "DB_CONNECTION_REFUSED": "RESTART_POD",
                "PVC_PENDING": "ESCALATION",
                "POD_IMAGE_PULL_BACKOFF": "ESCALATION",
                "POD_PENDING_INSUFFICIENT_RESOURCES": "SCALE_DOWN",
                "NODE_DISK_FULL": "ESCALATION",
                "METRICS_SCRAPE_FAILURE": "ESCALATION",
                "ERROR_RATE_5XX_SPIKE": "ROLLBACK_DEPLOYMENT",
                "NODE_HIGH_CPU": "SCALE_DOWN",
                "REQUEST_RATE_DROP": "ESCALATION",
                "POD_STUCK_TERMINATING": "FORCE_RECREATE_DEPLOYMENT",
                "NO_INCIDENT": "NO_ACTION",
            }.get(scen, "ESCALATION"),
            "explanation": f"Observed real metrics match {scen} signature."
        })
        prompt = f"<|im_start|>user\n{instr}<|im_end|>\n<|im_start|>assistant\n{output}<|im_end|>"
        records.append({"text": prompt})

ds = Dataset.from_list(records)
print(f"  total examples: {len(ds)}")

def tok_fn(batch):
    enc = tokenizer(batch["text"], truncation=True, max_length=256, padding=False)
    enc["labels"] = enc["input_ids"].copy()
    return enc

ds = ds.map(tok_fn, batched=True, remove_columns=["text"])

print("[4/6] Trainer args 10 epochs grad_accum=4...")
args = TrainingArguments(
    output_dir=str(OUTPUT_DIR),
    num_train_epochs=10,
    per_device_train_batch_size=1,
    gradient_accumulation_steps=4,
    learning_rate=2e-4,
    warmup_ratio=0.05,
    logging_steps=30,
    save_strategy="epoch",
    save_total_limit=2,
    fp16=False, bf16=False,
    dataloader_num_workers=0,
    report_to=[],
    optim="adamw_torch",
)
collator = DataCollatorForLanguageModeling(tokenizer=tokenizer, mlm=False)

trainer = Trainer(model=model, args=args, train_dataset=ds, data_collator=collator)

print("[5/6] Training...")
trainer.train()

print(f"[6/6] Save {OUTPUT_DIR}")
trainer.save_model(str(OUTPUT_DIR))
tokenizer.save_pretrained(str(OUTPUT_DIR))
print("DONE v2b")
