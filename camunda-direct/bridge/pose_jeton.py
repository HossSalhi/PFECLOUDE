# -*- coding: utf-8 -*-
"""Ajoute HELIOS_BRIDGE_TOKEN au drop-in camunda8, avec la procedure de redemarrage SURE.
c8run -detached echappe a systemd : un 'restart' naif laisse Camunda DOWN."""
import paramiko, sys, os, time
def out(s): sys.stdout.buffer.write((str(s)+"\n").encode("utf-8","replace")); sys.stdout.flush()
def sec(t): out("\n" + "="*74 + "\n  " + t + "\n" + "="*74)
SP = os.path.dirname(__file__)
JETON = open(os.path.join(SP, "bridge_token.txt")).read().strip()
a = paramiko.SSHClient(); a.set_missing_host_key_policy(paramiko.AutoAddPolicy())
a.connect("192.168.148.161", username="kube", password="kube", timeout=20, allow_agent=False, look_for_keys=False)
def r(c,t=240):
    _,o,e=a.exec_command(c,timeout=t)
    return "\n".join(l for l in (o.read().decode(errors="replace")+e.read().decode(errors="replace")).splitlines() if "password for" not in l).strip()
def ports(): return "8080=%s 26500=%s 8086=%s" % (r("ss -tln|grep -c ':8080'"), r("ss -tln|grep -c ':26500'"), r("ss -tln|grep -c ':8086'"))

sec("0. ETAT AVANT (doit etre sain)")
out("   camunda8 : %s | %s" % (r("systemctl is-active camunda8"), ports()))
if "healthy" not in r("curl -s --max-time 8 http://localhost:8080/v2/topology"):
    out("   !! Camunda n'est pas sain, on n'y touche pas"); sys.exit(1)
out("   topology : healthy")

sec("1. AJOUT DU JETON AU DROP-IN EXISTANT")
CONF = """#!/bin/bash
set -e
F=/etc/systemd/system/camunda8.service.d/helios.conf
grep -v 'HELIOS_BRIDGE_TOKEN' "$F" > /tmp/h.conf
echo 'Environment=HELIOS_BRIDGE_TOKEN=%s' >> /tmp/h.conf
echo 'Environment=SECRET_HELIOS_BRIDGE_TOKEN=%s' >> /tmp/h.conf
echo 'Environment=CONNECTOR_SECRET_HELIOS_BRIDGE_TOKEN=%s' >> /tmp/h.conf
cp /tmp/h.conf "$F"
chmod 600 "$F"
rm -f /tmp/h.conf
systemctl daemon-reload
echo POSE
""" % (JETON, JETON, JETON)
sftp = a.open_sftp()
with sftp.open("/tmp/conf.sh","w") as f: f.write(CONF)
sftp.close()
out("   " + r("echo kube | sudo -S bash /tmp/conf.sh 2>&1 | tail -1"))
r("rm -f /tmp/conf.sh")

sec("2. ARRET PROPRE (verification port par port)")
r("echo kube | sudo -S systemctl stop camunda8")
time.sleep(12)
out("   apres stop : " + ports())
for i in range(6):
    if ports() == "8080=0 26500=0 8086=0":
        out("   >>> tous les ports liberes"); break
    out("   orphelins -> kill cible")
    for pat in ("c8run","camunda-zeebe","connector-runtime","org.springframework.boot.loader"):
        r("echo kube | sudo -S pkill -9 -f '%s' 2>/dev/null || true" % pat)
    time.sleep(10)
    out("   ports      : " + ports())

sec("3. DEMARRAGE")
r("echo kube | sudo -S systemctl reset-failed camunda8 2>/dev/null || true")
r("echo kube | sudo -S systemctl start camunda8")
ok=False
for i in range(18):
    time.sleep(20)
    z = r("curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://localhost:8080/v2/topology")
    c86 = r("ss -tln|grep -c ':8086'")
    out("   [%3ds] camunda8=%s topology=%s | %s" % (20*(i+1), r("systemctl is-active camunda8"), z, ports()))
    if z == "200" and c86 == "1":
        out("   >>> ZEEBE + CONNECTEURS OPERATIONNELS"); ok=True; break

sec("4. VERIFICATIONS")
out("   camunda8       : " + r("systemctl is-active camunda8"))
out("   topology       : " + ("healthy" if "healthy" in r("curl -s --max-time 8 http://localhost:8080/v2/topology") else "KO"))
out("   ports          : " + ports())
out("   jeton herite   : " + r("systemctl show camunda8 -p Environment | tr ' ' '\\n' | grep -c 'HELIOS_BRIDGE_TOKEN'") + " variable(s)")
out("   K8S_TOKEN      : " + r("systemctl show camunda8 -p Environment | tr ' ' '\\n' | grep -c 'K8S_TOKEN=' ") + " (doit rester >0)")
out("   bridge         : " + r("systemctl is-active helios-ssh-bridge"))
out("   process defini : " + ("present" if "helios-camunda-full-cycle" in r("curl -s --max-time 20 -X POST http://localhost:8080/v2/process-definitions/search -H 'Content-Type: application/json' -d '{\"page\":{\"limit\":20}}'") else "ABSENT"))
a.close()
