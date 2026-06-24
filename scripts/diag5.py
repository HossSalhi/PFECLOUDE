import paramiko
key = paramiko.Ed25519Key.from_private_key_file("C:/Users/houss/.ssh/id_ed25519")
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.148.153", username="kube", pkey=key, timeout=10, allow_agent=False, look_for_keys=False)

def run(cmd):
    _, o, _ = c.exec_command(cmd, timeout=30)
    return o.read().decode().strip()

# Logs 200 lignes des 2 pods combinés
print("=== logs combinés 200 lignes (grep poll/leader/error) ===")
print(run("kubectl logs -n pfe -l app=backend --tail=200 --prefix 2>/dev/null | grep -vE 'at org\\.|at com\\.|at java\\.' | grep -iE 'leader|poll|error|Exception|schedul|aiops' | tail -30"))

print("\n=== lease kubernetes (leader election) ===")
print(run("kubectl get lease -n pfe"))

c.close()
