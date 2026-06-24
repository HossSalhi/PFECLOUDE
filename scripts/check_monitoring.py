import paramiko, sys
key = paramiko.Ed25519Key.from_private_key_file("C:/Users/houss/.ssh/id_ed25519")
def out(s): sys.stdout.buffer.write((s+"\n").encode("utf-8","replace")); sys.stdout.flush()
c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.148.153",username="kube",pkey=key,timeout=10,allow_agent=False,look_for_keys=False)
def run(cmd):
    _,o,_=c.exec_command(cmd,timeout=25); return o.read().decode(errors="replace").strip()
out("=== HELIOS surveille-t-il (dernier polling) ? ===")
out(run("kubectl logs -n pfe -l app=backend --tail=120 2>/dev/null | grep -iE 'AIOps polling:|Infrastructure check:' | tail -4") or "(pas de polling recent)")
out("\n=== incidents en base ===")
out(run("kubectl exec -n pfe postgres-2 -c postgres -- psql postgresql://appuser:postgres@localhost/pfe_db -t -c \"SELECT count(*) FROM incident;\""))
out("\n=== podinfo (cible surveillee) ===")
out(run("kubectl get deploy podinfo -n pfe --no-headers"))
c.close()
