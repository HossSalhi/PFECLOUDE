"""Free disk space on VM and retry ollama create aiops-sre from fp16 model."""
from __future__ import annotations
import sys, os
os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONUNBUFFERED", "1")
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

import paramiko

VM_HOST = "192.168.148.161"
VM_USER = "kube"
VM_PASS = "kube"
REMOTE_MODEL_DIR = "/home/kube/aiops-sre-fp16"

MODELFILE = (
    f"FROM {REMOTE_MODEL_DIR}\n"
    "PARAMETER temperature 0\n"
    "PARAMETER num_predict 400\n"
    'PARAMETER stop "</s>"\n'
    'SYSTEM """You are an expert SRE AI assistant. Analyze the provided metrics and logs, '
    "then respond ONLY with a valid JSON object with exactly these fields: incident (bool), "
    "incident_type (string), severity (string: LOW/MEDIUM/HIGH/CRITICAL), confidence (float 0-1), "
    'recommended_action (string), auto_execute (bool), explanation (string)."""\n'
)


def run(c: paramiko.SSHClient, cmd: str, timeout: int = 60, check: bool = False) -> str:
    _, out, err = c.exec_command(cmd, timeout=timeout)
    result = out.read().decode("utf-8", errors="replace").strip()
    code   = out.channel.recv_exit_status()
    print(f"  $ {cmd[:70]}")
    if result:
        print(f"    {result[:300]}")
    if check and code != 0:
        raise RuntimeError(f"exit {code}")
    return result


def main():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(VM_HOST, username=VM_USER, password=VM_PASS,
              timeout=15, look_for_keys=False, allow_agent=False)
    print(f"Connected to {VM_HOST}")

    print("\n[1/4] Checking disk...")
    run(c, "df -h / | tail -1")

    print("\n[2/4] Removing old Ollama models to free space...")
    run(c, "ollama rm aiops-sre 2>/dev/null || true")
    run(c, "ollama rm qwen2.5:3b 2>/dev/null || true")
    run(c, "df -h / | tail -1")

    print("\n[3/4] Creating aiops-sre model from fp16...")
    # Write Modelfile
    chan = c.get_transport().open_session()
    chan.exec_command("cat > /tmp/Modelfile-aiops-sre")
    chan.sendall(MODELFILE.encode("utf-8"))
    chan.shutdown_write()
    chan.recv_exit_status()
    print("  Modelfile written.")

    # Run ollama create — this converts fp16 -> GGUF (~5 min)
    print("  Running ollama create (fp16 -> GGUF conversion)...")
    _, out, _ = c.exec_command(
        "ollama create aiops-sre -f /tmp/Modelfile-aiops-sre 2>&1",
        timeout=600,
    )
    for raw_line in out:
        # Strip ANSI escape codes
        import re
        clean = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]|\x1b\[\?[0-9;]*[a-zA-Z]', '', raw_line)
        clean = clean.strip()
        if clean and ('100%' in clean or 'Error' in clean or 'success' in clean.lower()
                      or 'copying' in clean):
            print(f"  {clean}")
    code = out.channel.recv_exit_status()
    print(f"  ollama create exit code: {code}")

    if code != 0:
        print("  ERROR: ollama create failed. Check disk space.")
        run(c, "df -h / | tail -1")
        c.close()
        sys.exit(1)

    print("\n[4/4] Verifying and smoke test...")
    run(c, "ollama list")
    run(c, "df -h / | tail -1")

    # Delete fp16 source to reclaim 4 GB
    print("\n  Deleting fp16 source dir (4 GB freed after model is in Ollama)...")
    run(c, f"rm -rf {REMOTE_MODEL_DIR}")
    run(c, "df -h / | tail -1")

    # Predict test
    payload = ('{"namespace":"pfe","app":"backend","metrics":{"cpu_usage_percent":85,'
               '"memory_usage_percent":70,"pod_restart_count":3,"http_error_rate":0.05},'
               '"logs":["ERROR: connection timeout"]}')
    run(c,
        f"curl -sf --max-time 30 -X POST http://localhost:8000/predict "
        f"-H 'Content-Type: application/json' -d '{payload}' 2>/dev/null || echo PREDICT_FAILED",
        timeout=45)

    c.close()
    print("\n=== DEPLOYMENT COMPLETE ===")
    print(f"  aiops-sre model (fine-tuned fp16) is live on {VM_HOST}")
    print(f"  AI service: http://{VM_HOST}:8000/health")


if __name__ == "__main__":
    main()
