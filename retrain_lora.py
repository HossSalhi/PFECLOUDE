"""LoRA fine-tuning on CPU — Qwen2.5-0.5B base, 51-class taxonomy dataset.
~30-60 min on 3 CPUs. Logs go to /tmp/lora_train.log.
"""
import json
import os
from pathlib import Path

os.environ.setdefault("HF_HOME", "/opt/pfe-ai-service/.cache/huggingface")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ["TRANSFORMERS_OFFLINE"] = "0"

import torch
from datasets import Dataset
from peft import LoraConfig, get_peft_model
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    TrainingArguments,
    Trainer,
    DataCollatorForLanguageModeling,
)

BASE_MODEL = "Qwen/Qwen2.5-0.5B-Instruct"
DATASET = Path("/opt/pfe-ai-service/datasets/lora_instructions_v2.jsonl")
OUTPUT_DIR = Path("/opt/pfe-ai-service/model_artifacts/aiops-sre-lora-v2-cpu")

print(f"[1/6] Loading tokenizer and base model: {BASE_MODEL}")
tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL)
if tokenizer.pad_token is None:
    tokenizer.pad_token = tokenizer.eos_token

model = AutoModelForCausalLM.from_pretrained(
    BASE_MODEL,
    torch_dtype=torch.float32,
    device_map="cpu",
    low_cpu_mem_usage=True,
)
model.config.use_cache = False

print(f"[2/6] Attaching LoRA adapter (r=8, alpha=16)")
lora_config = LoraConfig(
    r=8,
    lora_alpha=16,
    lora_dropout=0.05,
    bias="none",
    target_modules=["q_proj", "v_proj"],
    task_type="CAUSAL_LM",
)
model = get_peft_model(model, lora_config)
model.print_trainable_parameters()

print(f"[3/6] Loading dataset {DATASET}")
records = []
with DATASET.open("r", encoding="utf-8") as f:
    for line in f:
        rec = json.loads(line)
        prompt = f"<|im_start|>user\n{rec['instruction']}<|im_end|>\n<|im_start|>assistant\n{rec['output']}<|im_end|>"
        records.append({"text": prompt})

ds = Dataset.from_list(records)
print(f"  -> {len(ds)} examples")

def tokenize_fn(batch):
    enc = tokenizer(
        batch["text"],
        truncation=True,
        max_length=256,
        padding=False,
    )
    enc["labels"] = enc["input_ids"].copy()
    return enc

ds = ds.map(tokenize_fn, batched=True, remove_columns=["text"])

print(f"[4/6] Setting up Trainer (3 epochs, batch=1, grad_accum=8, lr=2e-4)")
training_args = TrainingArguments(
    output_dir=str(OUTPUT_DIR),
    num_train_epochs=3,
    per_device_train_batch_size=1,
    gradient_accumulation_steps=8,
    learning_rate=2e-4,
    warmup_ratio=0.05,
    logging_steps=20,
    save_strategy="epoch",
    save_total_limit=2,
    fp16=False,
    bf16=False,
    dataloader_num_workers=0,
    report_to=[],
    optim="adamw_torch",
)

collator = DataCollatorForLanguageModeling(tokenizer=tokenizer, mlm=False)

trainer = Trainer(
    model=model,
    args=training_args,
    train_dataset=ds,
    data_collator=collator,
)

print(f"[5/6] Starting training...")
trainer.train()

print(f"[6/6] Saving adapter to {OUTPUT_DIR}")
trainer.save_model(str(OUTPUT_DIR))
tokenizer.save_pretrained(str(OUTPUT_DIR))
print("DONE")
