"""
Correct fp16 dequantization: loads the original LoRA adapter (not the merged model),
merges LoRA in-memory (quant_state is intact), then dequantizes each Linear4bit layer
to fp16 and saves as sharded safetensors.

Usage:
    python scripts/dequantize_from_lora.py
"""
from __future__ import annotations
import gc, json, os, sys, shutil
from pathlib import Path

os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONUNBUFFERED", "1")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)

LORA_PATH   = Path("model_artifacts/aiops-sre-lora-full")
DST         = Path("model_artifacts/aiops-sre_fp16")
MAX_SHARD   = int(1.5 * 1024 ** 3)   # 1.5 GB per shard — fits in 2.3 GB free RAM

import torch
import bitsandbytes as bnb
from bitsandbytes.functional import dequantize_4bit
from safetensors.torch import save_file
from unsloth import FastLanguageModel


def _patch_fused_ce():
    try:
        import unsloth_zoo.fused_losses.cross_entropy_loss as _ce
        orig = _ce._get_chunk_multiplier
        def _safe(*a, **kw):
            try:
                return orig(*a, **kw)
            except RuntimeError:
                return 1
        _ce._get_chunk_multiplier = _safe
    except Exception:
        pass


def get_fp16_tensor(name: str, module, pname: str):
    """Return (key, fp16_cpu_tensor) or None."""
    key = f"{name}.{pname}" if name else pname
    param = getattr(module, pname, None)
    if param is None:
        return None

    if isinstance(module, bnb.nn.Linear4bit) and pname == "weight":
        qs = getattr(param, "quant_state", None)
        if qs is None:
            print(f"  WARNING: quant_state is None for {key}, skipping dequantize")
            return None
        fp16 = dequantize_4bit(param.data, qs).to(torch.float16)
        return key, fp16.cpu()

    if isinstance(param, torch.nn.Parameter):
        return key, param.data.to(torch.float16).cpu()
    return None


def save_shard(shard: dict, idx: int, dst: Path) -> str:
    fname = f"model-part-{idx:05d}.safetensors"
    save_file(shard, str(dst / fname))
    size_mb = sum(t.nelement() * t.element_size() for t in shard.values()) / 1e6
    print(f"  Saved shard {idx}: {len(shard)} tensors, {size_mb:.0f} MB")
    return fname


def main():
    _patch_fused_ce()
    DST.mkdir(parents=True, exist_ok=True)

    print(f"Loading LoRA model from {LORA_PATH} (quant_state will be intact)...")
    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=str(LORA_PATH),
        max_seq_length=1024,
        dtype=None,
        load_in_4bit=True,
    )

    # Verify quant_state is available on first layer
    for n, m in model.named_modules():
        if isinstance(m, bnb.nn.Linear4bit):
            qs = getattr(m.weight, "quant_state", None)
            print(f"  quant_state check on {n}.weight: {qs is not None}")
            print(f"  weight.data shape: {m.weight.data.shape}")
            break

    print("\nDequantizing and sharding (progressive, 3 GB/shard)...")
    shard: dict = {}
    shard_bytes = 0
    shard_idx = 1
    shard_files: list = []
    weight_map: dict = {}
    current_fname = f"model-part-{shard_idx:05d}.safetensors"
    visited: set = set()
    processed = 0

    for full_name, module in model.named_modules():
        has_params = any(
            True for _ in module.parameters(recurse=False)
        )
        if not has_params or full_name in visited:
            continue
        visited.add(full_name)

        for pname in list(module._parameters.keys()):
            res = get_fp16_tensor(full_name, module, pname)
            if res is None:
                continue
            key, tensor = res
            nbytes = tensor.nelement() * tensor.element_size()

            if shard and shard_bytes + nbytes > MAX_SHARD:
                fname = save_shard(shard, shard_idx, DST)
                shard_files.append(fname)
                shard = {}
                shard_bytes = 0
                shard_idx += 1
                current_fname = f"model-part-{shard_idx:05d}.safetensors"
                gc.collect()
                torch.cuda.empty_cache()

            shard[key] = tensor
            shard_bytes += nbytes
            weight_map[key] = current_fname
            processed += 1

        torch.cuda.empty_cache()

    if shard:
        fname = save_shard(shard, shard_idx, DST)
        shard_files.append(fname)

    print(f"\n{processed} tensors saved in {len(shard_files)} shard(s).")

    # Rename with correct total count
    total = len(shard_files)
    final_map: dict = {}
    for i, old in enumerate(shard_files):
        new = f"model-{i+1:05d}-of-{total:05d}.safetensors"
        os.rename(DST / old, DST / new)
        for k, v in weight_map.items():
            if v == old:
                final_map[k] = new

    # Verify shapes
    from safetensors import safe_open
    with safe_open(str(DST / f"model-00001-of-{total:05d}.safetensors"), framework="pt") as sf:
        k0 = list(sf.keys())[0]
        print(f"\nShape check [{k0}]: {sf.get_tensor(k0).shape}")
        # Check a linear weight
        for k in sf.keys():
            if "q_proj.weight" in k:
                t = sf.get_tensor(k)
                print(f"Shape check [{k}]: {t.shape}  dtype={t.dtype}")
                break

    # Write index
    total_bytes = sum((DST / f"model-{i+1:05d}-of-{total:05d}.safetensors").stat().st_size
                      for i in range(total))
    index = {"metadata": {"total_size": total_bytes}, "weight_map": final_map}
    (DST / "model.safetensors.index.json").write_text(
        json.dumps(index, indent=2), encoding="utf-8"
    )

    del model
    gc.collect()
    torch.cuda.empty_cache()

    # Save tokenizer + configs
    tokenizer.save_pretrained(str(DST))
    for f in ["config.json", "generation_config.json"]:
        src = LORA_PATH / f
        if src.exists():
            shutil.copy2(src, DST / f)
    # Remove tie_word_embeddings duplication: keep lm_head as separate tensor
    # (already done by our dequantize loop)

    total_gb = sum(f.stat().st_size for f in DST.rglob("*") if f.is_file()) / 1e9
    print(f"\nDone! fp16 model at {DST}  ({total_gb:.1f} GB)")


if __name__ == "__main__":
    main()
