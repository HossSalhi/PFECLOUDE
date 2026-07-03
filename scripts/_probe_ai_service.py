"""Localise le code du service IA sur .161 (chemin inference.py + service systemd) pour savoir
si on peut redeployer une inference.py editee."""
import paramiko, sys
KEY="C:/Users/houss/.ssh/id_ed25519"; AI="192.168.148.161"
def out(s): sys.stdout.buffer.write((s+"\n").encode("utf-8","replace")); sys.stdout.flush()
k=paramiko.Ed25519Key.from_private_key_file(KEY); c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(AI,username="kube",pkey=k,timeout=12,allow_agent=False,look_for_keys=False)
def run(cmd,sudo=False,t=40):
    if sudo: cmd="echo 'kube' | sudo -S "+cmd
    _,o,e=c.exec_command(cmd,timeout=t); return (o.read().decode(errors="replace")+e.read().decode(errors="replace")).strip()
out("[service actif ?]"); out(run("systemctl list-units --type=service --state=running | grep -iE 'ai|uvicorn|pfe' || echo none"))
out("\n[ExecStart pfe-ai-service]"); out(run("systemctl cat pfe-ai-service.service 2>/dev/null | grep -E 'ExecStart|WorkingDirectory|User' || systemctl cat ai-service.service 2>/dev/null | grep -E 'ExecStart|WorkingDirectory|User' || echo none"))
out("\n[inference.py sur disque]"); out(run("find /opt /home /srv /root -name inference.py -path '*models*' 2>/dev/null | head -n 5 || echo none"))
c.close()
