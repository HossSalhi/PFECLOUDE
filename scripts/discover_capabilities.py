import paramiko, sys
key = paramiko.Ed25519Key.from_private_key_file("C:/Users/houss/.ssh/id_ed25519")
def ssh(host="192.168.148.153"):
    c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(host, username="kube", pkey=key, timeout=10, allow_agent=False, look_for_keys=False)
    return c
def run(c, cmd, sudo=False):
    if sudo: cmd = "echo 'kube' | sudo -S " + cmd
    _, o, e = c.exec_command(cmd, timeout=30)
    return (o.read().decode(errors="replace") + e.read().decode(errors="replace")).strip()
def out(s): sys.stdout.buffer.write((s+"\n").encode("utf-8","replace"))

c = ssh()
out("=== metrics-server (pour HPA) ===")
out(run(c, "kubectl get deploy -n kube-system metrics-server 2>&1 | head -3"))
out(run(c, "kubectl top pods -n pfe 2>&1 | head -3"))

out("\n=== CoreDNS ===")
out(run(c, "kubectl get deploy -n kube-system coredns 2>&1 | head -3"))

out("\n=== CNI / NetworkPolicy (Calico ?) ===")
out(run(c, "kubectl get pods -n kube-system 2>&1 | grep -iE 'calico|cilium|weave' | head -3"))

out("\n=== PodDisruptionBudget podinfo ? ===")
out(run(c, "kubectl get pdb -n pfe 2>&1 | head -5"))

out("\n=== Images en cache sur worker1 (avec shell pour stress) ===")
imgs = run(c, "ctr -n k8s.io images list -q 2>/dev/null | grep -iE 'busybox|alpine|stress|nginx|ubuntu|debian|polinux|progrium' | head -20", sudo=True)
out(imgs if imgs else "(aucune image stress/shell evidente — verif manuelle)")

out("\n=== podinfo : securityContext (readonly fs ?) ===")
out(run(c, "kubectl get deploy podinfo -n pfe -o jsonpath='{.spec.template.spec.containers[0].securityContext}' 2>&1"))
c.close()
