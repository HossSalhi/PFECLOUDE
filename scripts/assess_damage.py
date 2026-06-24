import paramiko, sys
key = paramiko.Ed25519Key.from_private_key_file("C:/Users/houss/.ssh/id_ed25519")
def out(s): sys.stdout.buffer.write((s+"\n").encode("utf-8","replace")); sys.stdout.flush()
c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.148.153",username="kube",pkey=key,timeout=10,allow_agent=False,look_for_keys=False)
def run(cmd):
    _,o,_=c.exec_command(cmd,timeout=25); return o.read().decode(errors="replace").strip()
out("=== noeuds (cordon ?) ==="); out(run("kubectl get nodes --no-headers | awk '{print $1, $2}'"))
out("\n=== networkpolicies chaos ==="); out(run("kubectl get networkpolicy -n pfe --no-headers 2>/dev/null | awk '{print $1}'") or "(aucune)")
out("\n=== hpa ==="); out(run("kubectl get hpa -n pfe --no-headers 2>/dev/null") or "(aucun)")
out("\n=== podinfo (resources + pods) ==="); out(run("kubectl get deploy podinfo -n pfe -o jsonpath='{.spec.template.spec.containers[0].resources.limits}'")); out(run("kubectl get pods -n pfe -l app=podinfo -o wide --no-headers | awk '{print $1,$2,$3,$7}'"))
out("\n=== command override (crashloop) ? ==="); out(run("kubectl get deploy podinfo -n pfe -o jsonpath='{.spec.template.spec.containers[0].command}'") or "(aucune)")
out("\n=== postgres ==="); out(run("kubectl get pods -n pfe -l cnpg.io/cluster --no-headers | awk '{print $1,$2,$3}'")); out(run("kubectl get cluster postgres -n pfe -o jsonpath='ready={.status.readyInstances} primary={.status.currentPrimary}'"))
c.close()
