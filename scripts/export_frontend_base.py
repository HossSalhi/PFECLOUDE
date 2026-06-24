import paramiko, sys
key = paramiko.Ed25519Key.from_private_key_file("C:/Users/houss/.ssh/id_ed25519")
def out(s): sys.stdout.buffer.write((s+"\n").encode("utf-8","replace")); sys.stdout.flush()
c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.148.155",username="kube",pkey=key,timeout=10,allow_agent=False,look_for_keys=False)
def run(cmd,sudo=False,t=180):
    if sudo: cmd="echo 'kube' | sudo -S "+cmd
    _,o,e=c.exec_command(cmd,timeout=t)
    return (o.read().decode(errors="replace")+e.read().decode(errors="replace")).strip()
out("[1] export pfe-frontend:1.1.2 -> /tmp/fe-base.tar (OCI)")
out(run("ctr -n k8s.io images export /tmp/fe-base.tar docker.io/library/pfe-frontend:1.1.2", sudo=True))
out("[2] chmod + taille")
out(run("chmod 644 /tmp/fe-base.tar; ls -la /tmp/fe-base.tar", sudo=True))
out("[3] sftp -> local")
sftp=c.open_sftp()
sftp.get("/tmp/fe-base.tar","C:/Users/houss/IdeaProjects/PFE-Orange/frontend/fe-base.tar")
sftp.close()
import os
sz=os.path.getsize("C:/Users/houss/IdeaProjects/PFE-Orange/frontend/fe-base.tar")
out(f"[done] local fe-base.tar = {sz//1024//1024} MB")
c.close()
