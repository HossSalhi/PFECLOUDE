import paramiko, os, sys
KEY="C:/Users/houss/.ssh/id_ed25519"; TAR="C:/Users/houss/IdeaProjects/PFE-Orange/pfe-frontend-1.1.3.tar"
NODES=[("192.168.148.153","master"),("192.168.148.158","w1"),("192.168.148.155","w2")]
def out(s): sys.stdout.buffer.write((s+"\n").encode("utf-8","replace")); sys.stdout.flush()
def cli(h):
    k=paramiko.Ed25519Key.from_private_key_file(KEY); c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(h,username="kube",pkey=k,timeout=10,allow_agent=False,look_for_keys=False); return c
def run(c,cmd,sudo=False,t=180):
    if sudo: cmd="echo 'kube' | sudo -S "+cmd
    _,o,e=c.exec_command(cmd,timeout=t); return (o.read().decode(errors="replace")+e.read().decode(errors="replace")).strip()

for ip,label in NODES:
    out(f"\n[{label} {ip}] scp + import")
    c=cli(ip); sftp=c.open_sftp(); sftp.put(TAR,"/tmp/pfe-frontend-1.1.3.tar"); sftp.close()
    out(run(c,"ctr -n k8s.io images import /tmp/pfe-frontend-1.1.3.tar",sudo=True))
    c.close()

out("\n[master] update deployment -> 1.1.3")
c=cli("192.168.148.153")
out(run(c,"kubectl set image deployment/frontend frontend=pfe-frontend:1.1.3 -n pfe"))
out(run(c,"kubectl rollout status deployment/frontend -n pfe --timeout=120s",t=140))
out("\n[verif] bundle servi par le nouveau pod :")
pod=run(c,"kubectl get pod -n pfe -l app=frontend -o jsonpath='{.items[0].metadata.name}'")
out(run(c,f"kubectl exec -n pfe {pod} -- sh -c 'ls /usr/share/nginx/html/main*.js'"))
c.close()
out("\n=== attendu : main.e996059a5f12a649.js (build du 17 juin) ===")
