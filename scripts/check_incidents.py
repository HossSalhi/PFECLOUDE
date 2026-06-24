import paramiko
key = paramiko.Ed25519Key.from_private_key_file("C:/Users/houss/.ssh/id_ed25519")
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.148.153", username="kube", pkey=key, timeout=10, allow_agent=False, look_for_keys=False)

# Use the app user via TCP connection
cmd = (
    "kubectl exec -n pfe postgres-1 -c postgres -- "
    "psql postgresql://appuser:postgres@localhost/pfe_db "
    "-c \"SELECT id, app, incident_type, status, created_at FROM incident "
    "WHERE status NOT IN ('RESOLVED','CLOSED') ORDER BY id DESC LIMIT 20;\""
)
_, o, e = c.exec_command(cmd, timeout=30)
out = o.read().decode()
err = e.read().decode()
print(out if out else "(no output)")
if err: print("STDERR:", err[:300])
c.close()
