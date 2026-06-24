#!/usr/bin/env python3
"""Deploy pfe-backend:0.2.63 ONLY (import tar on 3 nodes + apply deployment + rollout).
Does NOT touch the frontend (already baked image 1.1.4)."""
import paramiko, os

SSH_USER = "kube"
KEY_PATH = "C:/Users/houss/.ssh/id_ed25519"
SUDO_PASS = "kube"
VER = "0.2.111"

NODES = [
    ("192.168.148.153", "master"),
    ("192.168.148.158", "worker1"),
    ("192.168.148.155", "worker2"),
]
MASTER_IP = "192.168.148.153"
BASE = "C:/Users/houss/IdeaProjects/PFE-Orange"

def client(host):
    key = paramiko.Ed25519Key.from_private_key_file(KEY_PATH)
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(host, username=SSH_USER, pkey=key, timeout=10, allow_agent=False, look_for_keys=False)
    return c

def run(c, cmd, timeout=200, sudo=False):
    if sudo:
        cmd = f"echo '{SUDO_PASS}' | sudo -S {cmd}"
    print(f"  $ {cmd[:110]}")
    stdin, stdout, stderr = c.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode(errors="replace").strip()
    err = stderr.read().decode(errors="replace").strip()
    rc = stdout.channel.recv_exit_status()
    if out: print(f"    {out[:400]}")
    if err and "[sudo]" not in err:
        print(f"    ERR: {err[:300]}")
    return rc, out

def scp_file(host, local, remote):
    print(f"  SCP {os.path.basename(local)} -> {host}:{remote}")
    key = paramiko.Ed25519Key.from_private_key_file(KEY_PATH)
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(host, username=SSH_USER, pkey=key, timeout=10, allow_agent=False, look_for_keys=False)
    sftp = c.open_sftp()
    sftp.put(local, remote)
    sftp.close(); c.close()
    print("  done")

# STEP 1: import backend image on all nodes
print(f"\n== STEP 1: import pfe-backend:{VER} on all nodes ==")
tar = f"{BASE}/pfe-backend-{VER}.tar"
for ip, label in NODES:
    print(f"\n[{label} {ip}]")
    scp_file(ip, tar, f"/tmp/pfe-backend-{VER}.tar")
    c = client(ip)
    rc, _ = run(c, f"ctr -n k8s.io images import /tmp/pfe-backend-{VER}.tar", sudo=True)
    print(f"  import rc={rc}")
    c.close()

# STEP 2: apply rbac + configmap + deployment + rollout
print("\n== STEP 2: apply rbac + configmap + deployment ==")
scp_file(MASTER_IP, f"{BASE}/backend/backend/infra/k8s/backend-rbac.yaml", "/tmp/backend-rbac.yaml")
scp_file(MASTER_IP, f"{BASE}/backend/backend/infra/k8s/backend-configmap.yaml", "/tmp/backend-configmap.yaml")
scp_file(MASTER_IP, f"{BASE}/backend/backend/infra/k8s/backend-deployment.yaml", "/tmp/backend-deployment.yaml")
c = client(MASTER_IP)
run(c, "kubectl apply -f /tmp/backend-rbac.yaml")
run(c, "kubectl apply -f /tmp/backend-configmap.yaml -n pfe")
run(c, "kubectl apply -f /tmp/backend-deployment.yaml -n pfe")
print("  Waiting for rollout (max 4 min)...")
rc, out = run(c, "kubectl rollout status deployment/backend -n pfe --timeout=240s", timeout=260)
print(f"  rollout rc={rc}")
run(c, "kubectl get pods -n pfe -l app=backend -o wide")
c.close()
print("\n== DONE (backend only) ==")
