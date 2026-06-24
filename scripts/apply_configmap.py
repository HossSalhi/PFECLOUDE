import paramiko, os
key = paramiko.Ed25519Key.from_private_key_file("C:/Users/houss/.ssh/id_ed25519")
MASTER = "192.168.148.153"
BASE = "C:/Users/houss/IdeaProjects/PFE-Orange"

def ssh(host):
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(host, username="kube", pkey=key, timeout=10, allow_agent=False, look_for_keys=False)
    return c

def run(c, cmd, timeout=60):
    _, o, e = c.exec_command(cmd, timeout=timeout)
    out = o.read().decode().strip()
    err = e.read().decode().strip()
    if out: print(" ", out[:300])
    if err and "[sudo]" not in err: print("  ERR:", err[:200])

def scp(host, local, remote):
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(host, username="kube", pkey=key, timeout=10, allow_agent=False, look_for_keys=False)
    sftp = c.open_sftp()
    sftp.put(local, remote)
    sftp.close(); c.close()

print("Applying configmap...")
scp(MASTER, f"{BASE}/backend/backend/infra/k8s/backend-configmap.yaml", "/tmp/backend-configmap.yaml")
c = ssh(MASTER)
run(c, "kubectl apply -f /tmp/backend-configmap.yaml -n pfe")
run(c, "kubectl rollout restart deployment/backend -n pfe")
print("Waiting for rollout...")
run(c, "kubectl rollout status deployment/backend -n pfe --timeout=120s", timeout=130)
c.close()
print("Done.")
