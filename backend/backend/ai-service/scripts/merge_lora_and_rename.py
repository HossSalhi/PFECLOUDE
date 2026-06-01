"""
Post-process the dequantized LoRA shards:
  1. Strip PEFT prefix  (base_model.model. → )
  2. Merge LoRA delta   (base_layer.weight + scale * lora_B @ lora_A)
  3. Save new shards with standard HuggingFace names

Works entirely on CPU from the already-saved fp16 shards.
No GPU or re-quantization needed.

Usage:
    python scripts/merge_lora_and_rename.py
"""
from __future__ import annotations
import gc, json, os, re, sys
from pathlib import Path

os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONUNBUFFERED", "1")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)

import torch
from safetensors import safe_open
from safetensors.torch import save_file

SRC         = Path("model_artifacts/aiops-sre_fp16")
DST         = Path("model_artifacts/aiops-sre_merged_fp16")
MAX_SHARD   = int(2 * 1024 ** 3)   # 2 GB per shard
LORA_SCALE  = 2.0   # lora_alpha / lora_r = 32 / 16

# LoRA target module names (those that have base_layer / lora_A / lora_B)
LORA_MODULES = {"q_proj", "k_proj", "v_proj", "o_proj",
                "gate_proj", "up_proj", "down_proj"}


def strip_peft_prefix(name: str) -> str:
    """Remove 'base_model.model.' leading prefix."""
    if name.startswith("base_model.model."):
        return name[len("base_model.model."):]
    return name


def canonical_name(name: str) -> str | None:
    """
    Convert PEFT tensor name → standard HuggingFace name.
    Returns None if tensor should be skipped.
    """
    name = strip_peft_prefix(name)

    # Skip LoRA adapter tensors (they'll be merged into base_layer)
    if ".lora_A." in name or ".lora_B." in name or ".lora_embedding" in name:
        return None
    if ".scaling" in name or ".lora_dropout" in name:
        return None
    # Skip PEFT internal metadata
    if "lora_magnitude_vector" in name:
        return None

    # base_layer.weight → weight   (for LoRA target modules)
    name = name.replace(".base_layer.weight", ".weight")
    name = name.replace(".base_layer.bias",   ".bias")

    return name


def load_all_shards(src: Path) -> dict[str, torch.Tensor]:
    """Load every tensor from every shard into a flat dict."""
    all_tensors: dict[str, torch.Tensor] = {}
    for sf_file in sorted(src.glob("model-*.safetensors")):
        print(f"  loading {sf_file.name} …")
        with safe_open(str(sf_file), framework="pt", device="cpu") as sf:
            for k in sf.keys():
                all_tensors[k] = sf.get_tensor(k)
    print(f"  total tensors loaded: {len(all_tensors)}")
    return all_tensors


def merge_lora(all_t: dict, src_name: str, canon: str) -> torch.Tensor:
    """
    If this tensor is a base_layer weight that has LoRA adapters,
    return merged weight; otherwise return original.
    """
    w = all_t[src_name]

    # Determine if this tensor has LoRA by checking for matching lora_A/lora_B
    # src_name example:
    #   base_model.model.model.layers.0.self_attn.q_proj.base_layer.weight
    if ".base_layer.weight" not in src_name:
        return w

    # Extract module name (drop .base_layer.weight)
    mod_prefix = src_name[: src_name.rfind(".base_layer.weight")]
    # Check the last component is a LoRA target module
    mod_leaf = mod_prefix.split(".")[-1]
    if mod_leaf not in LORA_MODULES:
        return w

    lora_a_key = mod_prefix + ".lora_A.default.weight"
    lora_b_key = mod_prefix + ".lora_B.default.weight"
    if lora_a_key not in all_t or lora_b_key not in all_t:
        return w

    lora_a = all_t[lora_a_key].to(torch.float32)
    lora_b = all_t[lora_b_key].to(torch.float32)
    base   = w.to(torch.float32)

    # LoRA delta: lora_B has shape [out, r], lora_A has shape [r, in]
    delta = LORA_SCALE * (lora_b @ lora_a)
    merged = (base + delta).to(torch.float16)
    return merged


def save_shard(shard: dict, idx: int, total_hint: str, dst: Path) -> str:
    fname = f"model-part-{idx:05d}.safetensors"
    save_file(shard, str(dst / fname))
    mb = sum(t.nelement() * t.element_size() for t in shard.values()) / 1e6
    print(f"  saved shard {idx}: {len(shard)} tensors, {mb:.0f} MB")
    return fname


def main() -> None:
    DST.mkdir(parents=True, exist_ok=True)

    print("Loading all source shards into memory …")
    all_t = load_all_shards(SRC)

    print("\nBuilding merged+renamed state dict and sharding …")
    shard: dict[str, torch.Tensor] = {}
    shard_bytes = 0
    shard_idx = 1
    shard_files: list[str] = []
    weight_map: dict[str, str] = {}
    current_fname = f"model-part-{shard_idx:05d}.safetensors"
    processed = skipped = 0

    for src_name, tensor in all_t.items():
        canon = canonical_name(src_name)
        if canon is None:
            skipped += 1
            continue

        t = merge_lora(all_t, src_name, canon).to(torch.float16)
        nbytes = t.nelement() * t.element_size()

        if shard and shard_bytes + nbytes > MAX_SHARD:
            fname = save_shard(shard, shard_idx, "", DST)
            shard_files.append(fname)
            shard = {}
            shard_bytes = 0
            shard_idx += 1
            current_fname = f"model-part-{shard_idx:05d}.safetensors"
            gc.collect()

        shard[canon] = t
        shard_bytes += nbytes
        weight_map[canon] = current_fname
        processed += 1

    if shard:
        fname = save_shard(shard, shard_idx, "", DST)
        shard_files.append(fname)

    print(f"\nProcessed {processed} tensors, skipped {skipped} LoRA adapter tensors.")

    # Rename with correct total count
    total = len(shard_files)
    final_map: dict[str, str] = {}
    for i, old in enumerate(shard_files):
        new = f"model-{i+1:05d}-of-{total:05d}.safetensors"
        os.rename(DST / old, DST / new)
        for k, v in weight_map.items():
            if v == old:
                final_map[k] = new

    # Shape verification
    first_shard = f"model-00001-of-{total:05d}.safetensors"
    print("\nShape verification:")
    with safe_open(str(DST / first_shard), framework="pt", device="cpu") as sf:
        for k in sf.keys():
            if any(x in k for x in ["embed_tokens", "q_proj.weight", "lm_head"]):
                t = sf.get_tensor(k)
                print(f"  {k}: {t.shape} {t.dtype}")

    # Write safetensors index
    total_bytes = sum((DST / f"model-{i+1:05d}-of-{total:05d}.safetensors").stat().st_size
                      for i in range(total))
    index = {"metadata": {"total_size": total_bytes}, "weight_map": final_map}
    (DST / "model.safetensors.index.json").write_text(
        json.dumps(index, indent=2), encoding="utf-8"
    )

    # Copy config + tokenizer files
    import shutil
    for f in SRC.iterdir():
        if f.suffix in (".json", ".jinja", ".model", ".tiktoken") and f.is_file():
            shutil.copy2(f, DST / f.name)

    size_gb = sum(f.stat().st_size for f in DST.rglob("*") if f.is_file()) / 1e9
    print(f"\nDone!  merged fp16 model → {DST}  ({size_gb:.1f} GB)")
    print("Tensor names now use standard HuggingFace format (model.layers.*)")


if __name__ == "__main__":
    main()
