import paramiko, sys
key = paramiko.Ed25519Key.from_private_key_file("C:/Users/houss/.ssh/id_ed25519")
c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.148.153", username="kube", pkey=key, timeout=10, allow_agent=False, look_for_keys=False)
def run(cmd):
    _, o, _ = c.exec_command(cmd, timeout=20)
    return o.read().decode(errors="replace").strip()

logs = run("kubectl logs -n pfe -l app=backend --tail=60 --prefix 2>/dev/null | grep -iE 'poll|analyzing|target|hasActive|cooldown|leader|podinfo' | tail -20")
sys.stdout.buffer.write((logs + "\n").encode("utf-8", errors="replace"))

sql = "SELECT id, incident_type, status, created_at FROM incident WHERE app='podinfo' ORDER BY id DESC LIMIT 2;"
result = run(f'kubectl exec -n pfe postgres-1 -c postgres -- psql postgresql://appuser:postgres@localhost/pfe_db -c "{sql}"')
sys.stdout.buffer.write(("\nDB:\n" + result + "\n").encode("utf-8", errors="replace"))
c.close()
