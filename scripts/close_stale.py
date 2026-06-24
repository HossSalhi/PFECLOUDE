import paramiko
key = paramiko.Ed25519Key.from_private_key_file("C:/Users/houss/.ssh/id_ed25519")
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.148.153", username="kube", pkey=key, timeout=10, allow_agent=False, look_for_keys=False)

# Close all RESOLUTION_FAILED incidents so dedup constraint doesn't block new ones
sql = "UPDATE incident SET status='CLOSED' WHERE status='RESOLUTION_FAILED'; SELECT id, app, incident_type, status FROM incident WHERE id IN (3256,3236,3129);"
cmd = f"kubectl exec -n pfe postgres-1 -c postgres -- psql postgresql://appuser:postgres@localhost/pfe_db -c \"{sql}\""
_, o, e = c.exec_command(cmd, timeout=30)
print(o.read().decode())
err = e.read().decode()
if err and "Defaulted" not in err: print("ERR:", err[:200])
c.close()
