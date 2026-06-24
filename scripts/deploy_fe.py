#!/usr/bin/env python3
"""Deploy frontend via /proc/PID/root on the correct node."""
import paramiko, os

SSH_USER = "kube"
KEY_PATH  = "C:/Users/houss/.ssh/id_ed25519"
SUDO_PASS = "kube"
MASTER_IP = "192.168.148.153"
BASE      = "C:/Users/houss/IdeaProjects/PFE-Orange"

def client(host):
    key = paramiko.Ed25519Key.from_private_key_file(KEY_PATH)
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(host, username=SSH_USER, pkey=key, timeout=10,
              allow_agent=False, look_for_keys=False)
    return c

def run(c, cmd, timeout=60, sudo=False):
    actual = f"echo {SUDO_PASS} | sudo -S {cmd}" if sudo else cmd
    print(f"  $ {cmd[:100]}")
    _, stdout, stderr = c.exec_command(actual, timeout=timeout)
    out = stdout.read().decode(errors="replace").strip()
    err = stderr.read().decode(errors="replace").strip()
    rc  = stdout.channel.recv_exit_status()
    if out: print(f"    {out[:400]}")
    if err and "[sudo]" not in err: print(f"    STDERR: {err[:200]}")
    return rc, out

def scp_dir(host, local_dir, remote_dir):
    print(f"  SCP {local_dir} -> {host}:{remote_dir}")
    key = paramiko.Ed25519Key.from_private_key_file(KEY_PATH)
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(host, username=SSH_USER, pkey=key, timeout=10,
              allow_agent=False, look_for_keys=False)
    sftp = c.open_sftp()
    def up(local, remote):
        try: sftp.mkdir(remote)
        except: pass
        for item in os.listdir(local):
            lp = os.path.join(local, item)
            rp = f"{remote}/{item}"
            if os.path.isfile(lp): sftp.put(lp, rp)
            elif os.path.isdir(lp): up(lp, rp)
    up(local_dir, remote_dir)
    sftp.close(); c.close()
    print("  done")

# ── 1. Find pod + node ────────────────────────────────────────────────────
print("== Find frontend pod ==")
c = client(MASTER_IP)
_, info = run(c, "kubectl get pod -n pfe -l app=frontend "
                 "-o jsonpath='{.items[0].metadata.name} {.items[0].spec.nodeName}'")
c.close()
parts    = info.strip().strip("'").split()
pod_name = parts[0]
node_name= parts[1] if len(parts) > 1 else ""
print(f"  pod={pod_name}  node={node_name}")

# ── 2. Resolve node IP ────────────────────────────────────────────────────
c = client(MASTER_IP)
_, ip_out = run(c, f"kubectl get node {node_name} "
                   "-o jsonpath='{.status.addresses[?(@.type==\"InternalIP\")].address}'")
c.close()
node_ip = ip_out.strip().strip("'") or MASTER_IP
print(f"  node_ip={node_ip}")

# ── 3. SCP fe-new to the node ─────────────────────────────────────────────
print("== SCP frontend files ==")
c_node = client(node_ip)
run(c_node, "rm -rf /tmp/fe-new && mkdir -p /tmp/fe-new")
c_node.close()
scp_dir(node_ip, f"{BASE}/frontend/dist/frontend", "/tmp/fe-new")

# ── 4. Get container PID via crictl ───────────────────────────────────────
print("== Get container PID ==")
c_node = client(node_ip)
# crictl ps gives container id; crictl inspect gives pid
_, cid = run(c_node, "crictl ps --name=frontend -q 2>/dev/null | head -1", sudo=True)
cid = cid.strip()
print(f"  container id: {cid}")

if cid:
    _, pid = run(c_node, f"crictl inspect {cid} 2>/dev/null | "
                          "python3 -c \"import sys,json; d=json.load(sys.stdin); "
                          "print(d['info']['pid'])\"", sudo=True)
    pid = pid.strip()
    print(f"  PID: {pid}")
    if pid and pid.isdigit():
        run(c_node, f"cp -af /tmp/fe-new/. /proc/{pid}/root/usr/share/nginx/html/", sudo=True)
        _, ls = run(c_node, f"ls /proc/{pid}/root/usr/share/nginx/html/", sudo=True)
        print(f"  html/: {ls}")
    else:
        print("  PID parse failed")
else:
    print("  container not found via crictl — trying kubectl cp from master")
    c2 = client(MASTER_IP)
    # kubectl cp without timeout (run async, wait longer)
    print(f"  $ kubectl cp /tmp/fe-new/. pfe/{pod_name}:/usr/share/nginx/html/")
    _, stdout, stderr = c2.exec_command(
        f"kubectl cp /tmp/fe-new/. pfe/{pod_name}:/usr/share/nginx/html/", timeout=300)
    out = stdout.read().decode(errors="replace").strip()
    stderr.read()
    rc = stdout.channel.recv_exit_status()
    if out: print(f"    {out}")
    print(f"    kubectl cp rc={rc}")
    c2.close()

c_node.close()
print("\n== DONE ==")
