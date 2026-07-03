"""Deploie l'inference.py editee sur .161 (backup + install + restart) puis SONDE /predict
pour un crashloop -> doit renvoyer incident_type=POD_CRASH_LOOP + final_executable_action=ROLLBACK_DEPLOYMENT
(preuve que l'IA elle-meme recommande DESORMAIS le ROLLBACK, plus DELETE). Cle API lue+utilisee sur .161."""
import paramiko, sys, time
KEY="C:/Users/houss/.ssh/id_ed25519"; AI="192.168.148.161"
LOCAL="C:/Users/houss/IdeaProjects/PFE-Orange/backend/backend/ai-service/app/models/inference.py"
DEST="/opt/pfe-ai-service/app/models/inference.py"
def out(s): sys.stdout.buffer.write((s+"\n").encode("utf-8","replace")); sys.stdout.flush()
k=paramiko.Ed25519Key.from_private_key_file(KEY); c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(AI,username="kube",pkey=k,timeout=12,allow_agent=False,look_for_keys=False)
def run(cmd,sudo=False,t=120):
    if sudo: cmd="echo 'kube' | sudo -S bash -c "+repr(cmd)
    _,o,e=c.exec_command(cmd,timeout=t); return (o.read().decode(errors="replace")+e.read().decode(errors="replace")).strip()
out("[1] upload inference.py -> /tmp")
sftp=c.open_sftp(); sftp.put(LOCAL,"/tmp/inference_new.py"); sftp.close()
ts=time.strftime("%Y%m%d-%H%M%S")
out("[2] backup + install + chown + restart")
out(run(f"cp {DEST} {DEST}.bak-{ts} && cp /tmp/inference_new.py {DEST} && chown aiops:aiops {DEST} && systemctl restart pfe-ai-service && echo INSTALLED-{ts}",sudo=True))
out("[3] attente /health 200")
for i in range(20):
    h=run("curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/health || echo 000")
    if h.strip().endswith("200"): out(f"    health=200 apres {i*3}s"); break
    time.sleep(3)
else: out("    /health jamais 200 — abort probe"); c.close(); sys.exit(1)
out("\n[4] SONDE /predict crashloop (via .161, cle depuis l'env du service)")
payload='{"namespace":"pfe","app":"probe-crashloop","window_minutes":10,"feature_overrides":{"pod_restarts_total":6.0,"unavailable_replicas":1.0,"overall_risk_score":0.7,"node_not_ready_count":0.0,"monitoring_vm_up":1.0,"ai_vm_up":1.0},"remediation_context":{"root_cause":"container crashloopbackoff"}}'
probe=(
 "K=$(systemctl show pfe-ai-service -p Environment | tr ' ' '\\n' | grep '^AIOPS_API_KEY=' | cut -d= -f2-); "
 "if [ -z \"$K\" ]; then EF=$(systemctl show pfe-ai-service -p EnvironmentFiles | grep -o '/[^ ()]*' | head -n1); K=$(grep -h '^AIOPS_API_KEY=' $EF 2>/dev/null | cut -d= -f2-); fi; "
 "curl -s -H \"X-API-Key: $K\" -H 'Content-Type: application/json' -X POST http://127.0.0.1:8000/predict -d '"+payload+"'"
)
resp=run(probe,sudo=True,t=60)
out(resp[:1500])
out("\n[5] extrait")
import re
for key in ("incident_type","recommended_action","final_executable_action","is_incident","incident\""):
    m=re.search(r'"'+key.replace('"','')+r'"\s*:\s*("?[^",}]+"?)',resp)
    if m: out(f"    {key.replace(chr(34),'')} = {m.group(1)}")
c.close()
out("\n=== ATTENDU : incident_type=POD_CRASH_LOOP , final_executable_action=ROLLBACK_DEPLOYMENT ===")
