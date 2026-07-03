import paramiko, sys, time, re
KEY="C:/Users/houss/.ssh/id_ed25519"; AI="192.168.148.161"
LOCAL="C:/Users/houss/IdeaProjects/PFE-Orange/backend/backend/ai-service/app/models/inference.py"
SH="C:/Users/houss/AppData/Local/Temp/claude/c--Users-houss-IdeaProjects-PFE-Orange/7a498a6f-f64e-405d-a014-1f262520c572/scratchpad/probe.sh"
DEST="/opt/pfe-ai-service/app/models/inference.py"
def out(s): sys.stdout.buffer.write((s+"\n").encode("utf-8","replace")); sys.stdout.flush()
k=paramiko.Ed25519Key.from_private_key_file(KEY); c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(AI,username="kube",pkey=k,timeout=12,allow_agent=False,look_for_keys=False)
def run(cmd,t=120):
    _,o,e=c.exec_command(cmd,timeout=t); return (o.read().decode(errors="replace")+e.read().decode(errors="replace")).strip()
sftp=c.open_sftp(); sftp.put(LOCAL,"/tmp/inference_new.py"); sftp.put(SH,"/tmp/probe.sh"); sftp.close()
ts=time.strftime("%Y%m%d-%H%M%S")
out("[install + restart]")
out(run(f"echo 'kube' | sudo -S bash -c 'cp {DEST} {DEST}.bak-{ts}; cp /tmp/inference_new.py {DEST}; chown aiops:aiops {DEST}; systemctl restart pfe-ai-service; echo OK-{ts}'"))
for i in range(20):
    if run("curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/health || echo 000").strip().endswith("200"):
        out(f"health=200 apres {i*3}s"); break
    time.sleep(3)
out("\n[re-sonde /predict crashloop]")
resp=run("echo 'kube' | sudo -S bash /tmp/probe.sh 2>/dev/null",t=60)
out(resp[:900])
out("\n[extrait]")
for key in ("incident_type","recommended_action","final_executable_action","execution_mode","severity","confidence"):
    m=re.search(r'"'+key+r'"\s*:\s*("?[^",}]+"?)',resp)
    if m: out(f"  {key} = {m.group(1)}")
c.close()
out("\n=== ATTENDU : final_executable_action=ROLLBACK_DEPLOYMENT , execution_mode=AUTO_EXECUTE_CANDIDATE ===")
