import paramiko
key = paramiko.Ed25519Key.from_private_key_file("C:/Users/houss/.ssh/id_ed25519")
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.148.153", username="kube", pkey=key, timeout=10, allow_agent=False, look_for_keys=False)

print("=== pods podinfo ===")
_, o, _ = c.exec_command("kubectl get pods -n pfe -l app=podinfo")
print(o.read().decode())

print("=== backend logs (50 lignes) ===")
_, o, _ = c.exec_command("kubectl logs -n pfe -l app=backend --tail=50 --prefix 2>/dev/null | grep -E 'poll|incident|podinfo|RESOLUTION|error|ERROR|skip|active' | tail -40")
print(o.read().decode())
c.close()
