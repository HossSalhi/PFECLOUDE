import paramiko, sys
key = paramiko.Ed25519Key.from_private_key_file("C:/Users/houss/.ssh/id_ed25519")
def out(s): sys.stdout.buffer.write((s+"\n").encode("utf-8","replace"))
c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.148.155",username="kube",pkey=key,timeout=10,allow_agent=False,look_for_keys=False)  # worker2
def run(cmd,sudo=False):
    if sudo: cmd="echo 'kube' | sudo -S "+cmd
    _,o,e=c.exec_command(cmd,timeout=30)
    return (o.read().decode(errors="replace")+e.read().decode(errors="replace")).strip()
out("=== outils build dispo ? ===")
out("nerdctl: "+run("which nerdctl || echo NON"))
out("buildctl: "+run("which buildctl || echo NON"))
out("buildah: "+run("which buildah || echo NON"))
out("crane: "+run("which crane || echo NON"))
out("\n=== images base cachees (node/nginx/frontend) ===")
out(run("ctr -n k8s.io images list -q 2>/dev/null | grep -iE 'node:|nginx:|pfe-frontend' | sort -u", sudo=True))
c.close()
