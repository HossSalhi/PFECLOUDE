"""Deploie le LLM fine-tune HELIOS (GGUF Q4) sur .161 dans Ollama.

Entree : le .gguf telecharge depuis l'Output Kaggle (cellule 7 -> qwen3b-helios/*.gguf).
Sortie : modele Ollama 'helios-120-3b' pret, teste sur un vrai incident.

Contrairement a l'ancien deploy_to_vm.py (uploadait un dossier fp16 de 4 Go puis
convertissait en GGUF sur le CPU de .161 = ~10 min), ici le GGUF est deja pret :
upload direct (~2 Go) + `ollama create` instantane.

Usage :
    python scripts/llm-finetune/deploy_helios_120_gguf.py C:\\chemin\\vers\\qwen3b-helios.Q4_K_M.gguf
"""
from __future__ import annotations
import os, sys, time
from pathlib import Path

os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONUNBUFFERED", "1")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)

import paramiko

VM_HOST = "192.168.148.161"
VM_USER = "kube"
VM_PASS = "kube"
MODEL_NAME = "helios-120-3b"
REMOTE_GGUF = f"/home/{VM_USER}/{MODEL_NAME}.Q4_K_M.gguf"

# DOIT correspondre EXACTEMENT au system prompt d'entrainement (notebook cellule 6/8),
# sinon le modele repond hors-format.
SYSTEM_PROMPT = (
    "You are the automatic decision engine of a Kubernetes self-healing system. "
    "Reason from the symptoms and output: "
    "DIAGNOSTIC: <cause> | ACTION=<ACTION_NAME> | EXPLICATION: <how to fix>."
)

MODELFILE = f"""FROM {REMOTE_GGUF}
SYSTEM \"\"\"{SYSTEM_PROMPT}\"\"\"
PARAMETER temperature 0.3
PARAMETER num_ctx 1024
PARAMETER num_predict 200
"""


def _progress(fn, transferred, total):
    pct = transferred * 100 // total if total else 0
    print(f"\r  {fn}: {pct}%  ({transferred/1e6:.0f}/{total/1e6:.0f} MB)", end="", flush=True)


def sh(c, cmd, timeout=600, check=True):
    _, out, err = c.exec_command(cmd, timeout=timeout)
    o = out.read().decode(errors="replace").rstrip()
    e = err.read().decode(errors="replace").rstrip()
    code = out.channel.recv_exit_status()
    if o:
        print(o)
    if e:
        print("STDERR:", e[:500])
    if check and code != 0:
        raise RuntimeError(f"exit {code}: {cmd}")
    return o


def main():
    if len(sys.argv) < 2:
        sys.exit("Usage: python deploy_helios_120_gguf.py <chemin/vers/*.gguf>")
    gguf = Path(sys.argv[1])
    if not gguf.exists():
        sys.exit(f"Fichier introuvable : {gguf}")

    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    print(f"Connexion a {VM_USER}@{VM_HOST} ...")
    c.connect(VM_HOST, username=VM_USER, password=VM_PASS,
              timeout=15, look_for_keys=False, allow_agent=False)
    print("  Connecte.\n")

    # 1. Upload du GGUF
    print(f"[1/4] Upload {gguf.name} ({gguf.stat().st_size/1e6:.0f} MB) -> {REMOTE_GGUF}")
    sftp = c.open_sftp()
    sftp.put(str(gguf), REMOTE_GGUF, callback=lambda t, s: _progress(gguf.name, t, s))
    print("\n  Upload OK.\n")

    # 2. Ecrire le Modelfile
    print("[2/4] Ecriture du Modelfile")
    chan = c.get_transport().open_session()
    chan.exec_command(f"cat > /tmp/Modelfile-{MODEL_NAME}")
    chan.sendall(MODELFILE.encode("utf-8"))
    chan.shutdown_write()
    chan.recv_exit_status()
    print(sh(c, f"head -2 /tmp/Modelfile-{MODEL_NAME}"))

    # 3. ollama create (GGUF deja pret -> instantane)
    print(f"\n[3/4] ollama create {MODEL_NAME}")
    sh(c, f"ollama create {MODEL_NAME} -f /tmp/Modelfile-{MODEL_NAME} 2>&1")
    print(sh(c, f"ollama list | grep -E 'NAME|{MODEL_NAME}'", check=False))

    # 4. Smoke test sur un vrai incident
    print("\n[4/4] Test sur un incident OOMKilled")
    payload = (
        '{"model":"' + MODEL_NAME + '","stream":false,'
        '"prompt":"Incident: payment-svc OOMKilled, memory grows steadily over hours. '
        'Decide the remediation action."}'
    )
    resp = sh(c, "curl -s --max-time 60 http://localhost:11434/api/generate "
                 f"-d '{payload}'", timeout=90, check=False)
    print("\n--- Reponse du modele ---")
    print(resp[:800])

    sftp.close()
    c.close()
    print("\n======================================")
    print(f" Modele '{MODEL_NAME}' deploye sur {VM_HOST}")
    print("======================================")
    print("Suite : mettre a jour backend/backend/ai-service/app/core/config.py")
    print(f'   llm_model = "{MODEL_NAME}"   (le nouveau moteur de decision + explication)')
    print("puis redemarrer l'ai-service.")


if __name__ == "__main__":
    main()
