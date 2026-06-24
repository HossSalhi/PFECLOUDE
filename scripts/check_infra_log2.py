import paramiko, sys
key = paramiko.Ed25519Key.from_private_key_file("C:/Users/houss/.ssh/id_ed25519")
c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.148.153",username="kube",pkey=key,timeout=10,allow_agent=False,look_for_keys=False)
def run(cmd):
    _,o,_=c.exec_command(cmd,timeout=25); return o.read().decode(errors="replace").strip()
logs = run("kubectl logs -n pfe -l app=backend --tail=200 --prefix 2>/dev/null | grep -iE 'Infrastructure check|VM health|component' | tail -12")
sys.stdout.buffer.write(("INFRA LOGS:\n"+(logs if logs else "(rien encore - scheduler pas encore execute)")+"\n").encode("utf-8","replace"))
c.close()
