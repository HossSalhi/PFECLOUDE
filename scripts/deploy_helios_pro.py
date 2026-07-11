# -*- coding: utf-8 -*-
"""HELIOS — Déploiement ÉVAL-GATÉ du modèle DPO (helios-120-pro) sur .161.
Le point PRO : on ne remplace qwen QUE si le nouveau modèle le bat objectivement sur les 120 types.

usage : python scripts/deploy_helios_pro.py <chemin_local_helios-120-pro.gguf>
Étapes : scp GGUF -> Ollama create helios-120-pro -> ÉVAL action nommée sur 120 types
         -> si > 0.93 (qwen+RAG) : swap config llm_model + restart + résident ; sinon : garde qwen, supprime le modèle.
"""
import paramiko, sys, time, json, os
IP="192.168.148.161"; BASELINE=0.933
GGUF=sys.argv[1] if len(sys.argv)>1 else None
if not GGUF or not os.path.exists(GGUF): sys.exit("donne le chemin du .gguf : python scripts/deploy_helios_pro.py helios-120-pro.gguf")
c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(IP,username="kube",password="kube",timeout=15)
def run(cmd,sudo=False,t=300):
    if sudo: cmd="echo 'kube' | sudo -S "+cmd
    _,o,e=c.exec_command(cmd,timeout=t); return (o.read().decode(errors="replace")+e.read().decode(errors="replace")).strip()

# 1. upload GGUF + Modelfile + create Ollama model
print("scp GGUF (~2 Go)…")
sftp=c.open_sftp(); sftp.put(GGUF,"/tmp/helios-120-pro.gguf")
sftp.putfo(__import__("io").BytesIO(b'FROM /tmp/helios-120-pro.gguf\nPARAMETER temperature 0.1\nPARAMETER num_predict 96\n'),"/tmp/Modelfile.pro"); sftp.close()
print("ollama create:",run("cd /tmp && ollama create helios-120-pro -f Modelfile.pro",t=400)[-200:])

# 2. ÉVAL action nommée sur les 120 types (helios-120-pro, avec RAG)
EVAL=r'''# -*- coding: utf-8 -*-
import sys,collections; sys.path.insert(0,"/opt/pfe-ai-service")
from app.models.incident_catalog import INCIDENT_CATALOG
from app.services.rag_retriever import RagRetriever
from app.services.llm_wrapper import HeliosLLM
rag=RagRetriever(); llm=HeliosLLM(rag,model="helios-120-pro",fallback=None,timeout=30,num_predict=96)
ok=tot=0
for t,v in INCIDENT_CATALOG.items():
    card=rag.card_for(t)
    if not card: continue
    d=llm.decide(t,"HIGH",f"symptoms consistent with {t.replace(chr(95),chr(32)).lower()}","","")
    tot+=1
    if d and d.get("action")==card["action"]: ok+=1
print("HELIOS_PRO_NAMED=%.4f (%d/%d)"%(ok/max(1,tot),ok,tot))
'''
sftp=c.open_sftp(); sftp.putfo(__import__("io").BytesIO(EVAL.encode()),"/tmp/eval_pro.py"); sftp.close()
print("ÉVAL sur 120 types (helios-120-pro, ~30-50 min sur CPU)…")
# warm
run('''curl -s localhost:11434/api/generate -d '{"model":"helios-120-pro","prompt":"ok","keep_alive":-1,"options":{"num_predict":2}}' >/dev/null 2>&1''',t=250)
res=run("/opt/pfe-ai-service/.venv/bin/python3 /tmp/eval_pro.py",t=4000)
print(res)
import re; m=re.search(r"HELIOS_PRO_NAMED=([0-9.]+)",res); score=float(m.group(1)) if m else 0.0

# 3. décision : swap SEULEMENT si ça bat qwen+RAG
print(f"\nscore helios-120-pro = {score:.3f} vs baseline qwen+RAG {BASELINE}")
if score>BASELINE:
    print(">>> GAGNE -> on remplace qwen par helios-120-pro")
    run("sed -i 's/llm_model: str = .*/llm_model: str = \"helios-120-pro\"/' /opt/pfe-ai-service/app/core/config.py",sudo=True)
    run("systemctl restart pfe-ai-service",sudo=True); time.sleep(6)
    run('''curl -s localhost:11434/api/generate -d '{"model":"helios-120-pro","keep_alive":-1,"options":{"num_predict":1},"prompt":"ok"}' >/dev/null 2>&1''',t=250)
    print("déployé + résident. Vérifie E2E ensuite.")
else:
    print(">>> NE BAT PAS qwen -> on GARDE qwen (pas de régression). helios-120-pro laissé sur disque pour analyse.")
c.close()
