import paramiko, sys
key = paramiko.Ed25519Key.from_private_key_file("C:/Users/houss/.ssh/id_ed25519")
c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.148.153",username="kube",pkey=key,timeout=10,allow_agent=False,look_for_keys=False)
def run(cmd):
    _,o,_=c.exec_command(cmd,timeout=25); return o.read().decode(errors="replace").strip()
pods = run("kubectl get pods -n pfe -l app=backend -o jsonpath='{.items[*].metadata.name}'").split()
for p in pods:
    logs = run(f"kubectl logs -n pfe {p} --tail=300 2>/dev/null | grep -iE 'Infrastructure|leader|VM health' | tail -6")
    sys.stdout.buffer.write((f"\n=== {p} ===\n"+(logs if logs else "(rien)")+"\n").encode("utf-8","replace"))
c.close()
