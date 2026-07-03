"""Baseline avant le test IA-down : etat cluster + apps demo + dernier incident."""
import paramiko, sys
KEY="C:/Users/houss/.ssh/id_ed25519"; MASTER="192.168.148.153"
def out(s): sys.stdout.buffer.write((s+"\n").encode("utf-8","replace")); sys.stdout.flush()
def cli(h):
    k=paramiko.Ed25519Key.from_private_key_file(KEY); c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(h,username="kube",pkey=k,timeout=12,allow_agent=False,look_for_keys=False); return c
def run(c,cmd,t=60): _,o,e=c.exec_command(cmd,timeout=t); return (o.read().decode(errors="replace")+e.read().decode(errors="replace")).strip()
m=cli(MASTER)
out("[nodes]"); out(run(m,"kubectl get nodes --no-headers"))
out("\n[helios-crashloop deploy present ?]"); out(run(m,"kubectl -n pfe get deploy helios-crashloop --no-headers 2>&1"))
out("\n[derniers incidents]")
prim=run(m,"kubectl get pods -n pfe -l cnpg.io/instanceRole=primary -o jsonpath='{.items[0].metadata.name}'")
out(f"primaire={prim}")
out(run(m,f"kubectl exec -n pfe {prim} -c postgres -- psql -U postgres -d pfe_db -t -c \"SELECT id,app,incident_type,status FROM incident ORDER BY id DESC LIMIT 6\" 2>&1"))
out("\n[IA /health]"); out(run(m,"curl -s -o /dev/null -w '%{http_code}' http://192.168.148.161:8000/health 2>&1 || echo curl-fail"))
m.close()
