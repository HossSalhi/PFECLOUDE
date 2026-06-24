import paramiko, os
key = paramiko.Ed25519Key.from_private_key_file("C:/Users/houss/.ssh/id_ed25519")

def client(host):
    c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(host, username="kube", pkey=key, timeout=10, allow_agent=False, look_for_keys=False)
    return c

def run(c, cmd, timeout=30):
    _, o, e = c.exec_command(cmd, timeout=timeout)
    out = o.read().decode().strip()
    err = e.read().decode().strip()
    if out: print(f"  {out[:300]}")
    if err and "[sudo]" not in err: print(f"  ERR: {err[:200]}")
    return out

BASE = "C:/Users/houss/IdeaProjects/PFE-Orange"
MASTER = "192.168.148.153"

print("== Apply updated configmap ==")
# SCP
c = client(MASTER)
sftp = c.open_sftp()
sftp.put(f"{BASE}/backend/backend/infra/k8s/backend-configmap.yaml", "/tmp/backend-configmap-v2.yaml")
sftp.close()
run(c, "kubectl apply -f /tmp/backend-configmap-v2.yaml -n pfe")

# Restart backend pods to pick up new env vars
run(c, "kubectl rollout restart deployment/backend -n pfe")
print("  Waiting for rollout...")
run(c, "kubectl rollout status deployment/backend -n pfe --timeout=120s", timeout=130)

# Verify
pod = run(c, "kubectl get pod -n pfe -l app=backend -o jsonpath='{.items[0].metadata.name}'")
if pod:
    env = run(c, f"kubectl exec -n pfe {pod} -- env 2>/dev/null | grep -E 'REMEDIATION_VERIFY'")
    print("New verify timing:", env)

c.close()
print("Done.")
