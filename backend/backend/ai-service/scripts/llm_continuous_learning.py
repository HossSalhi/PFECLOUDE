"""
LLM Continuous Learning Pipeline — Auto-réentraînement du Qwen2.5-3B fine-tuné.

Ce script orchestre la boucle complète d'auto-apprentissage du LLM :
  1. Vérifie combien de nouveaux incidents résolus existent depuis le dernier fine-tuning
  2. Si le seuil est atteint (défaut: 20 incidents) → déclenche le pipeline
  3. Génère un dataset JSONL combinant incidents réels (backend API) + données synthétiques
  4. Lance finetune_lora.py  (LoRA fine-tuning sur GPU — ~1-2h)
  5. Lance dequantize_from_lora.py  (dequantize → fp16)
  6. Lance merge_lora_and_rename.py  (merge LoRA delta → modèle HF standard)
  7. Déploie sur VM 192.168.148.161 via SFTP + recrée le modèle Ollama
  8. Sauvegarde l'état dans last_llm_retrain.json

Exécution manuelle :
    python scripts/llm_continuous_learning.py

Forcer même si seuil non atteint :
    python scripts/llm_continuous_learning.py --force

Vérifier l'état sans agir :
    python scripts/llm_continuous_learning.py --status

Automatisation (Windows Task Scheduler, hebdomadaire) :
    python scripts/llm_continuous_learning.py --min-incidents 20

CONTRAINTE : Nécessite GPU CUDA (Windows). Ne tourne PAS sur la VM.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from datetime import datetime

os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONUNBUFFERED", "1")
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

# ── Configuration ────────────────────────────────────────────────────────────

BACKEND_URL   = "http://192.168.148.153:30808"
BACKEND_USER  = "operator"
BACKEND_PASS  = "operator-change-me"

VM_HOST = "192.168.148.161"
VM_USER = "kube"
VM_PASS = "kube"

SCRIPTS_DIR   = Path(__file__).parent
AI_SERVICE_DIR = SCRIPTS_DIR.parent

STATE_FILE    = AI_SERVICE_DIR / "model_artifacts" / "last_llm_retrain.json"
DATASET_PROD  = AI_SERVICE_DIR / "datasets" / "production_incidents.jsonl"
DATASET_SYNTH = AI_SERVICE_DIR / "datasets" / "finetune_dataset.jsonl"
DATASET_MERGED = AI_SERVICE_DIR / "datasets" / "merged_finetune_dataset.jsonl"

LORA_OUTPUT   = AI_SERVICE_DIR / "model_artifacts" / "aiops-sre-lora-cl"
FP16_OUTPUT   = AI_SERVICE_DIR / "model_artifacts" / "aiops-sre_fp16_cl"
MERGED_OUTPUT = AI_SERVICE_DIR / "model_artifacts" / "aiops-sre_merged_fp16_cl"

REMOTE_MODEL_DIR = "/home/kube/aiops-sre-fp16"
OLLAMA_MODEL_NAME = "aiops-sre"

MODELFILE_CONTENT = (
    f"FROM {REMOTE_MODEL_DIR}\n"
    "PARAMETER temperature 0\n"
    "PARAMETER num_predict 400\n"
    'PARAMETER stop "<|im_end|>"\n'
    'SYSTEM """You are aiops-sre, an expert Kubernetes SRE AI. '
    "Analyze metrics and logs, output ONLY valid JSON with: "
    "incident, incident_type, severity, confidence, recommended_action, executable, auto_execute, explanation.\"\"\"\n"
)


# ── State management ─────────────────────────────────────────────────────────

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "last_retrain_at": None,
        "last_incident_id_seen": None,
        "total_retrains": 0,
        "versions": [],
    }


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")


# ── Backend API ──────────────────────────────────────────────────────────────

def _api_get(path: str) -> dict | list:
    url = f"{BACKEND_URL}{path}"
    creds = base64.b64encode(f"{BACKEND_USER}:{BACKEND_PASS}".encode()).decode()
    req = urllib.request.Request(url, headers={"Authorization": f"Basic {creds}"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def get_resolved_incidents_since(since_iso: str | None, page_size: int = 200) -> list[dict]:
    """Fetch all resolved AI incidents from the backend API."""
    try:
        data = _api_get(f"/api/incidents?source=AI&status=RESOLVED&page=0&size={page_size}")
        incidents = data.get("content", data) if isinstance(data, dict) else data
        if since_iso:
            incidents = [i for i in incidents if i.get("createdAt", "") > since_iso]
        return incidents
    except Exception as exc:
        print(f"  WARNING: Could not fetch incidents from API: {exc}")
        return []


def count_new_incidents(state: dict) -> tuple[int, list[dict]]:
    """Returns (count, incidents_list) of new resolved incidents since last retrain."""
    since = state.get("last_retrain_at")
    incidents = get_resolved_incidents_since(since)
    return len(incidents), incidents


# ── Dataset generation ───────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are aiops-sre, an expert Kubernetes and infrastructure SRE AI.
Analyze Kubernetes metrics and logs to detect, classify, and recommend remediation for ANY incident.
Output ONLY valid JSON. Never add text outside the JSON object."""


def incident_to_finetune_example(incident: dict) -> dict | None:
    """Convert a real resolved incident to a fine-tuning JSONL example."""
    incident_type = incident.get("incidentType") or incident.get("incident_type") or ""
    if not incident_type or incident_type in ("UNKNOWN", "NO_INCIDENT", ""):
        return None

    severity = incident.get("severity", "MEDIUM")
    if hasattr(severity, "name"):
        severity = severity.name
    title = incident.get("title", "")
    description = incident.get("description", "")
    action_taken = incident.get("remediationAction") or incident.get("recommended_action") or "INVESTIGATE"
    confidence = float(incident.get("confidence") or 0.6)
    namespace = incident.get("namespace") or "pfe"
    app = incident.get("app") or "backend"
    auto_exec = action_taken in ("RESTART_POD", "SCALE_DEPLOYMENT") and confidence >= 0.55 and severity != "CRITICAL"

    # Build a user prompt from what we know about the incident
    user_content = (
        f"Kubernetes namespace={namespace} app={app} incident reported.\n"
        f"Title: {title}\n"
        f"Description: {description}\n"
        f"Analyze this incident and output the JSON response."
    )

    # Build the expected assistant response
    assistant_content = json.dumps({
        "incident": True,
        "incident_type": incident_type,
        "severity": severity if isinstance(severity, str) else str(severity),
        "confidence": round(confidence, 2),
        "recommended_action": f"Execute {action_taken} to resolve the {incident_type} incident",
        "executable": action_taken if action_taken in (
            "RESTART_POD", "SCALE_DEPLOYMENT", "INVESTIGATE", "ESCALATE", "NO_ACTION"
        ) else "INVESTIGATE",
        "auto_execute": auto_exec,
        "explanation": (
            f"Real production incident of type {incident_type} "
            f"with {severity} severity resolved by {action_taken}."
        ),
    }, ensure_ascii=False)

    return {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
            {"role": "assistant", "content": assistant_content},
        ]
    }


def build_merged_dataset(incidents: list[dict]) -> int:
    """
    Build merged JSONL dataset:
      - Convert real production incidents to training examples
      - Append synthetic dataset (existing finetune_dataset.jsonl)
    Returns number of examples written.
    """
    DATASET_MERGED.parent.mkdir(parents=True, exist_ok=True)
    examples = []

    # Real incidents
    prod_count = 0
    for inc in incidents:
        ex = incident_to_finetune_example(inc)
        if ex:
            examples.append(ex)
            prod_count += 1

    print(f"  Real production examples: {prod_count}")

    # Existing synthetic data (base knowledge — always include)
    synth_count = 0
    if DATASET_SYNTH.exists():
        with open(DATASET_SYNTH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        examples.append(json.loads(line))
                        synth_count += 1
                    except Exception:
                        pass
        print(f"  Synthetic examples: {synth_count}")
    else:
        print("  WARNING: No synthetic dataset found — training on real data only")

    # Write merged dataset
    with open(DATASET_MERGED, "w", encoding="utf-8") as f:
        for ex in examples:
            f.write(json.dumps(ex, ensure_ascii=False) + "\n")

    total = len(examples)
    print(f"  Total merged examples: {total} → {DATASET_MERGED}")
    return total


# ── Pipeline steps ────────────────────────────────────────────────────────────

def run_step(label: str, cmd: list[str], cwd: Path | None = None, timeout: int = 7200) -> bool:
    """Run a subprocess step. Returns True on success."""
    print(f"\n{'='*60}")
    print(f"STEP: {label}")
    print(f"  CMD: {' '.join(str(c) for c in cmd)}")
    print(f"{'='*60}")
    try:
        result = subprocess.run(
            cmd,
            cwd=str(cwd or AI_SERVICE_DIR),
            timeout=timeout,
            check=True,
        )
        print(f"  ✓ {label} completed (exit {result.returncode})")
        return True
    except subprocess.CalledProcessError as exc:
        print(f"  ✗ {label} FAILED (exit {exc.returncode})")
        return False
    except subprocess.TimeoutExpired:
        print(f"  ✗ {label} TIMED OUT after {timeout}s")
        return False
    except Exception as exc:
        print(f"  ✗ {label} ERROR: {exc}")
        return False


def step_finetune(dataset_path: Path, lora_output: Path, epochs: int) -> bool:
    return run_step(
        "LoRA fine-tuning (GPU required, ~1-2h)",
        [
            sys.executable, str(SCRIPTS_DIR / "finetune_lora.py"),
            "--dataset", str(dataset_path),
            "--output", str(lora_output),
            "--epochs", str(epochs),
            "--batch-size", "1",
            "--grad-accum", "8",
            "--max-seq-len", "1024",
            "--load-in-4bit",
        ],
        timeout=10800,  # 3h max
    )


def step_dequantize(lora_path: Path, fp16_output: Path) -> bool:
    """Dequantize LoRA adapter to fp16 safetensors."""
    # Patch paths in dequantize script via env vars
    env = os.environ.copy()
    env["CL_LORA_PATH"] = str(lora_path)
    env["CL_FP16_DST"] = str(fp16_output)

    # Use the existing dequantize script but with overridden paths
    script = SCRIPTS_DIR / "dequantize_from_lora.py"
    # Read and patch paths dynamically
    src = script.read_text(encoding="utf-8")
    patched = src.replace(
        'LORA_PATH   = Path("model_artifacts/aiops-sre-lora-full")',
        f'LORA_PATH   = Path(r"{lora_path}")',
    ).replace(
        'DST         = Path("model_artifacts/aiops-sre_fp16")',
        f'DST         = Path(r"{fp16_output}")',
    )
    tmp_script = SCRIPTS_DIR / "_dequantize_cl_tmp.py"
    tmp_script.write_text(patched, encoding="utf-8")

    ok = run_step(
        "Dequantize LoRA → fp16 safetensors",
        [sys.executable, str(tmp_script)],
        timeout=3600,
    )
    try:
        tmp_script.unlink()
    except Exception:
        pass
    return ok


def step_merge(fp16_path: Path, merged_output: Path) -> bool:
    """Merge LoRA delta and rename to standard HF format."""
    script = SCRIPTS_DIR / "merge_lora_and_rename.py"
    src = script.read_text(encoding="utf-8")
    patched = src.replace(
        'SRC         = Path("model_artifacts/aiops-sre_fp16")',
        f'SRC         = Path(r"{fp16_path}")',
    ).replace(
        'DST         = Path("model_artifacts/aiops-sre_merged_fp16")',
        f'DST         = Path(r"{merged_output}")',
    )
    tmp_script = SCRIPTS_DIR / "_merge_cl_tmp.py"
    tmp_script.write_text(patched, encoding="utf-8")

    ok = run_step(
        "Merge LoRA delta → standard HF model",
        [sys.executable, str(tmp_script)],
        timeout=1800,
    )
    try:
        tmp_script.unlink()
    except Exception:
        pass
    return ok


def step_deploy(merged_path: Path) -> bool:
    """Upload merged fp16 model to VM and recreate Ollama model."""
    try:
        import paramiko
    except ImportError:
        print("  ERROR: paramiko not installed. Run: pip install paramiko")
        return False

    print(f"\n{'='*60}")
    print("STEP: Deploy to VM + Ollama create")
    print(f"{'='*60}")

    shard_files = sorted(merged_path.glob("model-*-of-*.safetensors"))
    if not shard_files:
        print("  ERROR: No shard files found in merged output")
        return False

    config_files = [
        f for f in merged_path.iterdir()
        if f.suffix in (".json", ".jinja", ".model", ".tiktoken") and f.is_file()
    ]

    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        c.connect(VM_HOST, username=VM_USER, password=VM_PASS,
                  timeout=15, look_for_keys=False, allow_agent=False)
        print(f"  Connected to {VM_HOST}")

        def _run(cmd: str, timeout: int = 60) -> str:
            _, out, err = c.exec_command(cmd, timeout=timeout)
            result = out.read().decode("utf-8", errors="replace").strip()
            code = out.channel.recv_exit_status()
            print(f"  [{code}] {cmd[:70]}")
            if result:
                print(f"    {result[:300]}")
            return result

        # Free old model from Ollama RAM
        _run(f'curl -s -X POST http://localhost:11434/api/generate -d \'{{"model":"{OLLAMA_MODEL_NAME}","keep_alive":0}}\'', timeout=15)
        time.sleep(2)

        # Remove old model dir + recreate
        _run(f"rm -rf {REMOTE_MODEL_DIR}", timeout=30)
        _run(f"mkdir -p {REMOTE_MODEL_DIR}", timeout=10)

        # Upload via SFTP
        sftp = c.open_sftp()
        all_files = shard_files + config_files
        for local_file in all_files:
            remote_path = f"{REMOTE_MODEL_DIR}/{local_file.name}"
            size_mb = local_file.stat().st_size / 1e6
            print(f"  Uploading {local_file.name} ({size_mb:.0f} MB)...")
            sftp.put(str(local_file), remote_path)
            print(f"    Done")
        sftp.close()

        # Recreate Ollama model
        modelfile_path = f"{REMOTE_MODEL_DIR}/Modelfile"
        _run(f"echo '{MODELFILE_CONTENT}' > {modelfile_path}", timeout=10)
        _run(f"ollama rm {OLLAMA_MODEL_NAME}", timeout=30)
        _run(f"ollama create {OLLAMA_MODEL_NAME} -f {modelfile_path}", timeout=600)

        # Verify
        result = _run("ollama list", timeout=15)
        if OLLAMA_MODEL_NAME in result:
            print(f"  ✓ {OLLAMA_MODEL_NAME} successfully created in Ollama")
        else:
            print(f"  ✗ Model not found in ollama list after create")
            return False

        # Restart AI service
        _run("echo kube | sudo -S systemctl restart pfe-ai-service", timeout=30)
        time.sleep(5)
        health = _run("curl -s http://localhost:8000/health", timeout=10)
        print(f"  AI service health: {health}")

        c.close()
        return True

    except Exception as exc:
        print(f"  ERROR during deploy: {exc}")
        try:
            c.close()
        except Exception:
            pass
        return False


# ── Main pipeline ─────────────────────────────────────────────────────────────

def print_status(state: dict, new_count: int, threshold: int) -> None:
    print("\n╔══════════════════════════════════════════════════════╗")
    print("║         LLM Continuous Learning — Status             ║")
    print("╚══════════════════════════════════════════════════════╝")
    last = state.get("last_retrain_at") or "jamais"
    total = state.get("total_retrains", 0)
    print(f"  Dernier fine-tuning  : {last}")
    print(f"  Total fine-tunings   : {total}")
    print(f"  Nouveaux incidents   : {new_count} / {threshold} requis")
    print(f"  Prêt à lancer        : {'✓ OUI' if new_count >= threshold else f'✗ NON (manque {threshold - new_count})'}")
    versions = state.get("versions", [])
    if versions:
        v = versions[-1]
        print(f"  Dernière version     : {v.get('version')} ({v.get('date')})")
        print(f"  Dataset size         : {v.get('dataset_size')} exemples")
        print(f"  Résultat             : {'✓ Succès' if v.get('success') else '✗ Échec'}")
    print()


def run_pipeline(args: argparse.Namespace) -> None:
    state = load_state()
    new_count, incidents = count_new_incidents(state)

    if args.status:
        print_status(state, new_count, args.min_incidents)
        return

    print_status(state, new_count, args.min_incidents)

    if new_count < args.min_incidents and not args.force:
        print(f"Seuil non atteint ({new_count} < {args.min_incidents} incidents).")
        print("Utilisez --force pour lancer quand même, ou attendez plus d'incidents.")
        return

    version = f"v{state['total_retrains'] + 1}"
    started_at = datetime.now().isoformat()
    print(f"\n🚀 Lancement du pipeline LLM auto-apprentissage — {version}")
    print(f"   {new_count} nouveaux incidents depuis: {state.get('last_retrain_at') or 'le début'}")

    # ── Étape 1 : Build dataset ───────────────────────────────────────────────
    print(f"\n[1/5] Construction du dataset ({new_count} incidents réels + synthétiques)")
    dataset_size = build_merged_dataset(incidents)
    if dataset_size == 0:
        print("ERROR: Dataset vide — abandon")
        return

    # ── Étape 2 : Fine-tune LoRA ──────────────────────────────────────────────
    print(f"\n[2/5] Fine-tuning LoRA ({args.epochs} epochs, GPU requis)...")
    LORA_OUTPUT.mkdir(parents=True, exist_ok=True)
    ok = step_finetune(DATASET_MERGED, LORA_OUTPUT, args.epochs)
    if not ok:
        print("ERREUR à l'étape fine-tuning. Vérifiez que CUDA est disponible.")
        _record_failure(state, version, started_at, dataset_size, "finetune_failed")
        save_state(state)
        return

    # ── Étape 3 : Dequantize → fp16 ──────────────────────────────────────────
    print(f"\n[3/5] Dequantization LoRA → fp16 safetensors...")
    ok = step_dequantize(LORA_OUTPUT, FP16_OUTPUT)
    if not ok:
        _record_failure(state, version, started_at, dataset_size, "dequantize_failed")
        save_state(state)
        return

    # ── Étape 4 : Merge ──────────────────────────────────────────────────────
    print(f"\n[4/5] Merge LoRA delta → modèle HuggingFace standard...")
    ok = step_merge(FP16_OUTPUT, MERGED_OUTPUT)
    if not ok:
        _record_failure(state, version, started_at, dataset_size, "merge_failed")
        save_state(state)
        return

    # ── Étape 5 : Déployer sur VM ─────────────────────────────────────────────
    print(f"\n[5/5] Déploiement sur VM {VM_HOST} + Ollama create...")
    ok = step_deploy(MERGED_OUTPUT)

    # ── Résultat ──────────────────────────────────────────────────────────────
    state["last_retrain_at"] = started_at
    state["last_incident_id_seen"] = incidents[-1].get("id") if incidents else None
    state["total_retrains"] = state.get("total_retrains", 0) + 1
    state.setdefault("versions", []).append({
        "version": version,
        "date": started_at,
        "dataset_size": dataset_size,
        "new_incidents": new_count,
        "epochs": args.epochs,
        "success": ok,
        "step_failed": None if ok else "deploy",
    })
    save_state(state)

    duration = round((time.time() - time.mktime(
        time.strptime(started_at[:19], "%Y-%m-%dT%H:%M:%S")
    )) / 60, 1)

    print(f"\n{'='*60}")
    if ok:
        print(f"✓ Pipeline {version} TERMINÉ avec succès en {duration} minutes")
        print(f"  Dataset : {dataset_size} exemples ({new_count} réels)")
        print(f"  Modèle  : ollama aiops-sre mis à jour sur {VM_HOST}")
    else:
        print(f"✗ Pipeline {version} ÉCHOUÉ à l'étape déploiement")
        print(f"  Le modèle fusionné est disponible dans: {MERGED_OUTPUT}")
        print(f"  Relancez avec --force après correction.")
    print(f"{'='*60}\n")


def _record_failure(state: dict, version: str, started_at: str, dataset_size: int, reason: str) -> None:
    state.setdefault("versions", []).append({
        "version": version,
        "date": started_at,
        "dataset_size": dataset_size,
        "success": False,
        "step_failed": reason,
    })


# ── Entry point ───────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="LLM Continuous Learning — Auto-réentraîne le Qwen2.5-3B sur de vrais incidents",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--min-incidents", type=int, default=20,
        help="Nombre minimum de nouveaux incidents pour déclencher le fine-tuning (défaut: 20)",
    )
    p.add_argument(
        "--epochs", type=int, default=2,
        help="Nombre d'epochs LoRA (défaut: 2 — rapide, suffisant pour s'adapter)",
    )
    p.add_argument(
        "--force", action="store_true",
        help="Forcer le fine-tuning même si le seuil n'est pas atteint",
    )
    p.add_argument(
        "--status", action="store_true",
        help="Afficher l'état actuel sans lancer le pipeline",
    )
    return p.parse_args()


if __name__ == "__main__":
    run_pipeline(parse_args())
