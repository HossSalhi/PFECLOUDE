import paramiko, time
key = paramiko.Ed25519Key.from_private_key_file("C:/Users/houss/.ssh/id_ed25519")

def ssh(host):
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(host, username="kube", pkey=key, timeout=10, allow_agent=False, look_for_keys=False)
    return c

def run(c, cmd):
    _, o, e = c.exec_command(cmd, timeout=30)
    out = o.read().decode().strip()
    err = e.read().decode().strip()
    if out: print(f"  {out[:300]}")
    if err and "[sudo]" not in err: print(f"  ERR: {err[:200]}")
    return out

c = ssh("192.168.148.153")

print("=== Avant : replicas podinfo ===")
run(c, "kubectl get deployment podinfo -n pfe")

print("\n=== Scale podinfo -> 0 ===")
run(c, "kubectl scale deployment podinfo -n pfe --replicas=0")

print("\n=== Apres : replicas podinfo ===")
run(c, "kubectl get deployment podinfo -n pfe")
run(c, "kubectl get pods -n pfe -l app=podinfo")

c.close()
print("\nTest A1 declenche — attends 30-90s puis verifie l'UI")
