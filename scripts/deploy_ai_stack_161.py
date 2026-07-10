"""
HELIOS — Déploiement PRODUCTION du nouveau stack IA sur .161 (k8s-aiCamunda).

Ce que fait ce script (tout via SSH/SFTP, aucun test déclenché depuis Windows) :
  1. Synchronise le CODE ai-service (app/) sur .161  -> RAG + nouveau moteur LLM + config
  2. (option) Déploie le LLM fine-tuné .gguf -> modèle Ollama 'helios-120-3b'
  3. Redémarre l'ai-service (systemd ou docker)
  4. Smoke tests SUR .161 : /health, /predict, décision LLM+RAG en direct

Le ML live (model.joblib) N'EST PAS écrasé : il est prouvé sur données réelles.
Le 120-types synthétique ne transfère pas au réel (train/serve skew) -> on garde le
ML prouvé pour la classification ; la richesse 120 vient du LLM+RAG+catalogue (action
+ explication). Retrain ML sur données réelles = étape ultérieure (après test réel).

Usage :
    # déploiement complet (code + LLM) :
    python scripts/deploy_ai_stack_161.py --gguf "C:\\Users\\houss\\Downloads\\Qwen2.5-3B-Instruct.Q4_K_M.gguf"
    # redéployer seulement le code (LLM déjà en place) :
    python scripts/deploy_ai_stack_161.py --code-only
"""
from __future__ import annotations
import argparse, os, sys, time
from pathlib import Path

os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONUNBUFFERED", "1")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)

import paramiko

VM_HOST = "192.168.148.161"
VM_USER = "kube"
VM_PASS = "kube"
REMOTE_AI_DIR = f"/home/{VM_USER}/ai-service"
MODEL_NAME = "helios-120-3b"
REMOTE_GGUF = f"/home/{VM_USER}/{MODEL_NAME}.Q4_K_M.gguf"

REPO = Path(__file__).resolve().parents[1]
LOCAL_APP_DIR = REPO / "backend" / "backend" / "ai-service" / "app"

SYSTEM_PROMPT = (
    "You are the automatic decision engine of a Kubernetes self-healing system (HELIOS). "
    "Reason from the symptoms and the knowledge base, then output EXACTLY one line: "
    "DIAGNOSTIC: <cause> | ACTION=<ACTION_NAME> | EXPLICATION: <how the action fixes it>."
)
MODELFILE = f"""FROM {REMOTE_GGUF}
SYSTEM \"\"\"{SYSTEM_PROMPT}\"\"\"
PARAMETER temperature 0.2
PARAMETER num_ctx 1024
PARAMETER num_predict 200
"""


def sh(c, cmd, timeout=300, check=True, quiet=False):
    _, out, err = c.exec_command(cmd, timeout=timeout)
    o = out.read().decode(errors="replace").rstrip()
    e = err.read().decode(errors="replace").rstrip()
    code = out.channel.recv_exit_status()
    if o and not quiet:
        print("   ", o[:800].replace("\n", "\n    "))
    if e and not quiet:
        print("    STDERR:", e[:400])
    if check and code != 0:
        raise RuntimeError(f"exit {code}: {cmd}")
    return o


def _progress(fn, t, s):
    print(f"\r    {fn}: {t*100//s if s else 0}% ({t/1e6:.0f}/{s/1e6:.0f} MB)", end="", flush=True)


def sync_code(sftp, c):
    print(f"[1/4] Sync du code ai-service -> {REMOTE_AI_DIR}/app")
    sh(c, f"mkdir -p {REMOTE_AI_DIR}/app", quiet=True)
    n = 0
    for item in sorted(LOCAL_APP_DIR.rglob("*")):
        rel = item.relative_to(LOCAL_APP_DIR)
        if "__pycache__" in rel.parts or item.suffix == ".pyc":
            continue
        rpath = f"{REMOTE_AI_DIR}/app/" + rel.as_posix()
        if item.is_dir():
            try:
                sftp.stat(rpath)
            except FileNotFoundError:
                sftp.mkdir(rpath)
        else:
            sftp.put(str(item), rpath)
            n += 1
    print(f"    {n} fichiers synchronisés (RAG + inference.py + config inclus).")


def deploy_llm(sftp, c, gguf: Path):
    print(f"[2/4] Déploiement LLM '{MODEL_NAME}' ({gguf.name}, {gguf.stat().st_size/1e6:.0f} MB)")
    # ne ré-uploade pas si déjà présent à la bonne taille
    try:
        st = sftp.stat(REMOTE_GGUF)
        if st.st_size == gguf.stat().st_size:
            print("    .gguf déjà présent (même taille) -> pas de ré-upload.")
        else:
            raise FileNotFoundError
    except FileNotFoundError:
        sftp.put(str(gguf), REMOTE_GGUF, callback=lambda t, s: _progress(gguf.name, t, s))
        print()
    chan = c.get_transport().open_session()
    chan.exec_command(f"cat > /tmp/Modelfile-{MODEL_NAME}")
    chan.sendall(MODELFILE.encode("utf-8"))
    chan.shutdown_write()
    chan.recv_exit_status()
    print(f"    ollama create {MODEL_NAME} ...")
    sh(c, f"ollama create {MODEL_NAME} -f /tmp/Modelfile-{MODEL_NAME} 2>&1", timeout=300)
    sh(c, f"ollama list | grep -E 'NAME|{MODEL_NAME}' || true", check=False)


def restart_service(c):
    print("[3/4] Redémarrage de l'ai-service")
    cmd = f"""
if echo '{VM_PASS}' | sudo -S systemctl is-active --quiet ai-service 2>/dev/null; then
  echo '{VM_PASS}' | sudo -S systemctl restart ai-service && echo 'systemd restart OK'
elif docker compose -f {REMOTE_AI_DIR}/docker-compose.yml ps 2>/dev/null | grep -q ai-service; then
  docker compose -f {REMOTE_AI_DIR}/docker-compose.yml restart ai-service && echo 'docker restart OK'
else
  echo 'ATTENTION: service ai-service non détecté (systemd/docker) — démarrage manuel requis'
fi
""".strip()
    sh(c, cmd, check=False)
    print("    attente 10s du redémarrage ...")
    time.sleep(10)


def smoke(c):
    print("[4/4] Smoke tests SUR .161")
    print("  · /health :")
    sh(c, "curl -sf --max-time 12 http://localhost:8000/health || echo HEALTH_FAILED", check=False)
    print("  · /predict (incident crashloop simulé) :")
    payload = ('{"namespace":"pfe","app":"backend","metrics":{"pod_restart_count":7,'
               '"memory_usage_percent":40,"cpu_usage_percent":30,"http_error_rate":0.0},'
               '"logs":["Back-off restarting failed container","CrashLoopBackOff"]}')
    sh(c, f"curl -sf --max-time 40 -X POST http://localhost:8000/predict "
          f"-H 'Content-Type: application/json' -d '{payload}' || echo PREDICT_FAILED",
       timeout=50, check=False)
    print("\n  · LLM+RAG en direct (modèle helios-120-3b) :")
    dp = ('{"model":"' + MODEL_NAME + '","stream":false,"prompt":'
          '"Incident: POD_OOM_KILLED. Evidence: last termination OOMKilled, memory at the limit. '
          'Decide the remediation action."}')
    sh(c, f"curl -s --max-time 60 http://localhost:11434/api/generate -d '{dp}'",
       timeout=90, check=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gguf", help="chemin du .gguf à déployer (sinon --code-only)")
    ap.add_argument("--code-only", action="store_true", help="ne déploie que le code (LLM déjà en place)")
    args = ap.parse_args()

    if not args.code_only and not args.gguf:
        sys.exit("Donne --gguf <chemin> OU --code-only")
    gguf = Path(args.gguf) if args.gguf else None
    if gguf and not gguf.exists():
        sys.exit(f".gguf introuvable : {gguf}")
    if not LOCAL_APP_DIR.exists():
        sys.exit(f"code ai-service introuvable : {LOCAL_APP_DIR}")

    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    print(f"Connexion {VM_USER}@{VM_HOST} ...")
    c.connect(VM_HOST, username=VM_USER, password=VM_PASS,
              timeout=15, look_for_keys=False, allow_agent=False)
    print("  Connecté.\n")
    sftp = c.open_sftp()

    sync_code(sftp, c)
    if gguf:
        deploy_llm(sftp, c, gguf)
    else:
        print("[2/4] LLM : --code-only -> saut du déploiement .gguf")
    restart_service(c)
    smoke(c)

    sftp.close()
    c.close()
    print("\n======================================")
    print(" Stack IA déployé sur .161")
    print("======================================")
    print(f"  ai-service : http://{VM_HOST}:8000  | Ollama : http://{VM_HOST}:11434")
    print(f"  LLM        : {MODEL_NAME} (décision + explication, ancré RAG 120 types)")
    print("  ML         : model.joblib prouvé conservé (classification)")
    print("  Suite : test réel E2E DANS le cluster -> scripts/prod_test_in_cluster.py")


if __name__ == "__main__":
    main()
