import paramiko, sys
key = paramiko.Ed25519Key.from_private_key_file("C:/Users/houss/.ssh/id_ed25519")
def out(s): sys.stdout.buffer.write((s+"\n").encode("utf-8","replace")); sys.stdout.flush()
c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.148.153",username="kube",pkey=key,timeout=10,allow_agent=False,look_for_keys=False)
def run(cmd):
    _,o,_=c.exec_command(cmd,timeout=25); return o.read().decode(errors="replace").strip()
out("=== podinfo pods (RESTARTS ?) ===")
out(run("kubectl get pods -n pfe -l app=podinfo"))
out("\n=== dernieres analyses podinfo (verdict IA) ===")
out(run("kubectl logs -n pfe -l app=backend --tail=150 2>/dev/null | grep -iE 'analyzing namespace=pfe app=podinfo|AI prediction|no incident|incident created|pod_restarts' | tail -8") or "(pas d'analyse recente)")
out("\n=== incidents ===")
out(run("kubectl exec -n pfe postgres-2 -c postgres -- psql postgresql://appuser:postgres@localhost/pfe_db -t -c \"SELECT id,incident_type,status,created_at FROM incident ORDER BY id DESC LIMIT 3;\"") or "(0 incident)")
c.close()
