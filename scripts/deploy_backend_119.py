"""Deploie pfe-backend:0.2.119 (fix OOM : hasOOMKilled reconnait exitCode 137 -> MEMORY_STRESS
au lieu de DEPLOYMENT_UNAVAILABLE quand l'OOM-killer fauche le process enfant `tail`).
Import air-gap sur worker1+worker2 (backend = nodeAffinity node-type=worker) + rollout +
purge leader_lease sur le PRIMAIRE CNPG detecte dynamiquement (jamais hardcode)."""
import paramiko, sys
KEY="C:/Users/houss/.ssh/id_ed25519"; TAR="C:/Users/houss/IdeaProjects/PFE-Orange/pfe-backend-0.2.119.tar"
WORKERS=[("192.168.148.158","w1"),("192.168.148.155","w2")]; MASTER="192.168.148.153"
def out(s): sys.stdout.buffer.write((s+"\n").encode("utf-8","replace")); sys.stdout.flush()
def cli(h):
    k=paramiko.Ed25519Key.from_private_key_file(KEY); c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(h,username="kube",pkey=k,timeout=12,allow_agent=False,look_for_keys=False); return c
def run(c,cmd,sudo=False,t=240):
    if sudo: cmd="echo 'kube' | sudo -S "+cmd
    _,o,e=c.exec_command(cmd,timeout=t); return (o.read().decode(errors="replace")+e.read().decode(errors="replace")).strip()
for ip,label in WORKERS:
    out(f"[{label} {ip}] scp+import (219 MB)"); c=cli(ip); sftp=c.open_sftp(); sftp.put(TAR,"/tmp/pfe-backend-0.2.119.tar"); sftp.close()
    out(run(c,"ctr -n k8s.io images import /tmp/pfe-backend-0.2.119.tar",sudo=True,t=300)); c.close()
m=cli(MASTER)
out("\n[master] set image -> 0.2.119")
out(run(m,"kubectl set image deployment/backend backend=pfe-backend:0.2.119 -n pfe"))
out(run(m,"kubectl rollout status deployment/backend -n pfe --timeout=200s",t=220))
out("\n[detect primaire CNPG]")
prim=run(m,"kubectl get pods -n pfe -l cnpg.io/instanceRole=primary -o jsonpath='{.items[0].metadata.name}'")
if not prim or "error" in prim.lower():
    prim=run(m,"kubectl get pods -n pfe -l role=primary -o jsonpath='{.items[0].metadata.name}'")
out(f"primaire = {prim}")
if prim:
    out("[purge leader_lease aiops-scheduler]")
    out(run(m,f"kubectl exec -n pfe {prim} -c postgres -- psql -U postgres -d pfe_db -c \"DELETE FROM leader_lease WHERE lease_name='aiops-scheduler'\" 2>&1") or "(ok/absent)")
out("\n[verif] image + pods")
out(run(m,"kubectl get pods -n pfe -l app=backend -o wide --no-headers 2>&1"))
out(run(m,"kubectl get deploy backend -n pfe -o jsonpath='{.spec.template.spec.containers[0].image}'"))
m.close()
out("\n=== attendu : pfe-backend:0.2.119, backend 2/2 ===")
