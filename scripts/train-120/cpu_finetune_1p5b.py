#!/usr/bin/env python3
"""Fine-tune de POIDS du LLM HELIOS sur les 120 — Qwen2.5-1.5B, sur CPU (pas de limite VRAM).

Le GPU RTX 3050 (4 Go) ne tient pas le 1.5B au chargement ; le CPU n'a pas cette limite (RAM hote).
Prérequis : avoir libéré de la RAM (VMs non essentielles éteintes). ~5-8 Go RAM, lent (~2-4h).
Pas de bitsandbytes (4-bit = GPU-only) : base en bf16 + LoRA (adaptateurs en fp32).

Entrée : corpus_120_clean.jsonl (600 ex NET, format ACTION=)
Sortie : aiops-sre-120-ft15-merged/ -> GGUF -> Ollama .161 (aiops-sre-120-ft15)
"""
import os, torch
os.environ.setdefault("OMP_NUM_THREADS", str(max(1, (os.cpu_count() or 4) - 1)))
from datasets import load_dataset
from transformers import (AutoModelForCausalLM, AutoTokenizer, TrainingArguments,
                          Trainer, DataCollatorForLanguageModeling)
from peft import LoraConfig, get_peft_model

HERE = os.path.dirname(os.path.abspath(__file__))
CORPUS = os.path.join(HERE, "corpus_120_clean.jsonl")
BASE = os.environ.get("FT_BASE", "Qwen/Qwen2.5-1.5B-Instruct")
OUT_LORA = os.path.join(HERE, "aiops-sre-120-ft15-lora")
OUT_MERGED = os.path.join(HERE, "aiops-sre-120-ft15-merged")
MAXLEN = int(os.environ.get("FT_MAXLEN", "320"))

def main():
    print("CPU threads:", os.environ.get("OMP_NUM_THREADS"), "| base:", BASE, "| corpus:", CORPUS)
    tok = AutoTokenizer.from_pretrained(BASE)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    ds = load_dataset("json", data_files=CORPUS, split="train")
    ds = ds.map(lambda ex: {"text": tok.apply_chat_template(ex["messages"], tokenize=False)},
                remove_columns=ds.column_names)
    def tok_fn(ex):
        out = tok(ex["text"], truncation=True, max_length=MAXLEN)
        out["labels"] = out["input_ids"].copy()
        return out
    ds = ds.map(tok_fn, remove_columns=["text"])

    # base bf16 sur CPU (pas de quantification), LoRA en fp32
    model = AutoModelForCausalLM.from_pretrained(BASE, torch_dtype=torch.bfloat16, low_cpu_mem_usage=True)
    model.config.use_cache = False
    model.gradient_checkpointing_enable()
    model.enable_input_require_grads()
    lora = LoraConfig(r=16, lora_alpha=32, lora_dropout=0.05, bias="none", task_type="CAUSAL_LM",
                      target_modules=["q_proj","k_proj","v_proj","o_proj","gate_proj","up_proj","down_proj"])
    model = get_peft_model(model, lora)
    model.print_trainable_parameters()

    collator = DataCollatorForLanguageModeling(tok, mlm=False)
    args = TrainingArguments(
        output_dir=OUT_LORA, num_train_epochs=3, per_device_train_batch_size=1,
        gradient_accumulation_steps=16, learning_rate=2e-4, warmup_ratio=0.03,
        logging_steps=5, save_strategy="epoch", lr_scheduler_type="cosine",
        report_to="none", fp16=False, bf16=False, use_cpu=True, dataloader_num_workers=0)
    Trainer(model=model, args=args, train_dataset=ds, data_collator=collator).train()
    model.save_pretrained(OUT_LORA)
    print("LoRA sauvegardé ->", OUT_LORA)

    # fusion (recharge base fp16 CPU + applique l'adaptateur)
    del model
    from peft import PeftModel
    base = AutoModelForCausalLM.from_pretrained(BASE, torch_dtype=torch.float16, low_cpu_mem_usage=True)
    merged = PeftModel.from_pretrained(base, OUT_LORA).merge_and_unload()
    merged.save_pretrained(OUT_MERGED, safe_serialization=True)
    tok.save_pretrained(OUT_MERGED)
    print("MODELE FUSIONNE ->", OUT_MERGED)

if __name__ == "__main__":
    main()
