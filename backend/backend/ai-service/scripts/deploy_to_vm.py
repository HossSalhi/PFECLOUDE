"""
Deploy fine-tuned fp16 model + AI service to the AI/Ollama VM (192.168.148.161).
Uses paramiko for SSH + SFTP — no sshpass or key required.

Steps:
  1. Upload fp16 model (~4 GB) to /opt/aiops-sre-fp16
  2. Create Ollama model 'aiops-sre' from the uploaded dir
  3. Sync AI service Python code
  4. Restart AI service
  5. Smoke test (/health + /predict)

Usage:
    python scripts/deploy_to_vm.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONUNBUFFERED", "1")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)

import paramiko

VM_HOST = "192.168.148.161"
VM_USER = "kube"
VM_PASS = "kube"
REMOTE_MODEL_DIR = "/home/kube/aiops-sre-fp16"
REMOTE_AI_DIR    = "/home/kube/ai-service"

LOCAL_FP16_DIR   = Path("model_artifacts/aiops-sre_fp16")
LOCAL_APP_DIR    = Path("app")
LOCAL_MODEL_JOBLIB = Path("model_artifacts/model.joblib")


def _progress(filename: str, transferred: int, total: int) -> None:
    pct = transferred * 100 // total if total else 0
    mb  = transferred / 1e6
    tot = total / 1e6
    print(f"\r  {filename}: {pct}%  ({mb:.0f}/{tot:.0f} MB)", end="", flush=True)


def ssh_exec(client: paramiko.SSHClient, cmd: str, check: bool = True) -> str:
    print(f"  $ {cmd}")
    stdin, stdout, stderr = client.exec_command(cmd, timeout=300)
    out  = stdout.read().decode(errors="replace").strip()
    err  = stderr.read().decode(errors="replace").strip()
    code = stdout.channel.recv_exit_status()
    if out:
        print(f"    {out[:400]}")
    if err:
        print(f"    STDERR: {err[:400]}")
    if check and code != 0:
        raise RuntimeError(f"Command failed (exit {code}): {cmd}")
    return out


def upload_dir(sftp: paramiko.SFTPClient, local: Path, remote: str) -> None:
    try:
        sftp.stat(remote)
    except FileNotFoundError:
        sftp.mkdir(remote)

    for item in sorted(local.rglob("*")):
        rel   = item.relative_to(local)
        rpath = remote + "/" + rel.as_posix()
        if item.is_dir():
            try:
                sftp.stat(rpath)
            except FileNotFoundError:
                sftp.mkdir(rpath)
        else:
            size_mb = item.stat().st_size / 1e6
            print(f"  uploading {rel}  ({size_mb:.1f} MB)")
            sftp.put(
                str(item), rpath,
                callback=lambda t, s, fn=rel.name: _progress(fn, t, s),
            )
            print()  # newline after progress


def main() -> None:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    print(f"Connecting to {VM_USER}@{VM_HOST}...")
    client.connect(VM_HOST, username=VM_USER, password=VM_PASS,
                   timeout=15, look_for_keys=False, allow_agent=False)
    print("  Connected.\n")

    sftp = client.open_sftp()

    # ── Step 1: Upload fp16 model ───────────────────────────────────────────
    print(f"[1/5] Uploading fp16 model to {REMOTE_MODEL_DIR} ...")
    ssh_exec(client, f"mkdir -p {REMOTE_MODEL_DIR}")
    upload_dir(sftp, LOCAL_FP16_DIR, REMOTE_MODEL_DIR)
    print(f"  Model uploaded.\n")

    # ── Step 2: Create / update Ollama model ────────────────────────────────
    print("[2/5] Creating Ollama model 'aiops-sre' ...")
    modelfile = f"""FROM {REMOTE_MODEL_DIR}
PARAMETER temperature 0
PARAMETER num_predict 400
PARAMETER stop "</s>"
SYSTEM \"\"\"You are an expert SRE AI assistant. Analyze the provided metrics and logs, then respond ONLY with a valid JSON object with exactly these fields: incident (bool), incident_type (string), severity (string: LOW/MEDIUM/HIGH/CRITICAL), confidence (float 0-1), recommended_action (string), auto_execute (bool), explanation (string).\"\"\"
"""
    # Write modelfile remotely
    escaped = modelfile.replace("'", "'\\''")
    ssh_exec(client, f"cat > /tmp/Modelfile-aiops-sre << 'MEOF'\n{modelfile}\nMEOF")
    # Check ollama is running
    ssh_exec(client, "ollama list 2>/dev/null | head -3 || echo 'ollama not ready'", check=False)
    ssh_exec(client, "ollama create aiops-sre -f /tmp/Modelfile-aiops-sre")
    ssh_exec(client, "ollama list | grep aiops-sre")
    print("  Ollama model created.\n")

    # ── Step 3: Sync AI service code ────────────────────────────────────────
    print("[3/5] Syncing AI service source code ...")
    ssh_exec(client, f"mkdir -p {REMOTE_AI_DIR}/app {REMOTE_AI_DIR}/model_artifacts")
    upload_dir(sftp, LOCAL_APP_DIR, f"{REMOTE_AI_DIR}/app")
    if LOCAL_MODEL_JOBLIB.exists():
        print(f"  uploading model.joblib ...")
        sftp.put(str(LOCAL_MODEL_JOBLIB), f"{REMOTE_AI_DIR}/model_artifacts/model.joblib")
    print("  Code synced.\n")

    # ── Step 4: Restart AI service ──────────────────────────────────────────
    print("[4/5] Restarting AI service ...")
    restart_cmd = f"""
if echo '{VM_PASS}' | sudo -S systemctl is-active --quiet ai-service 2>/dev/null; then
  echo '{VM_PASS}' | sudo -S systemctl restart ai-service && echo 'systemd restart OK'
elif docker compose -f {REMOTE_AI_DIR}/docker-compose.yml ps 2>/dev/null | grep -q ai-service; then
  docker compose -f {REMOTE_AI_DIR}/docker-compose.yml restart ai-service && echo 'docker restart OK'
else
  echo 'WARNING: no managed service detected — you may need to start manually'
fi
"""
    ssh_exec(client, restart_cmd.strip(), check=False)
    print("  Waiting 8 s for service to start...")
    time.sleep(8)
    print()

    # ── Step 5: Smoke test ──────────────────────────────────────────────────
    print("[5/5] Running smoke tests ...")
    health = ssh_exec(client, f"curl -sf --max-time 12 http://localhost:8000/health || echo 'HEALTH_FAILED'", check=False)
    if "HEALTH_FAILED" in health or not health:
        print("  WARNING: /health check failed — check service logs on VM")
    else:
        print(f"  /health OK: {health[:120]}")

    predict = ssh_exec(client, r"""curl -sf --max-time 25 -X POST http://localhost:8000/predict \
  -H 'Content-Type: application/json' \
  -d '{"namespace":"pfe","app":"backend","metrics":{"cpu_usage_percent":85,"memory_usage_percent":70,"pod_restart_count":3,"http_error_rate":0.05},"logs":["ERROR: connection timeout"]}' \
  || echo 'PREDICT_FAILED'""", check=False)
    if "PREDICT_FAILED" in predict or not predict:
        print("  WARNING: /predict check failed — may need extra startup time")
    else:
        print(f"  /predict OK: {predict[:200]}")

    sftp.close()
    client.close()

    print("\n======================================")
    print(" Deployment COMPLETE")
    print("======================================")
    print(f"  AI service : http://{VM_HOST}:8000")
    print(f"  Health     : http://{VM_HOST}:8000/health")
    print(f"  Ollama     : http://{VM_HOST}:11434")
    print(f"  Model      : aiops-sre (fine-tuned Qwen2.5-3B fp16)")


if __name__ == "__main__":
    main()
