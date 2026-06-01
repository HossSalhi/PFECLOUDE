"""
LoRA Fine-tuning for Qwen2.5-3B-Instruct on the AIOps incident dataset.

Uses standard HuggingFace PEFT + TRL (no unsloth dependency).
Compatible with: torch 2.5.1+cu121, transformers 4.44.x, peft 0.12.x, trl 0.9.x

Requirements (Python 3.12 venv):
    pip install torch==2.5.1+cu121 torchvision --index-url https://download.pytorch.org/whl/cu121
    pip install transformers==4.44.2 peft==0.12.0 trl==0.9.6 datasets bitsandbytes accelerate

Usage:
    python scripts/finetune_lora.py \\
        --dataset datasets/finetune_dataset.jsonl \\
        --output model_artifacts/aiops-sre-lora \\
        --epochs 3 --batch-size 1 --grad-accum 8 --max-seq-len 1024

Export to GGUF for Ollama (after training):
    python scripts/finetune_lora.py --export-gguf \\
        --lora-path model_artifacts/aiops-sre-lora \\
        --output model_artifacts/aiops-sre.gguf
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Force UTF-8 mode and unbuffered output — must be before any trl/transformers import
os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONUNBUFFERED", "1")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="LoRA fine-tune Qwen2.5-3B for AIOps")
    p.add_argument("--dataset", default="datasets/finetune_dataset.jsonl")
    p.add_argument("--output", default="model_artifacts/aiops-sre-lora")
    p.add_argument("--base-model", default="Qwen/Qwen2.5-3B-Instruct")
    p.add_argument("--epochs", type=int, default=3)
    p.add_argument("--batch-size", type=int, default=1)
    p.add_argument("--grad-accum", type=int, default=8)
    p.add_argument("--lr", type=float, default=2e-4)
    p.add_argument("--max-seq-len", type=int, default=1024)
    p.add_argument("--lora-r", type=int, default=16)
    p.add_argument("--lora-alpha", type=int, default=32)
    p.add_argument("--lora-dropout", type=float, default=0.05)
    p.add_argument("--load-in-4bit", action="store_true", default=True)
    p.add_argument("--export-gguf", action="store_true",
                   help="Export merged model to GGUF instead of training")
    p.add_argument("--lora-path", default="model_artifacts/aiops-sre-lora")
    p.add_argument("--seed", type=int, default=42)
    return p.parse_args()


def check_gpu() -> None:
    import torch
    if not torch.cuda.is_available():
        print("ERROR: CUDA not available. Install torch with CUDA:")
        print("  pip install torch==2.5.1+cu121 --index-url https://download.pytorch.org/whl/cu121")
        sys.exit(1)
    name = torch.cuda.get_device_name(0)
    vram = torch.cuda.get_device_properties(0).total_memory / 1e9
    print(f"GPU: {name} ({vram:.1f} GB VRAM)")
    if vram < 3.5:
        print("WARNING: Less than 3.5 GB VRAM — try --max-seq-len 512 if OOM")


def load_jsonl_dataset(path: str):
    from datasets import Dataset
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    print(f"Loaded {len(rows)} examples from {path}")
    return Dataset.from_list(rows)


def format_for_sft(example: dict, tokenizer) -> dict:
    """Apply chat template to messages list → single 'text' string for SFT."""
    messages = example.get("messages", [])
    text = tokenizer.apply_chat_template(
        messages,
        tokenize=False,
        add_generation_prompt=False,
    )
    return {"text": text}


def _patch_unsloth_fused_ce() -> None:
    """
    Monkey-patch unsloth_zoo's fused cross-entropy to use chunk_multiplier=1
    on GPUs with < 2 GB free VRAM, avoiding the RuntimeError on 4 GB cards.
    """
    try:
        import unsloth_zoo.fused_losses.cross_entropy_loss as _ce
        _original = _ce._get_chunk_multiplier

        def _safe_chunk_multiplier(*a, **kw):
            try:
                return _original(*a, **kw)
            except RuntimeError:
                return 1  # fallback: single chunk, no extra VRAM needed

        _ce._get_chunk_multiplier = _safe_chunk_multiplier
        print("Unsloth fused-CE patched for low-VRAM GPU.")
    except Exception as exc:
        print(f"Fused-CE patch skipped: {exc}")


def train(args: argparse.Namespace) -> None:
    import torch
    check_gpu()
    _patch_unsloth_fused_ce()

    from unsloth import FastLanguageModel
    from trl import SFTTrainer, SFTConfig

    print(f"\nLoading model + tokenizer via unsloth: {args.base_model}")
    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=args.base_model,
        max_seq_length=args.max_seq_len,
        dtype=None,
        load_in_4bit=args.load_in_4bit,
    )
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    print(f"Applying LoRA via unsloth (r={args.lora_r}, alpha={args.lora_alpha})...")
    model = FastLanguageModel.get_peft_model(
        model,
        r=args.lora_r,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                        "gate_proj", "up_proj", "down_proj"],
        lora_alpha=args.lora_alpha,
        lora_dropout=args.lora_dropout,
        bias="none",
        use_gradient_checkpointing="unsloth",
        random_state=args.seed,
    )
    model.print_trainable_parameters()

    print(f"\nLoading dataset: {args.dataset}")
    raw_ds = load_jsonl_dataset(args.dataset)
    dataset = raw_ds.map(
        lambda ex: format_for_sft(ex, tokenizer),
        remove_columns=raw_ds.column_names,
        desc="Formatting",
    )

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    # TRL >= 1.0 uses SFTConfig (subclass of TrainingArguments) — max_length / dataset_text_field live here
    sft_config = SFTConfig(
        output_dir=str(output_dir),
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size,
        gradient_accumulation_steps=args.grad_accum,
        learning_rate=args.lr,
        bf16=torch.cuda.is_bf16_supported(),
        fp16=not torch.cuda.is_bf16_supported(),
        logging_steps=10,
        save_steps=200,
        save_total_limit=2,
        warmup_ratio=0.05,
        lr_scheduler_type="cosine",
        seed=args.seed,
        report_to="none",
        optim="paged_adamw_8bit",
        dataloader_num_workers=0,
        remove_unused_columns=False,
        dataset_text_field="text",
        max_length=args.max_seq_len,
        packing=True,
        packing_strategy="bfd",
    )

    # TRL >= 1.0: processing_class replaces tokenizer parameter
    trainer = SFTTrainer(
        model=model,
        processing_class=tokenizer,
        train_dataset=dataset,
        args=sft_config,
    )

    print("\n=== Starting LoRA fine-tuning ===")
    print(f"  Dataset: {len(dataset)} examples")
    print(f"  Epochs: {args.epochs}")
    print(f"  Batch: {args.batch_size} × grad_accum {args.grad_accum} = effective batch {args.batch_size * args.grad_accum}")
    print(f"  Max seq len: {args.max_seq_len}")
    print(f"  Output: {output_dir}\n")

    trainer.train()

    model.save_pretrained(str(output_dir))
    tokenizer.save_pretrained(str(output_dir))
    print(f"\nLoRA adapter saved to: {output_dir}")
    print("Next step: python finetune_lora.py --export-gguf --lora-path " + str(output_dir))


def export_gguf(args: argparse.Namespace) -> None:
    """Merge LoRA weights via unsloth and export merged 16-bit model, then optionally GGUF."""
    import subprocess

    lora_path = Path(args.lora_path)
    output_path = Path(args.output)
    merged_dir = output_path.parent / (output_path.stem + "_merged")
    merged_dir.mkdir(parents=True, exist_ok=True)

    print(f"Loading model + LoRA adapter via unsloth from {lora_path}...")
    from unsloth import FastLanguageModel

    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=str(lora_path),
        max_seq_length=1024,
        dtype=None,
        load_in_4bit=True,
    )

    print(f"Merging LoRA weights and saving fp16 model to {merged_dir}...")
    model.save_pretrained_merged(str(merged_dir), tokenizer, save_method="merged_16bit")
    print(f"Merged fp16 model saved to: {merged_dir}")

    # Try GGUF conversion via llama.cpp if available
    convert_script = Path.home() / "llama.cpp" / "convert_hf_to_gguf.py"
    if not convert_script.exists():
        print(f"\nllama.cpp not found — skipping GGUF conversion.")
        print(f"Merged fp16 model is ready at: {merged_dir}")
        print("\nOptions to deploy:")
        print(f"\n1) Load directly with Ollama (no GGUF needed):")
        print(f"   ollama create aiops-sre -f - << 'EOF'")
        print(f"   FROM {merged_dir}")
        print(f"   PARAMETER temperature 0")
        print(f"   PARAMETER num_predict 400")
        print(f"   EOF")
        print(f"\n2) Convert to GGUF manually (install llama.cpp first):")
        print(f"   python ~/llama.cpp/convert_hf_to_gguf.py {merged_dir} --outfile {output_path} --outtype q4_k_m")
        print(f"\n3) Deploy merged model folder to VM and create Ollama model there:")
        print(f"   scp -r {merged_dir} ubuntu@192.168.148.161:/tmp/aiops-sre-merged")
        return

    print(f"Converting to GGUF (q4_k_m): {output_path}")
    result = subprocess.run(
        [sys.executable, str(convert_script), str(merged_dir),
         "--outfile", str(output_path), "--outtype", "q4_k_m"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print("GGUF conversion failed:")
        print(result.stderr)
        print(f"\nMerged fp16 model still available at: {merged_dir}")
        sys.exit(1)

    print(f"\nGGUF saved: {output_path}")
    print("\nDeploy to Ollama VM (192.168.148.161):")
    print(f"  scp {output_path} ubuntu@192.168.148.161:/tmp/aiops-sre.gguf")
    print("  ssh ubuntu@192.168.148.161")
    print("  cat > /tmp/Modelfile << 'EOF'")
    print("  FROM /tmp/aiops-sre.gguf")
    print("  PARAMETER temperature 0")
    print("  PARAMETER num_predict 400")
    print("  EOF")
    print("  ollama create aiops-sre -f /tmp/Modelfile")


def main() -> None:
    args = parse_args()
    if args.export_gguf:
        export_gguf(args)
    else:
        train(args)


if __name__ == "__main__":
    main()
