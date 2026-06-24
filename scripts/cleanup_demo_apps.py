import paramiko
key = paramiko.Ed25519Key.from_private_key_file("C:/Users/houss/.ssh/id_ed25519")
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.148.153", username="kube", pkey=key, timeout=10, allow_agent=False, look_for_keys=False)

sql = "UPDATE incident SET status='CLOSED' WHERE app IN ('incident-crashloop','incident-imagepull','incident-pending') AND status NOT IN ('RESOLVED','CLOSED'); SELECT app, count(*) FROM incident WHERE app IN ('incident-crashloop','incident-imagepull','incident-pending') AND status NOT IN ('RESOLVED','CLOSED') GROUP BY app;"
cmd = f"kubectl exec -n pfe postgres-1 -c postgres -- psql postgresql://appuser:postgres@localhost/pfe_db -c \"{sql}\""
_, o, _ = c.exec_command(cmd, timeout=30)
print(o.read().decode())
c.close()
