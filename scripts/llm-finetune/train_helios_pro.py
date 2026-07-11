# -*- coding: utf-8 -*-
"""HELIOS — PIPELINE PRO : UN seul modèle spécialiste fiable (SFT-propre -> DPO).
Corrige la régression du 1er fine-tune (qui a utilisé le dataset REASONED verbeux -> actions vagues).

RECETTE PRO :
  Étape 1  SFT sur sft_120.jsonl (CRISP, 100% ACTION=)  -> apprend le format net + les 120 actions
  Étape 2  DPO sur dpo_120.jsonl (chosen=net / rejected=vague) -> renforce la préférence action précise
  Éval     action nommée sur held-out  -> ne déployer QUE si ça bat qwen+RAG (0.93)
  Export   GGUF Q4 -> Ollama helios-120-pro sur .161

MATÉRIEL : GPU requis (Kaggle T4 gratuit, ~1-2h). Uploader sft_120.jsonl + dpo_120.jsonl comme dataset Kaggle.
Config anti-régression PROUVÉE : Qwen2.5-3B, QLoRA r64 rslora, LR SFT 5e-5 / DPO 5e-6, max_grad_norm 1.0.
"""
# === 0. deps (Kaggle) ========================================================
# !pip install -q unsloth "trl>=0.11" datasets

from unsloth import FastLanguageModel
import torch, json, re
from datasets import load_dataset
from trl import SFTTrainer, SFTConfig, DPOConfig, DPOTrainer

BASE = "unsloth/Qwen2.5-3B-Instruct"
MAXLEN = 1024
SFT_FILE = "sft_120.jsonl"      # CRISP (pas le reasoned !)
DPO_FILE = "dpo_120.jsonl"

model, tok = FastLanguageModel.from_pretrained(BASE, max_seq_length=MAXLEN, dtype=None, load_in_4bit=True)
model = FastLanguageModel.get_peft_model(
    model, r=64, lora_alpha=128, lora_dropout=0.0, bias="none",
    target_modules=["q_proj","k_proj","v_proj","o_proj","gate_proj","up_proj","down_proj"],
    use_gradient_checkpointing="unsloth", random_state=42, use_rslora=True)

# === 1. ÉTAPE SFT — données PROPRES (crisp, format net) ======================
sft_ds = load_dataset("json", data_files=SFT_FILE, split="train")
# petit held-out pour l'éval (5%)
sft_ds = sft_ds.train_test_split(test_size=0.05, seed=42)
def to_text(ex): return {"text": tok.apply_chat_template(ex["messages"], tokenize=False)}
sft_train = sft_ds["train"].map(to_text, remove_columns=sft_ds["train"].column_names)

print(">>> ÉTAPE 1/2 : SFT propre")
SFTTrainer(
    model=model, tokenizer=tok, train_dataset=sft_train,
    args=SFTConfig(
        per_device_train_batch_size=2, gradient_accumulation_steps=4, num_train_epochs=2,
        learning_rate=5e-5, max_grad_norm=1.0, lr_scheduler_type="cosine", warmup_ratio=0.05,
        optim="adamw_8bit", weight_decay=0.01, max_seq_length=MAXLEN,
        logging_steps=20, seed=42, dataset_text_field="text",
        output_dir="sft_out", report_to="none"),
).train()

# === 2. ÉTAPE DPO — préférence action NETTE vs prose VAGUE ===================
def dpo_fmt(ex):
    prompt = tok.apply_chat_template(ex["prompt"], tokenize=False, add_generation_prompt=True)
    return {"prompt": prompt, "chosen": ex["chosen"] + tok.eos_token, "rejected": ex["rejected"] + tok.eos_token}
dpo_ds = load_dataset("json", data_files=DPO_FILE, split="train")
dpo_ds = dpo_ds.map(dpo_fmt, remove_columns=dpo_ds.column_names)

print(">>> ÉTAPE 2/2 : DPO (préférence)")
DPOTrainer(
    model=model, ref_model=None,
    args=DPOConfig(
        per_device_train_batch_size=2, gradient_accumulation_steps=4, num_train_epochs=1,
        learning_rate=5e-6, beta=0.1, max_length=MAXLEN, max_prompt_length=640,
        max_grad_norm=1.0, lr_scheduler_type="cosine", warmup_ratio=0.05, optim="adamw_8bit",
        weight_decay=0.0, seed=42, logging_steps=10, output_dir="dpo_out", report_to="none"),
    train_dataset=dpo_ds, processing_class=tok,
).train()

# === 3. ÉVAL objective — action nommée sur le held-out SFT ===================
print(">>> ÉVAL held-out (action nommée)")
FastLanguageModel.for_inference(model)
ok = tot = 0
for ex in sft_ds["test"].select(range(min(200, len(sft_ds["test"])))):
    msgs = ex["messages"]
    gold = re.search(r"ACTION=([A-Z_]+)", msgs[-1]["content"])
    gold = gold.group(1) if gold else None
    prompt = tok.apply_chat_template(msgs[:-1], tokenize=False, add_generation_prompt=True)
    out = tok.batch_decode(model.generate(**tok(prompt, return_tensors="pt").to(model.device),
        max_new_tokens=64, temperature=0.1, do_sample=False))[0]
    pred = re.search(r"ACTION=([A-Z_]+)", out.split(prompt)[-1] if prompt in out else out)
    tot += 1
    if gold and pred and pred.group(1) == gold: ok += 1
print(f"ACTION NOMMÉE held-out : {ok}/{tot} = {ok/max(1,tot):.3f}  (objectif : battre qwen+RAG 0.93)")

# === 4. EXPORT GGUF Q4 -> déployer sur .161 si l'éval est bonne =============
model.save_pretrained("helios_pro_lora"); tok.save_pretrained("helios_pro_lora")
model.save_pretrained_gguf("helios-120-pro", tok, quantization_method="q4_k_m")
print("GGUF prêt : helios-120-pro.  Déploiement -> scripts/deploy_helios_pro.py sur .161 (si éval > 0.93).")
