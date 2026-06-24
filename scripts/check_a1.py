import paramiko
key = paramiko.Ed25519Key.from_private_key_file("C:/Users/houss/.ssh/id_ed25519")
c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.148.153", username="kube", pkey=key, timeout=10, allow_agent=False, look_for_keys=False)
def run(cmd):
    _, o, _ = c.exec_command(cmd, timeout=15)
    return o.read().decode().strip()
print("podinfo:", run("kubectl get deployment podinfo -n pfe"))
sql = "SELECT id, app, incident_type, status, created_at, updated_at FROM incident WHERE app='podinfo' ORDER BY id DESC LIMIT 4;"
print("incidents:", run(f'kubectl exec -n pfe postgres-1 -c postgres -- psql postgresql://appuser:postgres@localhost/pfe_db -c "{sql}"'))
c.close()
