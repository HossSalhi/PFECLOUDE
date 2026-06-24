import paramiko, sys
key = paramiko.Ed25519Key.from_private_key_file("C:/Users/houss/.ssh/id_ed25519")
c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.148.153", username="kube", pkey=key, timeout=10, allow_agent=False, look_for_keys=False)
def run(cmd):
    _, o, _ = c.exec_command(cmd, timeout=20)
    return o.read().decode(errors="replace").strip()
# Show columns of incident table first
cols = run("kubectl exec -n pfe postgres-1 -c postgres -- psql postgresql://appuser:postgres@localhost/pfe_db -c \"SELECT column_name FROM information_schema.columns WHERE table_name='incident' ORDER BY ordinal_position;\"")
sys.stdout.buffer.write(("COLUMNS:\n"+cols+"\n").encode("utf-8","replace"))
# Show recent incidents full title/description
rows = run("kubectl exec -n pfe postgres-1 -c postgres -- psql postgresql://appuser:postgres@localhost/pfe_db -x -c \"SELECT id, title, incident_type, severity, status FROM incident WHERE app='podinfo' ORDER BY id DESC LIMIT 3;\"")
sys.stdout.buffer.write(("\nRECENT:\n"+rows+"\n").encode("utf-8","replace"))
c.close()
