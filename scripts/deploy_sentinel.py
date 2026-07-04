"""Déploie la Sentinelle HELIOS (capteur externe de santé cluster) sur .161 en service systemd.
scp -> /opt/helios-sentinel/helios-sentinel.py + unit systemd + enable/start + vérif du log HEALTHY."""
import paramiko, sys, time
KEY="C:/Users/houss/.ssh/id_ed25519"; AI="192.168.148.161"
LOCAL="C:/Users/houss/IdeaProjects/PFE-Orange/scripts/helios-ops/helios-sentinel.py"
def out(s): sys.stdout.buffer.write((s+"\n").encode("utf-8","replace")); sys.stdout.flush()
k=paramiko.Ed25519Key.from_private_key_file(KEY); c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(AI,username="kube",pkey=k,timeout=12,allow_agent=False,look_for_keys=False)
def run(cmd,t=90): _,o,e=c.exec_command(cmd,timeout=t); return (o.read().decode(errors="replace")+e.read().decode(errors="replace")).strip()
def sudo(cmd,t=90): return run("echo 'kube' | sudo -S bash -c \""+cmd.replace('"','\\"')+"\"",t)

out("[1] scp du script vers /tmp")
sftp=c.open_sftp(); sftp.put(LOCAL,"/tmp/helios-sentinel.py"); sftp.close()

UNIT="""[Unit]
Description=HELIOS Sentinel - capteur externe de sante cluster (survit a la chute du cluster)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 /opt/helios-sentinel/helios-sentinel.py
Restart=always
RestartSec=5
User=root

[Install]
WantedBy=multi-user.target
"""
out("[2] install /opt + unit systemd")
out(sudo("mkdir -p /opt/helios-sentinel && cp /tmp/helios-sentinel.py /opt/helios-sentinel/helios-sentinel.py && chmod 644 /opt/helios-sentinel/helios-sentinel.py && touch /var/log/helios-sentinel.log"))
# écrire l'unit via un heredoc en base64 pour éviter les soucis de quoting
import base64
b64=base64.b64encode(UNIT.encode()).decode()
out(sudo(f"echo {b64} | base64 -d > /etc/systemd/system/helios-sentinel.service"))
out("[3] enable + (re)start")
out(sudo("systemctl daemon-reload && systemctl enable helios-sentinel >/dev/null 2>&1; systemctl restart helios-sentinel && sleep 2 && systemctl is-active helios-sentinel"))
out("[4] attente d'un premier cycle + log")
time.sleep(18)
out(run("tail -n 12 /var/log/helios-sentinel.log 2>&1"))
out("\n[5] état service")
out(sudo("systemctl status helios-sentinel --no-pager | head -6"))
c.close()
out("\n=== attendu : service active (running) + log 'HELIOS Sentinel démarré' + état HEALTHY ===")
