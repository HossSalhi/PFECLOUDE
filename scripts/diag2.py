import paramiko
key = paramiko.Ed25519Key.from_private_key_file("C:/Users/houss/.ssh/id_ed25519")
def ssh(host):
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(host, username="kube", pkey=key, timeout=10, allow_agent=False, look_for_keys=False)
    return c

def run(c, cmd):
    _, o, _ = c.exec_command(cmd, timeout=30)
    return o.read().decode().strip()

c = ssh("192.168.148.153")

print("=== incidents ouverts podinfo ===")
sql = "SELECT id, app, incident_type, status, created_at FROM incident WHERE app='podinfo' AND status NOT IN ('RESOLVED','CLOSED') ORDER BY id DESC LIMIT 5;"
print(run(c, f"kubectl exec -n pfe postgres-1 -c postgres -- psql postgresql://appuser:postgres@localhost/pfe_db -c \"{sql}\""))

print("\n=== tous incidents podinfo recents ===")
sql2 = "SELECT id, app, incident_type, status, created_at FROM incident WHERE app='podinfo' ORDER BY id DESC LIMIT 5;"
print(run(c, f"kubectl exec -n pfe postgres-1 -c postgres -- psql postgresql://appuser:postgres@localhost/pfe_db -c \"{sql2}\""))

print("\n=== logs polling podinfo (derniere minute) ===")
print(run(c, "kubectl logs -n pfe -l app=backend --tail=100 --prefix 2>/dev/null | grep -i 'podinfo\\|poll\\|cooldown\\|RESOLUTION_FAILED\\|skip\\|active' | tail -20"))

c.close()
