import paramiko, sys
key = paramiko.Ed25519Key.from_private_key_file("C:/Users/houss/.ssh/id_ed25519")
def out(s): sys.stdout.buffer.write((s+"\n").encode("utf-8","replace")); sys.stdout.flush()
c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.148.153",username="kube",pkey=key,timeout=10,allow_agent=False,look_for_keys=False)
def run(cmd):
    _,o,_=c.exec_command(cmd,timeout=25); return o.read().decode(errors="replace").strip()
out("=== pods backend (age) ===")
out(run("kubectl get pods -n pfe -l app=backend --no-headers"))
pods=run("kubectl get pods -n pfe -l app=backend -o jsonpath='{.items[*].metadata.name}'").split()
for p in pods:
    out(f"\n=== {p} : polling/discovery/leader ===")
    out(run(f"kubectl logs -n pfe {p} --tail=120 2>/dev/null | grep -iE 'auto-discovery|polling:|isLeader|acquired lease|became leader' | tail -5") or "(rien)")
c.close()
