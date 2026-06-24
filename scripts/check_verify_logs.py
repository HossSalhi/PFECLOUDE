import paramiko
key = paramiko.Ed25519Key.from_private_key_file("C:/Users/houss/.ssh/id_ed25519")
c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.148.153", username="kube", pkey=key, timeout=10, allow_agent=False, look_for_keys=False)
def run(cmd):
    _, o, _ = c.exec_command(cmd, timeout=20)
    return o.read().decode(errors="replace").strip()

# Get backend logs for verification-related messages
logs = run("kubectl logs -n pfe -l app=backend --tail=80 --prefix 2>/dev/null | grep -iE 'verif|resolve|resolved|RESOLVED|autoRes|workload|aiStill|attempt|podinfo' | tail -30")
print("=== Backend verification logs ===")
print(logs if logs else "(no matching log lines)")

# Also check current status
sql = "SELECT id, incident_type, status, updated_at FROM incident WHERE app='podinfo' ORDER BY id DESC LIMIT 2;"
print("\n=== DB state ===")
print(run(f'kubectl exec -n pfe postgres-1 -c postgres -- psql postgresql://appuser:postgres@localhost/pfe_db -c "{sql}"'))
c.close()
