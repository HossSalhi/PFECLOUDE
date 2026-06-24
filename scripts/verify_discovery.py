import paramiko, sys
key = paramiko.Ed25519Key.from_private_key_file("C:/Users/houss/.ssh/id_ed25519")
def out(s): sys.stdout.buffer.write((s+"\n").encode("utf-8","replace")); sys.stdout.flush()
c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.148.153",username="kube",pkey=key,timeout=10,allow_agent=False,look_for_keys=False)
def run(cmd):
    _,o,_=c.exec_command(cmd,timeout=25); return o.read().decode(errors="replace").strip()
out("=== log auto-discovery (cibles trouvees) ===")
out(run("kubectl logs -n pfe -l app=backend --tail=200 2>/dev/null | grep -iE 'auto-discovery found|polling:.*target' | tail -5") or "(pas encore - leader election en cours)")
out("\n=== incidents recents pour apps demo (doit etre vide/ancien) ===")
sql="SELECT app, count(*) FROM incident WHERE app IN ('incident-crashloop','incident-imagepull','incident-pending') AND created_at > now() - interval '5 minutes' GROUP BY app;"
out(run(f"kubectl exec -n pfe postgres-2 -c postgres -- psql postgresql://appuser:postgres@localhost/pfe_db -t -c \"{sql}\"") or "(aucun incident demo recent = OK)")
c.close()
