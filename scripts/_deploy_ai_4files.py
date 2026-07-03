"""Deploie 4 fichiers IA (incident_catalog, inference, predictor, schemas) sur .161 avec backup +
rollback auto si /health ne remonte pas. Champ dedie catalog_resolution ajoute."""
import paramiko, sys, time
KEY="C:/Users/houss/.ssh/id_ed25519"; AI="192.168.148.161"; ROOT="/opt/pfe-ai-service/app"
B="C:/Users/houss/IdeaProjects/PFE-Orange/backend/backend/ai-service/app"
FILES=[("models/incident_catalog.py","models"),("models/inference.py","models"),
       ("models/schemas.py","models"),("services/predictor.py","services")]
def out(s): sys.stdout.buffer.write((s+"\n").encode("utf-8","replace")); sys.stdout.flush()
k=paramiko.Ed25519Key.from_private_key_file(KEY); c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(AI,username="kube",pkey=k,timeout=12,allow_agent=False,look_for_keys=False)
def run(cmd,t=120): _,o,e=c.exec_command(cmd,timeout=t); return (o.read().decode(errors="replace")+e.read().decode(errors="replace")).strip()
sftp=c.open_sftp()
for rel,_ in FILES: sftp.put(f"{B}/{rel}",f"/tmp/dep_{rel.replace('/','_')}")
sftp.close()
ts=time.strftime("%Y%m%d-%H%M%S")
cmds=[]
for rel,_ in FILES:
    dst=f"{ROOT}/{rel}"; tmp=f"/tmp/dep_{rel.replace('/','_')}"
    cmds.append(f"cp {dst} {dst}.bak-{ts}; cp {tmp} {dst}; chown aiops:aiops {dst}")
cmds.append("systemctl restart pfe-ai-service; echo OK-"+ts)
out("[install 4 fichiers + restart]")
out(run("echo 'kube' | sudo -S bash -c '"+"; ".join(cmds)+"'"))
ok=False
for i in range(24):
    if run("curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/health || echo 000").strip().endswith("200"):
        ok=True; out(f"health=200 apres {i*3}s"); break
    time.sleep(3)
if not ok:
    out("!! /health KO -> ROLLBACK des 4 fichiers")
    rb=[f"cp {ROOT}/{rel}.bak-{ts} {ROOT}/{rel}" for rel,_ in FILES]
    rb.append("systemctl restart pfe-ai-service; echo ROLLED-BACK")
    out(run("echo 'kube' | sudo -S bash -c '"+"; ".join(rb)+"'")); c.close(); sys.exit(1)
out("OK IA deployee")
c.close()
