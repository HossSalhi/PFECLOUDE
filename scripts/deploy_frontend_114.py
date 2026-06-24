import paramiko, sys
KEY="C:/Users/houss/.ssh/id_ed25519"; TAR="C:/Users/houss/IdeaProjects/PFE-Orange/pfe-frontend-1.1.4.tar"
NODES=[("192.168.148.153","master"),("192.168.148.158","w1"),("192.168.148.155","w2")]
def out(s): sys.stdout.buffer.write((s+"\n").encode("utf-8","replace")); sys.stdout.flush()
def cli(h):
    k=paramiko.Ed25519Key.from_private_key_file(KEY); c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(h,username="kube",pkey=k,timeout=10,allow_agent=False,look_for_keys=False); return c
def run(c,cmd,sudo=False,t=180):
    if sudo: cmd="echo 'kube' | sudo -S "+cmd
    _,o,e=c.exec_command(cmd,timeout=t); return (o.read().decode(errors="replace")+e.read().decode(errors="replace")).strip()
for ip,label in NODES:
    out(f"[{label}] scp+import"); c=cli(ip); sftp=c.open_sftp(); sftp.put(TAR,"/tmp/pfe-frontend-1.1.4.tar"); sftp.close()
    out(run(c,"ctr -n k8s.io images import /tmp/pfe-frontend-1.1.4.tar",sudo=True)); c.close()
out("\n[master] update -> 1.1.4"); c=cli("192.168.148.153")
out(run(c,"kubectl set image deployment/frontend frontend=pfe-frontend:1.1.4 -n pfe"))
out(run(c,"kubectl rollout status deployment/frontend -n pfe --timeout=120s",t=140))
pod=run(c,"kubectl get pod -n pfe -l app=frontend -o jsonpath='{.items[0].metadata.name}'")
out("\n[verif] bundle servi + image:")
out(run(c,f"kubectl get pod -n pfe {pod} -o jsonpath='{{.spec.containers[0].image}}'"))
out(run(c,f"kubectl exec -n pfe {pod} -- sh -c \"grep -o 'main\\.[a-z0-9]*\\.js' /usr/share/nginx/html/index.html\""))
c.close()
out("\n=== attendu : pfe-frontend:1.1.4 + main.fccb46e97225fdde.js (sans Fine-Tuning) ===")
