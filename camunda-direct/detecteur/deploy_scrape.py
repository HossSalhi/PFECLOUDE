# -*- coding: utf-8 -*-
"""Installe le rafraichisseur de cibles + le job file_sd sur .160."""
import paramiko, sys, os, time
def out(s): sys.stdout.buffer.write((str(s)+"\n").encode("utf-8","replace")); sys.stdout.flush()
def sec(t): out("\n" + "="*74 + "\n  " + t + "\n" + "="*74)
SP = os.path.dirname(__file__)
mon = paramiko.SSHClient(); mon.set_missing_host_key_policy(paramiko.AutoAddPolicy())
mon.connect("192.168.148.160", username="kube", password="kube", timeout=20, allow_agent=False, look_for_keys=False)
def r(c,t=150):
    _,o,e=mon.exec_command(c,timeout=t)
    return "\n".join(l for l in (o.read().decode(errors="replace")+e.read().decode(errors="replace")).splitlines() if "password for" not in l).strip()

sec("1. RAFRAICHISSEUR DE CIBLES")
sftp = mon.open_sftp(); sftp.put(os.path.join(SP,"maj_cibles.py"), "/tmp/maj_cibles.py"); sftp.close()
UNIT = """[Unit]
Description=HELIOS - rafraichit les cibles pods pour Prometheus
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /opt/helios/maj_cibles.py
"""
TIMER = """[Unit]
Description=HELIOS - rafraichissement des cibles toutes les 30s
[Timer]
OnBootSec=30
OnUnitActiveSec=30
AccuracySec=5
[Install]
WantedBy=timers.target
"""
sftp = mon.open_sftp()
with sftp.open("/tmp/u.service","w") as f: f.write(UNIT)
with sftp.open("/tmp/u.timer","w") as f: f.write(TIMER)
sftp.close()
INSTALL = """#!/bin/bash
set -e
mkdir -p /opt/helios /etc/prometheus/targets
cp /tmp/maj_cibles.py /opt/helios/maj_cibles.py
chmod 750 /opt/helios/maj_cibles.py
cp /tmp/u.service /etc/systemd/system/helios-cibles.service
cp /tmp/u.timer /etc/systemd/system/helios-cibles.timer
systemctl daemon-reload
systemctl enable --now helios-cibles.timer
/usr/bin/python3 /opt/helios/maj_cibles.py
chown -R prometheus:prometheus /etc/prometheus/targets 2>/dev/null || true
echo INSTALLE
"""
sftp = mon.open_sftp()
with sftp.open("/tmp/i.sh","w") as f: f.write(INSTALL)
sftp.close()
out("   " + r("echo kube | sudo -S bash /tmp/i.sh 2>&1 | tail -2").replace("\n"," | "))
out("   timer   : " + r("systemctl is-active helios-cibles.timer"))
out("   cibles  : " + r("echo kube | sudo -S python3 -c \"import json;print(len(json.load(open('/etc/prometheus/targets/pods.json'))),'pods a scraper')\" 2>&1 | tail -1"))
out("   exemple : " + r("echo kube | sudo -S python3 -c \"import json;d=json.load(open('/etc/prometheus/targets/pods.json'));print(d[0]['labels']['app'],'->',d[0]['labels']['__metrics_path__'][:70]) if d else print('vide')\" 2>&1 | tail -1"))

sec("2. JOB file_sd DANS PROMETHEUS")
JOB = """
  - job_name: kubernetes-pods
    scheme: https
    tls_config:
      insecure_skip_verify: true
    authorization:
      credentials_file: /etc/prometheus/scraper-token
    file_sd_configs:
      - files: ['/etc/prometheus/targets/pods.json']
        refresh_interval: 30s
"""
sftp = mon.open_sftp()
with sftp.open("/tmp/job.yml","w") as f: f.write(JOB)
sftp.close()
SH = """#!/bin/bash
set -e
cp /etc/prometheus/prometheus.yml /etc/prometheus/prometheus.yml.avant-pods
grep -q 'job_name: kubernetes-pods' /etc/prometheus/prometheus.yml && { echo DEJA; exit 0; }
cat /tmp/job.yml >> /etc/prometheus/prometheus.yml
if ! promtool check config /etc/prometheus/prometheus.yml >/dev/null 2>&1; then
  cp /etc/prometheus/prometheus.yml.avant-pods /etc/prometheus/prometheus.yml
  echo INVALIDE_RESTAURE; exit 1
fi
echo AJOUTE
"""
sftp = mon.open_sftp()
with sftp.open("/tmp/a.sh","w") as f: f.write(SH)
sftp.close()
res = r("echo kube | sudo -S bash /tmp/a.sh 2>&1 | tail -1")
out("   " + res)
if "INVALIDE" in res: sys.exit(1)
out("   validation : " + r("echo kube | sudo -S promtool check config /etc/prometheus/prometheus.yml 2>&1 | tail -1"))
r("echo kube | sudo -S systemctl reload prometheus 2>/dev/null || echo kube | sudo -S systemctl restart prometheus")
time.sleep(15)
out("   prometheus : " + r("systemctl is-active prometheus"))

sec("3. LES METRIQUES APPLICATIVES ARRIVENT-ELLES ?")
for i in range(8):
    time.sleep(15)
    n = r("curl -s --max-time 10 'http://localhost:9090/api/v1/query?query=count(http_requests_total)' | grep -oE '\\[[0-9.]+,\"[0-9]+\"\\]' | head -1")
    ok = r("curl -s --max-time 10 'http://localhost:9090/api/v1/targets?state=active' | python3 -c \"import sys,json;d=json.load(sys.stdin)['data']['activeTargets'];k=[t for t in d if t['labels'].get('job')=='kubernetes-pods'];print('%d cibles, %d up'%(len(k),sum(1 for t in k if t['health']=='up')))\" 2>/dev/null")
    out("   [+%ds] %s | http_requests_total : %s" % (15*(i+1), ok, n or "(pas encore)"))
    if n: break
err = r("curl -s --max-time 10 'http://localhost:9090/api/v1/targets?state=active' | python3 -c \"import sys,json;d=json.load(sys.stdin)['data']['activeTargets'];[print('     ',t['labels'].get('app'),t['health'],t.get('lastError','')[:70]) for t in d if t['labels'].get('job')=='kubernetes-pods'][:4]\" 2>/dev/null")
out("\n   detail des cibles :"); out(err or "     (aucune)")
mon.close()
