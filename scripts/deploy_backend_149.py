"""Deploie pfe-backend:0.2.149 — l'explication affichee est celle ECRITE PAR LE LLM.

Mesure prealable (15 appels, 3 types de pannes reelles) : 93% des textes du LLM nomment
correctement la cause. Le service IA les filtre deja en amont (_llm_ok n'accepte le texte
que s'il nomme la cause vue par kubelet), donc aucun second filtre cote backend.

On n'extrait QUE la partie "Why:" : le prefixe porte un "Recommended action: X" qui peut
contredire l'action reelle apres un garde-fou backend. Repli deterministe si vide.

IMPORTANT : la version 0.2.148 cassait la creation d'incidents (aucune exception, aucune
collision de dedup, juste plus aucun incident). Cette version est volontairement pauvre :
operations sur chaines uniquement, pas de regex, pas d'etat statique, try/catch large.

Import air-gap w1+w2 + rollout + purge leader_lease sur le PRIMAIRE CNPG."""
import paramiko, sys
KEY="C:/Users/houss/.ssh/id_ed25519"; TAR="C:/Users/houss/IdeaProjects/PFE-Orange/pfe-backend-0.2.149.tar"
WORKERS=[("192.168.148.158","w1"),("192.168.148.155","w2")]; MASTER="192.168.148.153"
def out(s): sys.stdout.buffer.write((s+"\n").encode("utf-8","replace")); sys.stdout.flush()
def cli(h):
    k=paramiko.Ed25519Key.from_private_key_file(KEY); c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(h,username="kube",pkey=k,timeout=12,allow_agent=False,look_for_keys=False); return c
def run(c,cmd,sudo=False,t=400):
    if sudo: cmd="echo 'kube' | sudo -S "+cmd
    _,o,e=c.exec_command(cmd,timeout=t); return (o.read().decode(errors="replace")+e.read().decode(errors="replace")).strip()
for ip,label in WORKERS:
    out(f"[{label} {ip}] scp+import"); c=cli(ip); sftp=c.open_sftp(); sftp.put(TAR,"/tmp/pfe-backend-0.2.149.tar"); sftp.close()
    out(run(c,"ctr -n k8s.io images import /tmp/pfe-backend-0.2.149.tar",sudo=True,t=400)); c.close()
m=cli(MASTER)
out("\n[master] set image -> 0.2.149")
out(run(m,"kubectl set image deployment/backend backend=pfe-backend:0.2.149 -n pfe"))
out(run(m,"kubectl rollout status deployment/backend -n pfe --timeout=240s",t=260))
prim=run(m,"kubectl get pods -n pfe -l cnpg.io/instanceRole=primary -o jsonpath='{.items[0].metadata.name}'")
out(f"\n[primaire CNPG = {prim}] purge leader_lease")
if prim:
    out(run(m,f"kubectl exec -n pfe {prim} -c postgres -- psql -U postgres -d pfe_db -c \"DELETE FROM leader_lease WHERE lease_name='aiops-scheduler'\" 2>&1") or "(ok)")
out("\n[verif]")
out(run(m,"kubectl get pods -n pfe -l app=backend --no-headers 2>&1"))
out(run(m,"kubectl get deploy backend -n pfe -o jsonpath='{.spec.template.spec.containers[0].image}'"))
