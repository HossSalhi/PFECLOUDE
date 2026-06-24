import paramiko, sys
key = paramiko.Ed25519Key.from_private_key_file("C:/Users/houss/.ssh/id_ed25519")
def ssh(host="192.168.148.153"):
    c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(host, username="kube", pkey=key, timeout=10, allow_agent=False, look_for_keys=False)
    return c
def run(c, cmd, sudo=False):
    if sudo: cmd = "echo 'kube' | sudo -S " + cmd
    _, o, e = c.exec_command(cmd, timeout=40)
    return (o.read().decode(errors="replace") + e.read().decode(errors="replace")).strip()
def out(s): sys.stdout.buffer.write((s+"\n").encode("utf-8","replace"))

c = ssh()
pod = run(c, "kubectl get pod -n pfe -l app=podinfo -o jsonpath='{.items[0].metadata.name}'")
out(f"podinfo pod = {pod}")

out("\n=== podinfo a-t-il sh ? ===")
out(run(c, f"kubectl exec -n pfe {pod} -- /bin/sh -c 'echo SHELL_OK; uname -a' 2>&1 | head -3"))

out("\n=== podinfo a-t-il busybox/dd/yes ? ===")
out(run(c, f"kubectl exec -n pfe {pod} -- /bin/sh -c 'which dd yes nproc; ls /bin | head -20' 2>&1 | head -8"))

out("\n=== podinfo peut ecrire /tmp ? ===")
out(run(c, f"kubectl exec -n pfe {pod} -- /bin/sh -c 'echo test > /tmp/x && echo WRITE_OK && rm /tmp/x' 2>&1 | head -3"))

out("\n=== TOTAL images cache (worker1) ===")
out(run(c, "ctr -n k8s.io images list -q 2>/dev/null | grep -v sha256 | sort -u | head -40", sudo=True))
c.close()
