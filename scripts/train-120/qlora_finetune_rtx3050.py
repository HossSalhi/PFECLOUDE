#!/usr/bin/env python3
"""Fine-tune de POIDS (QLoRA 4-bit) du LLM HELIOS sur les 120 incidents — GPU RTX 3050 4 Go.

Le vrai fine-tune de poids (au-dessus du few-shot) tourne ENFIN en local sur la RTX 3050.
QLoRA 4-bit (bitsandbytes) fait tenir un 1.5B dans 4 Go de VRAM.

Entrée : corpus_120_sft.jsonl (format chat, 680 ex des 120)
Base   : Qwen/Qwen2.5-1.5B-Instruct (repli 0.5B si OOM)
Sortie : aiops-sre-120-ft-merged/ (modèle fusionné) -> à convertir en GGUF -> Ollama .161

Réglages 4 Go : 4-bit nf4, batch 1, grad_accum 16, gradient_checkpointing, max_seq 640.
"""
import os, sys, torch
from datasets import load_dataset
from transformers import (AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig,
                          TrainingArguments)
from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training

HERE = os.path.dirname(os.path.abspath(__file__))
CORPUS = os.path.join(HERE, "corpus_120_sft.jsonl")
BASE = os.environ.get("FT_BASE", "Qwen/Qwen2.5-1.5B-Instruct")
OUT_LORA = os.path.join(HERE, "aiops-sre-120-ft-lora")
OUT_MERGED = os.path.join(HERE, "aiops-sre-120-ft-merged")
MAXLEN = int(os.environ.get("FT_MAXLEN", "640"))

def main():
    assert torch.cuda.is_available(), "CUDA indisponible"
    print("GPU:", torch.cuda.get_device_name(0), "| VRAM libre(GB):",
          round(torch.cuda.mem_get_info()[0]/1e9, 2), "| base:", BASE)

    tok = AutoTokenizer.from_pretrained(BASE)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token

    ds = load_dataset("json", data_files=CORPUS, split="train")
    def to_text(ex):
        return {"text": tok.apply_chat_template(ex["messages"], tokenize=False,
                                                add_generation_prompt=False)}
    ds = ds.map(to_text, remove_columns=ds.column_names)

    def tokenize(ex):
        out = tok(ex["text"], truncation=True, max_length=MAXLEN, padding="max_length")
        out["labels"] = out["input_ids"].copy()
        return out
    ds = ds.map(tokenize, remove_columns=["text"])

    bnb = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                             bnb_4bit_compute_dtype=torch.float16,
                             bnb_4bit_use_double_quant=True)
    model = AutoModelForCausalLM.from_pretrained(BASE, quantization_config=bnb, device_map={"": 0})
    model = prepare_model_for_kbit_training(model, use_gradient_checkpointing=True)
    model.config.use_cache = False
    lora = LoraConfig(r=16, lora_alpha=32, lora_dropout=0.05, bias="none", task_type="CAUSAL_LM",
                      target_modules=["q_proj","k_proj","v_proj","o_proj","gate_proj","up_proj","down_proj"])
    model = get_peft_model(model, lora)
    model.print_trainable_parameters()

    from transformers import DataCollatorForLanguageModeling
    collator = DataCollatorForLanguageModeling(tok, mlm=False)
    args = TrainingArguments(
        output_dir=OUT_LORA, num_train_epochs=3, per_device_train_batch_size=1,
        gradient_accumulation_steps=16, learning_rate=2e-4, warmup_ratio=0.03,
        logging_steps=10, save_strategy="epoch", fp16=True, optim="paged_adamw_8bit",
        lr_scheduler_type="cosine", report_to="none", gradient_checkpointing=True)
    from transformers import Trainer
    trainer = Trainer(model=model, args=args, train_dataset=ds, data_collator=collator)
    trainer.train()
    trainer.save_model(OUT_LORA)
    print("LoRA sauvegardé ->", OUT_LORA)

    # fusion pour export GGUF (recharge la base en fp16 + applique l'adaptateur)
    del model, trainer
    torch.cuda.empty_cache()
    from peft import PeftModel
    base = AutoModelForCausalLM.from_pretrained(BASE, torch_dtype=torch.float16, device_map={"": 0})
    merged = PeftModel.from_pretrained(base, OUT_LORA).merge_and_unload()
    merged.save_pretrained(OUT_MERGED, safe_serialization=True)
    tok.save_pretrained(OUT_MERGED)
    print("MODÈLE FUSIONNÉ ->", OUT_MERGED)
    print("Étape suivante : convertir en GGUF (llama.cpp) -> scp .161 -> ollama create aiops-sre-120-ft")

if __name__ == "__main__":
    main()
