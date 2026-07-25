"""Deploie pfe-backend:0.2.150 — timeline coherente sur ImagePullBackOff.

Correctif (AIOpsOrchestrationService.autoResolveIfCleared, branche imagePullBackOff) :
  - si l'incident ouvert est DEJA POD_IMAGE_PULL_BACKOFF -> on NE re-cle PAS (return false) :
    sa remediation + verification post-remediation le pilotent. Fini le clignotement
    RESOLVED <-> RESOLUTION_FAILED sur la meme ligne.
  - cas legitime (autre type -> ImagePull) : cloture en CLOSED (gris neutre, PAS "resolu")
    au lieu de RESOLVED (vert), avec message "Re-indexe ...". CLOSED n'est pas dans
    ACTIVE_STATUSES => la dedup laisse quand meme s'ouvrir le nouvel incident type.

Aucun changement frontend : .closed est deja stylise en gris neutre.

Import air-gap w1+w2 + rollout + purge leader_lease sur le PRIMAIRE CNPG."""
import paramiko, sys
KEY="C:/Users/houss/.ssh/id_ed25519"; TAR="C:/Users/houss/IdeaProjects/PFE-Orange/pfe-backend-0.2.150.tar"
WORKERS=[("192.168.148.158","w1"),("192.168.148.155","w2")]; MASTER="192.168.148.153"
def out(s): sys.stdout.buffer.write((s+"\n").encode("utf-8","replace")); sys.stdout.flush()
def cli(h):
    k=paramiko.Ed25519Key.from_private_key_file(KEY); c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(h,username="kube",pkey=k,timeout=12,allow_agent=False,look_for_keys=False); return c
def run(c,cmd,sudo=False,t=400):
    if sudo: cmd="echo 'kube' | sudo -S "+cmd
    _,o,e=c.exec_command(cmd,timeout=t); return (o.read().decode(errors="replace")+e.read().decode(errors="replace")).strip()
for ip,label in WORKERS:
    out(f"[{label} {ip}] scp+import"); c=cli(ip); sftp=c.open_sftp(); sftp.put(TAR,"/tmp/pfe-backend-0.2.150.tar"); sftp.close()
    out(run(c,"ctr -n k8s.io images import /tmp/pfe-backend-0.2.150.tar",sudo=True,t=400)); c.close()
m=cli(MASTER)
out("\n[master] set image -> 0.2.150")
out(run(m,"kubectl set image deployment/backend backend=pfe-backend:0.2.150 -n pfe"))
out(run(m,"kubectl rollout status deployment/backend -n pfe --timeout=240s",t=260))
prim=run(m,"kubectl get pods -n pfe -l cnpg.io/instanceRole=primary -o jsonpath='{.items[0].metadata.name}'")
out(f"\n[primaire CNPG = {prim}] purge leader_lease")
if prim:
    out(run(m,f"kubectl exec -n pfe {prim} -c postgres -- psql -U postgres -d pfe_db -c \"DELETE FROM leader_lease WHERE lease_name='aiops-scheduler'\" 2>&1") or "(ok)")
out("\n[verif]")
out(run(m,"kubectl get pods -n pfe -l app=backend --no-headers 2>&1"))
out(run(m,"kubectl get deploy backend -n pfe -o jsonpath='{.spec.template.spec.containers[0].image}'"))
