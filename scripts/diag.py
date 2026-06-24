import paramiko
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
    if out: print(out)
    if err and "[sudo]" not in err: print("ERR:", err[:200])

c = ssh("192.168.148.153")
print("=== pods pfe ===")
run(c, "kubectl get pods -n pfe")
print("\n=== backend logs (30 lignes) ===")
run(c, "kubectl logs -n pfe -l app=backend --tail=30 --prefix")
c.close()
