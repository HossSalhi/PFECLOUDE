#!/usr/bin/env python3
"""Deploy pfe-backend:0.2.62 and frontend to the cluster."""
import paramiko
import os, sys

SSH_USER = "kube"
KEY_PATH = "C:/Users/houss/.ssh/id_ed25519"
SUDO_PASS = "kube"

NODES = [
    ("192.168.148.153", "master"),
    ("192.168.148.158", "worker1"),
    ("192.168.148.155", "worker2"),
]
MASTER_IP = "192.168.148.153"

def client(host):
    key = paramiko.Ed25519Key.from_private_key_file(KEY_PATH)
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(host, username=SSH_USER, pkey=key, timeout=10, allow_agent=False, look_for_keys=False)
    return c

def run(c, cmd, timeout=120, sudo=False):
    if sudo:
        cmd = f"echo '{SUDO_PASS}' | sudo -S {cmd}"
    print(f"  $ {cmd[:100]}")
    stdin, stdout, stderr = c.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode(errors="replace").strip()
    err = stderr.read().decode(errors="replace").strip()
    rc = stdout.channel.recv_exit_status()
    if out: print(f"    {out[:300]}")
    if err and "[sudo]" not in err:
        print(f"    ERR: {err[:200]}")
    return rc, out

def scp_file(host, local, remote):
    print(f"  SCP {os.path.basename(local)} -> {host}:{remote}")
    key = paramiko.Ed25519Key.from_private_key_file(KEY_PATH)
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(host, username=SSH_USER, pkey=key, timeout=10, allow_agent=False, look_for_keys=False)
    sftp = c.open_sftp()
    sftp.put(local, remote)
    sftp.close()
    c.close()
    print("  done")

def scp_dir(host, local_dir, remote_dir):
    print(f"  SCP dir -> {host}:{remote_dir}")
    key = paramiko.Ed25519Key.from_private_key_file(KEY_PATH)
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(host, username=SSH_USER, pkey=key, timeout=10, allow_agent=False, look_for_keys=False)
    sftp = c.open_sftp()

    def upload_dir(local, remote):
        try: sftp.mkdir(remote)
        except: pass
        for item in os.listdir(local):
            lp = os.path.join(local, item)
            rp = f"{remote}/{item}"
            if os.path.isfile(lp):
                sftp.put(lp, rp)
            elif os.path.isdir(lp):
                upload_dir(lp, rp)

    upload_dir(local_dir, remote_dir)
    sftp.close()
    c.close()
    print("  done")

BASE = "C:/Users/houss/IdeaProjects/PFE-Orange"

# ── STEP 1: import backend image on all nodes ──────────────────────────────
print("\n== STEP 1: import pfe-backend:0.2.62 on all nodes ==")
tar = f"{BASE}/pfe-backend-0.2.62.tar"
for ip, label in NODES:
    print(f"\n[{label} {ip}]")
    scp_file(ip, tar, "/tmp/pfe-backend-0.2.62.tar")
    c = client(ip)
    rc, _ = run(c, "ctr -n k8s.io images import /tmp/pfe-backend-0.2.62.tar", timeout=120, sudo=True)
    print(f"  import rc={rc}")
    c.close()

# ── STEP 2: apply configmap + deployment ──────────────────────────────────
print("\n== STEP 2: apply configmap + deployment ==")
scp_file(MASTER_IP,
    f"{BASE}/backend/backend/infra/k8s/backend-configmap.yaml",
    "/tmp/backend-configmap.yaml")
scp_file(MASTER_IP,
    f"{BASE}/backend/backend/infra/k8s/backend-deployment.yaml",
    "/tmp/backend-deployment.yaml")

c = client(MASTER_IP)
run(c, "kubectl apply -f /tmp/backend-configmap.yaml -n pfe")
run(c, "kubectl apply -f /tmp/backend-deployment.yaml -n pfe")
print("  Waiting for rollout (max 3 min)...")
rc, out = run(c, "kubectl rollout status deployment/backend -n pfe --timeout=180s", timeout=200)
print(f"  rollout rc={rc}")
c.close()

# ── STEP 3: deploy frontend ────────────────────────────────────────────────
print("\n== STEP 3: deploy frontend ==")
fe_dist = f"{BASE}/frontend/dist/frontend"
c = client(MASTER_IP)
run(c, "rm -rf /tmp/fe-new && mkdir -p /tmp/fe-new")
c.close()
scp_dir(MASTER_IP, fe_dist, "/tmp/fe-new")

c = client(MASTER_IP)
rc, pod = run(c, "kubectl get pod -n pfe -l app=frontend -o jsonpath='{.items[0].metadata.name}'")
pod = pod.strip().strip("'")
print(f"  Frontend pod: {pod}")
if pod:
    run(c, f"kubectl exec -n pfe {pod} -- sh -c 'rm -rf /usr/share/nginx/html/*'")
    run(c, f"kubectl cp /tmp/fe-new/. pfe/{pod}:/usr/share/nginx/html/")
    rc2, ls = run(c, f"kubectl exec -n pfe {pod} -- ls /usr/share/nginx/html/")
    print(f"  nginx html: {ls}")
c.close()

print("\n== DONE ==")
