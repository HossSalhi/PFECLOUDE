import paramiko
key = paramiko.Ed25519Key.from_private_key_file("C:/Users/houss/.ssh/id_ed25519")
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.148.153", username="kube", pkey=key, timeout=10, allow_agent=False, look_for_keys=False)
def run(cmd):
    _, o, _ = c.exec_command(cmd, timeout=30)
    return o.read().decode().strip()

print("=== podinfo replicas ===")
print(run("kubectl get deployment podinfo -n pfe"))

print("\n=== incidents podinfo ouverts ===")
sql = "SELECT id, app, incident_type, status, created_at FROM incident WHERE app='podinfo' AND status NOT IN ('RESOLVED','CLOSED') ORDER BY id DESC LIMIT 3;"
print(run(f"kubectl exec -n pfe postgres-1 -c postgres -- psql postgresql://appuser:postgres@localhost/pfe_db -c \"{sql}\""))

print("\n=== logs polling (derniers) ===")
print(run("kubectl logs -n pfe -l app=backend --tail=80 --prefix 2>/dev/null | grep -iE 'poll|podinfo|target|analyzing|incident created|no incident|cooldown|hasActive|RESOLVED|leader' | tail -20"))
c.close()
