# -*- coding: utf-8 -*-
"""OBSOLETE ET NEUTRALISE (6 nov. 2026) - NE PAS UTILISER.

Ce script installait l'ANCIEN Bridge (camunda-direct/bridge/bridge.py). Cette version contient rallumer() = appel du watchdog
hote :9099 (mise sous tension autonome d'une VM), un mot de passe SSH en clair (HELIOS_SSH_PASS=kube), le jeton du Bridge dans
l'unite systemd, et elle AFFICHE ce jeton. L'executer ecraserait le Bridge de production (infra/helios-sot/bridge/, sans aucune
capacite de mise sous tension). Un seul acteur demarre une VM : le backend, via le watchdog hote :9099.

Bridge de production : infra/helios-sot/bridge/ ; deploiement : infra/helios-sot/tools/deploy_bridge_target.sh ;
controle : infra/helios-sot/tools/check_bridge_no_poweron.sh. Original conserve : helios-state/snapshots/prechange-20261106/.
Le code historique ci-dessous est conserve pour memoire et n'est JAMAIS execute (arret inconditionnel).
"""
import sys
sys.stderr.write("deploy_bridge.py est OBSOLETE et NEUTRALISE : il installerait l'ancien Bridge (capacite de mise sous tension, "
                 "mot de passe en clair). Utiliser infra/helios-sot/tools/deploy_bridge_target.sh.\n")
raise SystemExit(3)

import paramiko, sys, os, time, json, secrets   # noqa: E402 - code historique, jamais atteint
def out(s): sys.stdout.buffer.write((str(s)+"\n").encode("utf-8","replace")); sys.stdout.flush()
def sec(t): out("\n" + "="*74 + "\n  " + t + "\n" + "="*74)
SP = os.path.dirname(__file__)
a = paramiko.SSHClient(); a.set_missing_host_key_policy(paramiko.AutoAddPolicy())
a.connect("192.168.148.161", username="kube", password="kube", timeout=20, allow_agent=False, look_for_keys=False)
def ra(c,t=120):
    _,o,e=a.exec_command(c,timeout=t)
    return "\n".join(l for l in (o.read().decode(errors="replace")+e.read().decode(errors="replace")).splitlines() if "password for" not in l).strip()

# jeton stable : on reutilise celui deja pose si le service existe deja
existant = ra("sudo -S grep -oP 'HELIOS_BRIDGE_TOKEN=\\K\\S+' /etc/systemd/system/helios-ssh-bridge.service 2>/dev/null <<< kube | head -1")
JETON = existant if existant and len(existant) > 16 else secrets.token_hex(24)

sec("1. DEPOT DU SERVICE")
sftp = a.open_sftp(); sftp.put(os.path.join(SP, "bridge.py"), "/tmp/bridge.py"); sftp.close()
UNIT = """[Unit]
Description=HELIOS SSH bridge pour Camunda (127.0.0.1:8099)
After=network-online.target

[Service]
Type=simple
Environment=HELIOS_SSH_USER=kube
Environment=HELIOS_SSH_PASS=kube
Environment=HELIOS_BRIDGE_TOKEN=%s
ExecStart=/usr/bin/python3 /opt/helios-ssh-bridge/bridge.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
""" % JETON
INSTALL = """#!/bin/bash
set -e
mkdir -p /opt/helios-ssh-bridge
cp /tmp/bridge.py /opt/helios-ssh-bridge/bridge.py
chmod 750 /opt/helios-ssh-bridge/bridge.py
cp /tmp/bridge.service /etc/systemd/system/helios-ssh-bridge.service
chmod 600 /etc/systemd/system/helios-ssh-bridge.service
touch /var/log/helios-ssh-bridge.log
systemctl daemon-reload
systemctl enable --now helios-ssh-bridge
sleep 3
systemctl restart helios-ssh-bridge
echo INSTALLE
"""
sftp = a.open_sftp()
with sftp.open("/tmp/bridge.service","w") as f: f.write(UNIT)
with sftp.open("/tmp/install.sh","w") as f: f.write(INSTALL)
sftp.close()
out("   " + ra("echo kube | sudo -S bash /tmp/install.sh 2>&1 | tail -2"))
time.sleep(4)
out("   service : " + ra("systemctl is-active helios-ssh-bridge"))
out("   ecoute  : " + (ra("ss -tln | grep ':8099' | head -1") or "AUCUNE"))

sec("2. VERIFICATION DU PERIMETRE RESEAU")
out("   depuis .161 (localhost)   : HTTP " + ra("curl -s -o /dev/null -w '%{http_code}' --max-time 6 http://127.0.0.1:8099/sante"))
out("   depuis l'exterieur (.160) : " + (ra("timeout 8 sshpass -p kube ssh -o StrictHostKeyChecking=no kube@192.168.148.160 \"curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://192.168.148.161:8099/sante\" 2>&1 | tail -1") or "injoignable") + "   (000 = injoignable = attendu)")
out("   sans jeton                : HTTP " + ra("curl -s -o /dev/null -w '%{http_code}' --max-time 8 -X POST http://127.0.0.1:8099/exec -H 'Content-Type: application/json' -d '{\"host\":\"master\",\"command\":\"hostname\"}'") + "   (401 attendu)")

sec("3. TEST SUR LES 3 NOEUDS (commandes inoffensives)")
def exec_(noeud, cmd):
    body = json.dumps({"host": noeud, "command": cmd}).replace("'", "'\\''")
    r = ra("curl -s --max-time 60 -X POST http://127.0.0.1:8099/exec -H 'Content-Type: application/json' -H 'X-Helios-Token: %s' -d '%s'" % (JETON, body))
    try: return json.loads(r)
    except: return {"stdout": r[:200], "exitCode": "?", "ok": False}

for n in ("master","worker1","worker2"):
    r = exec_(n, "hostname; uptime -p; systemctl is-active kubelet")
    out("   %-8s exit=%-3s %s" % (n, r.get("exitCode"), r.get("stdout","").replace("\n"," | ")[:90]))

sec("4. DROITS SUDO SUR LES NOEUDS (necessaires pour kubelet/disque)")
for n in ("master","worker1","worker2"):
    r = exec_(n, "echo kube | sudo -S -p '' systemctl is-active kubelet 2>/dev/null | tail -1")
    out("   %-8s sudo -> %s" % (n, (r.get("stdout") or r.get("stderr",""))[:60]))

sec("5. JOURNAL D'AUDIT")
out(ra("echo kube | sudo -S tail -6 /var/log/helios-ssh-bridge.log 2>/dev/null"))

out("\n   JETON (pour le BPMN) : " + JETON)
with open(os.path.join(SP, "bridge_token.txt"), "w") as f: f.write(JETON)
a.close()
