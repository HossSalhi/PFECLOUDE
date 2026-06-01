"""Create/update Ollama aiops-sre model on VM from fp16 model dir."""
from __future__ import annotations
import sys, os
os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONUNBUFFERED", "1")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)

import paramiko

VM_HOST = "192.168.148.161"
VM_USER = "kube"
VM_PASS = "kube"
REMOTE_MODEL_DIR = "/home/kube/aiops-sre-fp16"

MODELFILE = f"""FROM {REMOTE_MODEL_DIR}
PARAMETER temperature 0
PARAMETER num_predict 400
PARAMETER stop "</s>"
SYSTEM """  + '"""' + """You are an expert SRE AI assistant. Analyze the provided metrics and logs, then respond ONLY with a valid JSON object with exactly these fields: incident (bool), incident_type (string), severity (string: LOW/MEDIUM/HIGH/CRITICAL), confidence (float 0-1), recommended_action (string), auto_execute (bool), explanation (string).""" + '"""'

def main():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(VM_HOST, username=VM_USER, password=VM_PASS,
              timeout=15, look_for_keys=False, allow_agent=False)
    print(f"Connected to {VM_HOST}")

    # Write Modelfile via stdin
    chan = c.get_transport().open_session()
    chan.exec_command("cat > /tmp/Modelfile-aiops-sre")
    chan.sendall(MODELFILE.encode("utf-8"))
    chan.shutdown_write()
    chan.recv_exit_status()
    print("Modelfile written to /tmp/Modelfile-aiops-sre")

    # Verify the modelfile
    _, out, _ = c.exec_command("head -3 /tmp/Modelfile-aiops-sre")
    print("Modelfile preview:", out.read().decode().strip())

    # Run ollama create (replaces old aiops-sre model)
    print("\nRunning: ollama create aiops-sre -f /tmp/Modelfile-aiops-sre")
    print("(Converting fp16 safetensors -> GGUF format, takes ~5-10 min...)\n")
    _, out, err = c.exec_command(
        "ollama create aiops-sre -f /tmp/Modelfile-aiops-sre 2>&1",
        timeout=600
    )
    for line in out:
        print(line.rstrip())
    code = out.channel.recv_exit_status()
    print(f"\nollama create exit code: {code}")

    # Show updated model list
    _, out2, _ = c.exec_command("ollama list")
    print("\nOllama models now:")
    print(out2.read().decode().strip())

    # Quick predict test
    print("\nRunning predict smoke test...")
    payload = '{"namespace":"pfe","app":"backend","metrics":{"cpu_usage_percent":85,"memory_usage_percent":70,"pod_restart_count":3,"http_error_rate":0.05},"logs":["ERROR: connection timeout"]}'
    _, out3, _ = c.exec_command(
        f"curl -sf --max-time 30 -X POST http://localhost:8000/predict -H 'Content-Type: application/json' -d '{payload}' 2>/dev/null || echo PREDICT_FAILED",
        timeout=45
    )
    resp = out3.read().decode().strip()
    print(f"Predict response: {resp[:300]}")

    c.close()
    print("\nDone!")

if __name__ == "__main__":
    main()
