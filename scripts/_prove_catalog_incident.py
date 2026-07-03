"""Preuve finale : un incident REEL porte l'explication IA + la RESOLUTION CATALOGUE des 120."""
import paramiko, sys, time
KEY="C:/Users/houss/.ssh/id_ed25519"; MASTER="192.168.148.153"
def out(s): sys.stdout.buffer.write((s+"\n").encode("utf-8","replace")); sys.stdout.flush()
k=paramiko.Ed25519Key.from_private_key_file(KEY); c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(MASTER,username="kube",pkey=k,timeout=12,allow_agent=False,look_for_keys=False)
def run(cmd,t=90): _,o,e=c.exec_command(cmd,timeout=t); return (o.read().decode(errors="replace")+e.read().decode(errors="replace")).strip()
prim=run("kubectl get pods -n pfe -l cnpg.io/instanceRole=primary -o jsonpath='{.items[0].metadata.name}'")
def sql(q): return run(f"kubectl exec -n pfe {prim} -c postgres -- psql -U postgres -d pfe_db -t -A -c \"{q}\" 2>&1").strip()
base=sql("SELECT COALESCE(MAX(id),0) FROM incident"); out(f"[baseline] max id={base}")
out(run("kubectl -n pfe patch deploy helios-crashloop --type=json "
    "-p '[{\"op\":\"add\",\"path\":\"/spec/template/spec/containers/0/command\",\"value\":[\"sh\",\"-c\",\"echo boot; exit 1\"]}]'"))
inc=None
for i in range(24):
    time.sleep(15)
    row=sql(f"SELECT id||'~'||incident_type||'~'||status FROM incident WHERE app='helios-crashloop' AND id>{base} ORDER BY id DESC LIMIT 1")
    out(f"  +{(i+1)*15:3d}s {row or '(aucun)'}")
    if row and '~' in row:
        inc=row.split('~')[0]
        if row.split('~')[-1] in ("RESOLVED","RESOLUTION_FAILED"): break
if inc:
    out(f"\n[incident #{inc} — colonnes]")
    out("TYPE  = "+sql(f"SELECT incident_type FROM incident WHERE id={inc}"))
    out("EXPL  = "+sql(f"SELECT explanation FROM incident WHERE id={inc}"))
    out("STATUS= "+sql(f"SELECT status FROM incident WHERE id={inc}"))
c.close()
out("\n=== ATTENDU : EXPL contient 'Resolution (catalogue HELIOS)' ===")
