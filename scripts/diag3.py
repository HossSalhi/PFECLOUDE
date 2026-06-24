import paramiko
key = paramiko.Ed25519Key.from_private_key_file("C:/Users/houss/.ssh/id_ed25519")
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.148.153", username="kube", pkey=key, timeout=10, allow_agent=False, look_for_keys=False)

def run(cmd):
    _, o, _ = c.exec_command(cmd, timeout=30)
    return o.read().decode().strip()

print("=== podinfo replicas ===")
print(run("kubectl get deployment podinfo -n pfe -o jsonpath='{.spec.replicas} replicas, {.status.availableReplicas} available'"))

print("\n=== logs polling bruts (100 lignes) ===")
print(run("kubectl logs -n pfe -l app=backend --tail=100 --prefix 2>/dev/null | grep -iE 'AIOps|poll|podinfo|analyzi|target|incident created|no incident|active AI|hasActive' | tail -30"))

c.close()
