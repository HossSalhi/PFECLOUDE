#!/usr/bin/env python3
"""Fine-tuning LoRA du LLM HELIOS sur le corpus des 120 incidents (+ données publiques K8s).

⚠️ À LANCER SUR UNE MACHINE AVEC GPU (Colab T4/A100, ou serveur CUDA). .161 n'a PAS de GPU
   (mesuré : 3 cœurs CPU, ~2.6 Go RAM libre, 4.2 Go disque libre) -> infaisable en local.

Entrée  : corpus_120_sft.jsonl (format chat) — généré par build_sft_corpus.py + fetch_public_datasets.py
Base    : Qwen2.5-0.5B-Instruct (même famille que l'actuel aiops-sre-ft 0.5B d'Ollama sur .161)
Sortie  : adaptateur LoRA -> fusion -> export GGUF -> `ollama create aiops-sre-ft-v3`

Prérequis (GPU) : pip install torch transformers peft trl datasets accelerate bitsandbytes
"""
import json, os
from datasets import load_dataset
from transformers import AutoModelForCausalLM, AutoTokenizer, TrainingArguments
from peft import LoraConfig
from trl import SFTTrainer

HERE = os.path.dirname(os.path.abspath(__file__))
CORPUS = os.path.join(HERE, "corpus_120_sft.jsonl")
BASE = "Qwen/Qwen2.5-0.5B-Instruct"
OUT = os.path.join(HERE, "aiops-sre-ft-v3-lora")

def main():
    tok = AutoTokenizer.from_pretrained(BASE)
    ds = load_dataset("json", data_files=CORPUS, split="train")

    def fmt(ex):
        # applique le template de chat du modèle sur la liste de messages
        return {"text": tok.apply_chat_template(ex["messages"], tokenize=False,
                                                add_generation_prompt=False)}
    ds = ds.map(fmt, remove_columns=ds.column_names)

    model = AutoModelForCausalLM.from_pretrained(BASE, device_map="auto", load_in_4bit=True)
    lora = LoraConfig(r=16, lora_alpha=32, lora_dropout=0.05, bias="none",
                      task_type="CAUSAL_LM",
                      target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                                      "gate_proj", "up_proj", "down_proj"])
    args = TrainingArguments(
        output_dir=OUT, num_train_epochs=3, per_device_train_batch_size=4,
        gradient_accumulation_steps=4, learning_rate=2e-4, warmup_ratio=0.03,
        logging_steps=10, save_strategy="epoch", bf16=True, optim="paged_adamw_8bit",
        lr_scheduler_type="cosine", report_to="none")
    trainer = SFTTrainer(model=model, args=args, train_dataset=ds,
                         peft_config=lora, dataset_text_field="text", max_seq_length=1024)
    trainer.train()
    trainer.save_model(OUT)
    print(f"LoRA sauvegardé -> {OUT}")
    print("Étapes suivantes : fusionner (merge_and_unload) -> convertir GGUF (llama.cpp) -> "
          "`ollama create aiops-sre-ft-v3 -f Modelfile` -> déployer sur .161")

if __name__ == "__main__":
    main()
