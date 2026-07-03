"""Deploie incident_catalog.py (NEUF) + inference.py (import du catalogue) sur .161. Rollback auto si
/health ne remonte pas (import casse). Puis sonde /predict -> decision_explanation doit contenir
'Resolution (catalogue HELIOS)'."""
import paramiko, sys, time, re
KEY="C:/Users/houss/.ssh/id_ed25519"; AI="192.168.148.161"; D="/opt/pfe-ai-service/app/models"
BASE="C:/Users/houss/IdeaProjects/PFE-Orange/backend/backend/ai-service/app/models"
SH="C:/Users/houss/AppData/Local/Temp/claude/c--Users-houss-IdeaProjects-PFE-Orange/7a498a6f-f64e-405d-a014-1f262520c572/scratchpad/probe.sh"
def out(s): sys.stdout.buffer.write((s+"\n").encode("utf-8","replace")); sys.stdout.flush()
k=paramiko.Ed25519Key.from_private_key_file(KEY); c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(AI,username="kube",pkey=k,timeout=12,allow_agent=False,look_for_keys=False)
def run(cmd,t=120): _,o,e=c.exec_command(cmd,timeout=t); return (o.read().decode(errors="replace")+e.read().decode(errors="replace")).strip()
sftp=c.open_sftp()
sftp.put(f"{BASE}/incident_catalog.py","/tmp/incident_catalog.py")
sftp.put(f"{BASE}/inference.py","/tmp/inference_new.py")
sftp.put(SH,"/tmp/probe.sh"); sftp.close()
ts=time.strftime("%Y%m%d-%H%M%S")
out("[install catalog + inference + restart]")
out(run(f"echo 'kube' | sudo -S bash -c '"
        f"cp {D}/inference.py {D}/inference.py.bak-{ts}; "
        f"[ -f {D}/incident_catalog.py ] && cp {D}/incident_catalog.py {D}/incident_catalog.py.bak-{ts}; "
        f"cp /tmp/incident_catalog.py {D}/incident_catalog.py; cp /tmp/inference_new.py {D}/inference.py; "
        f"chown aiops:aiops {D}/incident_catalog.py {D}/inference.py; "
        f"systemctl restart pfe-ai-service; echo OK-{ts}'"))
ok=False
for i in range(20):
    if run("curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/health || echo 000").strip().endswith("200"):
        ok=True; out(f"health=200 apres {i*3}s"); break
    time.sleep(3)
if not ok:
    out("!! /health NE REMONTE PAS -> ROLLBACK inference.py")
    out(run(f"echo 'kube' | sudo -S bash -c 'cp {D}/inference.py.bak-{ts} {D}/inference.py; systemctl restart pfe-ai-service; echo ROLLED-BACK'"))
    c.close(); sys.exit(1)
out("\n[sonde /predict crashloop -> resolution catalogue ?]")
resp=run("echo 'kube' | sudo -S bash /tmp/probe.sh 2>/dev/null",t=60)
m=re.search(r'"decision_explanation"\s*:\s*"([^"]+)"',resp)
out("decision_explanation = "+(m.group(1) if m else "(absent)"))
mt=re.search(r'"incident_type"\s*:\s*"([^"]+)"',resp); out("incident_type = "+(mt.group(1) if mt else "?"))
out("\nCATALOGUE PRESENT: "+("OUI" if (m and "catalogue HELIOS" in m.group(1)) else "NON"))
c.close()
