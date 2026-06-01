"""
Convert the bitsandbytes-4bit merged model to fp16 safetensors.
Uses progressive sharding so peak RAM stays ~3 GB instead of 6 GB.
No internet / HuggingFace download required.

Usage:
    python scripts/dequantize_to_fp16.py
"""
from __future__ import annotations

import gc
import json
import sys
import os
from pathlib import Path

os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONUNBUFFERED", "1")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)

SRC = Path("model_artifacts/aiops-sre_merged")
DST = Path("model_artifacts/aiops-sre_fp16")
MAX_SHARD_BYTES = 3 * 1024 ** 3  # 3 GB per shard

import torch
import bitsandbytes as bnb
from bitsandbytes.functional import dequantize_4bit
from safetensors.torch import save_file
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig


def _get_fp16_param(name: str, module, param_name: str) -> tuple[str, torch.Tensor] | None:
    """Return (full_key, fp16_cpu_tensor) for a single parameter."""
    key = f"{name}.{param_name}" if name else param_name
    param = getattr(module, param_name, None)
    if param is None:
        return None
    if isinstance(module, bnb.nn.Linear4bit) and param_name == "weight":
        fp16 = dequantize_4bit(param.data, param.quant_state).to(torch.float16)
        return key, fp16.cpu()
    if isinstance(param, torch.nn.Parameter):
        return key, param.data.to(torch.float16).cpu()
    return None


def save_shard(shard: dict[str, torch.Tensor], shard_idx: int, dst: Path) -> str:
    fname = f"model-part-{shard_idx:05d}.safetensors"
    save_file(shard, str(dst / fname))
    size_mb = sum(t.nelement() * t.element_size() for t in shard.values()) / 1e6
    print(f"  Saved shard {shard_idx}: {fname}  ({size_mb:.0f} MB, {len(shard)} tensors)")
    return fname


def main() -> None:
    DST.mkdir(parents=True, exist_ok=True)

    print(f"Loading 4-bit model from {SRC} ...")
    bnb_config = BitsAndBytesConfig(load_in_4bit=True)
    model = AutoModelForCausalLM.from_pretrained(
        str(SRC),
        quantization_config=bnb_config,
        device_map="cuda",
        dtype=torch.float16,
    )
    tokenizer = AutoTokenizer.from_pretrained(str(SRC))

    print("Dequantizing and sharding to fp16 (progressive, ~3 GB per shard)...")

    shard: dict[str, torch.Tensor] = {}
    shard_bytes = 0
    shard_idx = 1
    shard_files: list[str] = []
    weight_map: dict[str, str] = {}
    processed = 0
    current_shard_name = f"model-part-{shard_idx:05d}.safetensors"

    visited_modules: set[str] = set()

    for full_name, module in model.named_modules():
        # Only leaf modules with direct parameters
        has_params = any(
            isinstance(p, torch.nn.Parameter)
            for p in module.parameters(recurse=False)
        )
        if not has_params:
            continue
        if full_name in visited_modules:
            continue
        visited_modules.add(full_name)

        for pname in list(module._parameters.keys()):
            result = _get_fp16_param(full_name, module, pname)
            if result is None:
                continue
            key, tensor = result
            nbytes = tensor.nelement() * tensor.element_size()

            # Flush current shard if it would overflow
            if shard and shard_bytes + nbytes > MAX_SHARD_BYTES:
                fname = save_shard(shard, shard_idx, DST)
                shard_files.append(fname)
                shard = {}
                shard_bytes = 0
                shard_idx += 1
                current_shard_name = f"model-part-{shard_idx:05d}.safetensors"
                gc.collect()
                torch.cuda.empty_cache()

            shard[key] = tensor
            shard_bytes += nbytes
            weight_map[key] = current_shard_name
            processed += 1

        # Clear GPU after each module
        torch.cuda.empty_cache()

    # Save final shard
    if shard:
        fname = save_shard(shard, shard_idx, DST)
        shard_files.append(fname)
        del shard
        gc.collect()

    total_shards = len(shard_files)
    print(f"\nSaved {total_shards} shard(s), {processed} tensors total.")

    # Rename shards with correct total count in filename
    final_weight_map: dict[str, str] = {}
    for i, old_name in enumerate(shard_files):
        new_name = f"model-{i+1:05d}-of-{total_shards:05d}.safetensors"
        os.rename(DST / old_name, DST / new_name)
        # Update weight map entries that pointed to old_name
        for k, v in weight_map.items():
            if v == old_name:
                final_weight_map[k] = new_name

    # Write safetensors index
    index = {"metadata": {"total_size": sum(
        (DST / f).stat().st_size for f in [
            f"model-{i+1:05d}-of-{total_shards:05d}.safetensors"
            for i in range(total_shards)
        ]
    )}, "weight_map": final_weight_map}
    (DST / "model.safetensors.index.json").write_text(
        json.dumps(index, indent=2), encoding="utf-8"
    )

    # Save config + tokenizer
    del model
    gc.collect()
    torch.cuda.empty_cache()

    tokenizer.save_pretrained(str(DST))
    # Copy config files from source
    for cfg_file in ["config.json", "generation_config.json",
                     "special_tokens_map.json", "tokenizer_config.json"]:
        src_f = SRC / cfg_file
        if src_f.exists():
            import shutil
            shutil.copy2(src_f, DST / cfg_file)

    total_gb = sum(f.stat().st_size for f in DST.rglob("*") if f.is_file()) / 1e9
    print(f"\nDone!  fp16 model at {DST}  ({total_gb:.1f} GB)")
    print("\nDeploy to Ollama VM (192.168.148.161):")
    print(f"  scp -r {DST} ubuntu@192.168.148.161:/opt/aiops-sre-fp16")
    print(f"  ssh ubuntu@192.168.148.161 'cat > /tmp/Mf <<EOF")
    print(f"  FROM /opt/aiops-sre-fp16")
    print(f"  PARAMETER temperature 0")
    print(f"  PARAMETER num_predict 400")
    print(f"  EOF")
    print(f"  ollama create aiops-sre -f /tmp/Mf'")


if __name__ == "__main__":
    main()
