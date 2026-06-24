import paramiko, sys
key = paramiko.Ed25519Key.from_private_key_file("C:/Users/houss/.ssh/id_ed25519")
c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.148.153",username="kube",pkey=key,timeout=10,allow_agent=False,look_for_keys=False)
def run(cmd):
    _,o,_=c.exec_command(cmd,timeout=25); return o.read().decode(errors="replace").strip()
def out(s): sys.stdout.buffer.write((s+"\n").encode("utf-8","replace"))
out("=== resultat check infra (leader) ===")
out(run("kubectl logs -n pfe -l app=backend --tail=300 2>/dev/null | grep -iE 'Infrastructure check:|VM health|component.* DOWN' | tail -8"))
out("\n=== incidents VM crees ? ===")
sql="SELECT namespace, app, status FROM incident WHERE namespace LIKE 'vm-%' ORDER BY id DESC LIMIT 8;"
out(run(f"kubectl exec -n pfe postgres-2 -c postgres -- psql postgresql://appuser:postgres@localhost/pfe_db -t -c \"{sql}\""))
c.close()
