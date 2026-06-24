import paramiko
key = paramiko.Ed25519Key.from_private_key_file("C:/Users/houss/.ssh/id_ed25519")
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.148.153", username="kube", pkey=key, timeout=10, allow_agent=False, look_for_keys=False)

def run(cmd):
    _, o, _ = c.exec_command(cmd, timeout=30)
    return o.read().decode().strip()

# Logs bruts sans filtre des 2 pods
print("=== backend pod 1 (50 lignes brutes) ===")
_, pods, _ = c.exec_command("kubectl get pods -n pfe -l app=backend -o jsonpath='{.items[0].metadata.name}'", timeout=10)
pod1 = pods.read().decode().strip().strip("'")
print(f"Pod: {pod1}")
print(run(f"kubectl logs -n pfe {pod1} --tail=50 2>/dev/null | grep -vE 'at org\\.|at com\\.|at java\\.|at sun\\.' | tail -30"))

c.close()
