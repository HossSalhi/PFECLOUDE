"""LoRA Qwen 0.5B v3 — GPU RTX 3050, 1124 examples (synth+real), r=16, fixed gradients."""
import json, os
from pathlib import Path

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("HF_HOME", str(Path.home() / ".cache" / "huggingface"))
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["HF_DATASETS_OFFLINE"] = "1"

import torch
from datasets import Dataset
from peft import LoraConfig, get_peft_model
from transformers import (
    AutoModelForCausalLM, AutoTokenizer,
    TrainingArguments, Trainer,
    DataCollatorForLanguageModeling,
)

BASE_MODEL = "c:/Users/houss/IdeaProjects/PFE-Orange/model_base/Qwen2.5-0.5B-Instruct"
DATASET = Path("c:/Users/houss/IdeaProjects/PFE-Orange/datasets/lora_v3_real.jsonl")
OUTPUT_DIR = Path("c:/Users/houss/IdeaProjects/PFE-Orange/model_artifacts/aiops-sre-lora-v3-gpu")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
print(f"Device: {DEVICE}", flush=True)
if DEVICE == "cuda":
    print(f"GPU: {torch.cuda.get_device_name(0)}", flush=True)
    free, total = torch.cuda.mem_get_info(0)
    print(f"VRAM: {free/1e9:.1f}GB free / {total/1e9:.1f}GB total", flush=True)

print("[1/6] Loading tokenizer + base model...", flush=True)
tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL, local_files_only=True)
if tokenizer.pad_token is None:
    tokenizer.pad_token = tokenizer.eos_token

model = AutoModelForCausalLM.from_pretrained(
    BASE_MODEL,
    torch_dtype=torch.float16,
    device_map=DEVICE,
    local_files_only=True,
)
model.config.use_cache = False
print("  Model loaded.", flush=True)

print("[2/6] LoRA r=16 alpha=32...", flush=True)
lora_config = LoraConfig(
    r=16, lora_alpha=32, lora_dropout=0.05, bias="none",
    target_modules=["q_proj", "k_proj", "v_proj", "o_proj"],
    task_type="CAUSAL_LM",
)
model = get_peft_model(model, lora_config)
model.print_trainable_parameters()

print("[3/6] Loading dataset...", flush=True)
records = []
with DATASET.open("r", encoding="utf-8") as f:
    for line in f:
        rec = json.loads(line)
        instruction = rec.get("instruction", "")
        output = rec.get("output", "")
        prompt = f"<|im_start|>user\n{instruction}<|im_end|>\n<|im_start|>assistant\n{output}<|im_end|>"
        records.append({"text": prompt})

print(f"  examples: {len(records)}", flush=True)
ds = Dataset.from_list(records)

PAD_ID = tokenizer.pad_token_id

def tok_fn(batch):
    enc = tokenizer(batch["text"], truncation=True, max_length=256, padding="max_length")
    labels = []
    for ids in enc["input_ids"]:
        # mask padding tokens so loss ignores them
        lbl = [t if t != PAD_ID else -100 for t in ids]
        labels.append(lbl)
    enc["labels"] = labels
    return enc

ds = ds.map(tok_fn, batched=True, remove_columns=["text"])
print(f"  tokenized: {len(ds)} examples", flush=True)

print("[4/6] Training args (fp16, no gradient_checkpointing)...", flush=True)
args = TrainingArguments(
    output_dir=str(OUTPUT_DIR),
    num_train_epochs=5,
    per_device_train_batch_size=2,
    gradient_accumulation_steps=8,
    learning_rate=2e-4,
    warmup_ratio=0.05,
    logging_steps=20,
    save_strategy="epoch",
    save_total_limit=2,
    fp16=True,
    bf16=False,
    gradient_checkpointing=False,
    dataloader_num_workers=0,
    report_to=[],
    optim="adamw_torch",
)
collator = DataCollatorForLanguageModeling(tokenizer=tokenizer, mlm=False, pad_to_multiple_of=8)
trainer = Trainer(model=model, args=args, train_dataset=ds, data_collator=collator)

print("[5/6] Training...", flush=True)
trainer.train()

print(f"[6/6] Saving to {OUTPUT_DIR}", flush=True)
trainer.save_model(str(OUTPUT_DIR))
tokenizer.save_pretrained(str(OUTPUT_DIR))
print("DONE v3-gpu")
