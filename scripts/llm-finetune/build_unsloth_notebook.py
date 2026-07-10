#!/usr/bin/env python3
"""Notebook FINAL via UNSLOTH (2-5x plus rapide que HF Trainer sur T4 gratuit).
QLoRA Qwen2.5-3B, rsLoRA, masquage de loss, export GGUF. -> HELIOS_finetune_FINAL.ipynb"""
import json, os
D=os.path.dirname(os.path.abspath(__file__))
def md(*l): return {"cell_type":"markdown","metadata":{},"source":[x+"\n" for x in l]}
def code(*l): return {"cell_type":"code","metadata":{},"execution_count":None,"outputs":[],"source":[x+"\n" for x in l]}

cells=[
md("# 🧠 Fine-tuning QLoRA (UNSLOTH) — Qwen2.5-3B HELIOS",
   "**Unsloth = 2-5× plus rapide** que le Trainer HF sur T4 gratuit. QLoRA rang 64 + rsLoRA + masquage de loss.",
   "**Avant** : Settings → **GPU T4 x2** + Internet On. Add Input → helios-llm-120.",
   "**Lance via Run → Run All.** (~20-40 min)"),

md("## 1. Installation Unsloth"),
code("!pip install -q unsloth"),

md("## 2. Charger Qwen2.5-3B (4-bit) + QLoRA (rsLoRA rang 64)"),
code('import os',
     'os.environ["CUDA_VISIBLE_DEVICES"] = "0"        # 1 seul GPU',
     'os.environ["TORCHDYNAMO_DISABLE"] = "1"         # evite le bug de compilation Triton (fused CE loss) sur Kaggle',
     'os.environ["UNSLOTH_COMPILE_DISABLE"] = "1"     # Unsloth en mode eager (stable)',
     "from unsloth import FastLanguageModel",
     "import torch",
     "model, tok = FastLanguageModel.from_pretrained(",
     '    model_name = "unsloth/Qwen2.5-3B-Instruct-bnb-4bit",',
     "    max_seq_length = 1024, dtype = None, load_in_4bit = True)",
     "model = FastLanguageModel.get_peft_model(",
     "    model, r = 64, lora_alpha = 128, lora_dropout = 0, bias = \"none\",",
     '    target_modules = ["q_proj","k_proj","v_proj","o_proj","gate_proj","up_proj","down_proj"],',
     '    use_gradient_checkpointing = "unsloth", use_rslora = True, random_state = 42)'),

md("## 3. Dataset + template Qwen (ChatML)"),
code("from unsloth.chat_templates import get_chat_template",
     'tok = get_chat_template(tok, chat_template = "qwen-2.5")',
     "import glob",
     "from datasets import load_dataset",
     "def find(name):",
     "    for p in [name, f'/kaggle/input/**/{name}', f'/content/{name}']:",
     "        g = glob.glob(p, recursive=True)",
     "        if g: return g[0]",
     "    raise FileNotFoundError(name)",
     'TR, VA = find("train.jsonl"), find("val.jsonl")',
     'print("train:", TR, "| val:", VA)',
     'ds = load_dataset("json", data_files={"train":TR, "val":VA})',
     "def fmt(ex):",
     '    ex["text"] = tok.apply_chat_template(ex["messages"], tokenize=False, add_generation_prompt=False)',
     "    return ex",
     'train_ds = ds["train"].map(fmt); val_ds = ds["val"].map(fmt)',
     'print("train", len(train_ds), "val", len(val_ds))'),

md("## 4. Trainer Unsloth + masquage de loss (réponse only)"),
code("from trl import SFTTrainer, SFTConfig",
     "from unsloth.chat_templates import train_on_responses_only",
     "trainer = SFTTrainer(",
     "    model = model, tokenizer = tok,",
     "    train_dataset = train_ds, eval_dataset = val_ds,",
     "    args = SFTConfig(",
     '        dataset_text_field = "text", max_seq_length = 1024, packing = False,',
     "        per_device_train_batch_size = 2, gradient_accumulation_steps = 4,",
     "        num_train_epochs = 2, learning_rate = 5e-5, warmup_ratio = 0.05, weight_decay = 0.01,",
     "        max_grad_norm = 1.0, logging_steps = 20, eval_strategy = \"steps\", eval_steps = 250,",
     '        lr_scheduler_type = "cosine", seed = 42, optim = "adamw_8bit",',
     '        output_dir = "out", report_to = "none"))',
     "# masquage : n'entraine que la reponse de l'assistant",
     "trainer = train_on_responses_only(trainer,",
     '    instruction_part = "<|im_start|>user\\n", response_part = "<|im_start|>assistant\\n")'),

md("## 5. 🚀 Entraîner (~20-40 min)"),
code("trainer.train()"),

md("## 6. Tester"),
code("FastLanguageModel.for_inference(model)",
     'msgs=[{"role":"system","content":"You are the automatic decision engine of a Kubernetes self-healing system. Reason from the symptoms and output: DIAGNOSTIC: <cause> | ACTION=<ACTION_NAME> | EXPLICATION: <how to fix>."},',
     '      {"role":"user","content":"Incident: payment-svc OOMKilled, memory grows steadily over hours. Decide the remediation action."}]',
     "inp=tok.apply_chat_template(msgs,tokenize=True,add_generation_prompt=True,return_tensors='pt').to('cuda')",
     "out=model.generate(input_ids=inp,max_new_tokens=180,temperature=0.3,do_sample=True)",
     "print(tok.decode(out[0][inp.shape[1]:],skip_special_tokens=True))"),

md("## 7. Export GGUF Q4 (pour Ollama)"),
code('model.save_pretrained_gguf("qwen3b-helios", tok, quantization_method="q4_k_m")',
     'print("-> qwen3b-helios/*.gguf pret pour Ollama (telecharge dans Output)")'),

md("## 8. Déployer sur .161 (Ollama)",
   "```bash",
   "cat > Modelfile <<'EOF'",
   "FROM ./qwen3b-helios.Q4_K_M.gguf",
   'SYSTEM \"You are the automatic decision engine of a Kubernetes self-healing system. Reason from the symptoms and output: DIAGNOSTIC: <cause> | ACTION=<ACTION_NAME> | EXPLICATION: <how to fix>.\"',
   "PARAMETER temperature 0.3",
   "PARAMETER num_ctx 1024",
   "EOF",
   "ollama create helios-120-3b -f Modelfile",
   "```"),
]
nb={"cells":cells,"metadata":{"kernelspec":{"display_name":"Python 3","name":"python3"},
    "language_info":{"name":"python"},"accelerator":"GPU"},"nbformat":4,"nbformat_minor":0}
out=os.path.join(D,"HELIOS_finetune_FINAL.ipynb")
json.dump(nb, open(out,"w",encoding="utf-8"), ensure_ascii=False, indent=1)
print("Notebook UNSLOTH ecrit ->", out, "|", len(cells), "cellules")
