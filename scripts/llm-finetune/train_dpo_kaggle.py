# -*- coding: utf-8 -*-
"""HELIOS — Fine-tune DPO (preference tuning) sur Kaggle/Colab GPU.
But : corriger la régression du SFT (actions vagues « investigate ») en apprenant au modèle à
PRÉFÉRER l'action catalogue précise/structurée. Base = Qwen2.5-3B (le meilleur en base+RAG).

Prérequis : uploader dpo_120.jsonl (généré par gen_dpo_dataset.py) comme dataset Kaggle.
GPU requis (QLoRA 4-bit). Après : exporter GGUF Q4 -> Ollama `helios-120-dpo` sur .161.

NOTE : depuis le DÉCODAGE CONTRAINT (action verrouillée à l'enum catalogue), le DPO est
OPTIONNEL — il améliore la qualité intrinsèque du modèle (utile s'il tourne sans contrainte)."""

# ── 1. deps (Kaggle) ─────────────────────────────────────────────────────────
# !pip install -q unsloth "trl>=0.11" datasets

from unsloth import FastLanguageModel
import torch, json
from datasets import load_dataset
from trl import DPOConfig, DPOTrainer

MAX_LEN = 1024
model, tok = FastLanguageModel.from_pretrained(
    "unsloth/Qwen2.5-3B-Instruct", max_seq_length=MAX_LEN,
    dtype=None, load_in_4bit=True)
model = FastLanguageModel.get_peft_model(
    model, r=32, lora_alpha=32, lora_dropout=0.0, bias="none",
    target_modules=["q_proj","k_proj","v_proj","o_proj","gate_proj","up_proj","down_proj"],
    use_gradient_checkpointing="unsloth", random_state=42, use_rslora=True)

# ── 2. dataset DPO : applique le chat template au prompt (system+user) ────────
ds = load_dataset("json", data_files="dpo_120.jsonl", split="train")
def fmt(ex):
    prompt = tok.apply_chat_template(ex["prompt"], tokenize=False, add_generation_prompt=True)
    return {"prompt": prompt, "chosen": ex["chosen"] + tok.eos_token,
            "rejected": ex["rejected"] + tok.eos_token}
ds = ds.map(fmt, remove_columns=ds.column_names).train_test_split(test_size=0.05, seed=42)

# ── 3. DPO (LR bas + beta 0.1 ; anti-divergence comme le SFT : grad clip 1.0) ─
cfg = DPOConfig(
    per_device_train_batch_size=2, gradient_accumulation_steps=4,
    warmup_ratio=0.05, num_train_epochs=1, learning_rate=5e-6,   # DPO = LR bas
    beta=0.1, max_length=MAX_LEN, max_prompt_length=640,
    logging_steps=10, optim="adamw_8bit", weight_decay=0.0,
    lr_scheduler_type="cosine", max_grad_norm=1.0, seed=42,
    eval_strategy="steps", eval_steps=100, output_dir="dpo_out", report_to="none")
trainer = DPOTrainer(model=model, ref_model=None, args=cfg,
    train_dataset=ds["train"], eval_dataset=ds["test"], processing_class=tok)
stats = trainer.train()
print("train_loss:", stats.training_loss)

# ── 4. sauvegarde + export GGUF Q4 (pour Ollama .161) ────────────────────────
model.save_pretrained("helios_dpo_lora"); tok.save_pretrained("helios_dpo_lora")
model.save_pretrained_gguf("helios-120-dpo", tok, quantization_method="q4_k_m")
print("GGUF prêt -> déployer via scripts/llm-finetune/deploy_helios_120_gguf.py (Ollama helios-120-dpo)")
