#!/usr/bin/env python3
"""
HA-01 — PRÉFLIGHT SPÉCIFIQUE du 4e scénario réel : perte brutale de .162 (k8s-lb2, LB ACTIF du DNS). LECTURE SEULE : n'éteint, n'arrête et ne coupe RIEN.

  python p5_162_preflight.py [--capacity-s 30] [--no-revive-test] [--no-capacity]

Actions autorisées de cet outil : lire l'état (kubectl, etcdctl, vmrun list, DNS, HAProxy), un court test de charge dirigé vers .160 SEUL, un test du filet de relance
(vm_revive.ps1 avec VM en marche : il doit constater « tourne déjà »), et l'écriture d'un fichier de résultats. Il ne lance pas `vmrun stop`, n'arrête aucun service.

Sortie : tableau PASS/FAIL (+ WARN = information à lire), JSON results/<date>/p5-162-preflight-<horodatage>.json que p4_vmstop.py exige (< 30 min, tout PASS)
avant d'accepter d'éteindre .162. Code retour : 0 = tout PASS · 1 = au moins un FAIL.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time

import ha01_ops as O
import p4_vmstop as P4

D = O.ha01_hoststall
VMX = r"C:\Users\houss\Documents\Virtual Machines\k8s-lb2\k8s-lb2.vmx"
RESULTS = []


def add(sec, name, ok, detail="", level="FAIL"):
    """ok True -> PASS ; ok False -> FAIL (ou WARN si level='WARN')."""
    st = "PASS" if ok else level
    RESULTS.append({"section": sec, "check": name, "status": st, "detail": str(detail)[:260]})
    print("%-5s [%s] %-84s %s" % (st, sec, name[:84], str(detail)[:120]), flush=True)


def ps(cmd, timeout=60):
    r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
    return (r.stdout + r.stderr).strip()


def safe(fn):
    def w(*a, **k):
        try:
            return fn(*a, **k)
        except Exception as e:  # noqa: BLE001
            add("outil", "exception dans %s" % fn.__name__, False, "%s: %s" % (type(e).__name__, e))
    return w


@safe
def sec_etat():
    s = "A-etat"
    out = ps("& 'C:\\Program Files (x86)\\VMware\\VMware Workstation\\vmrun.exe' -T ws list")
    names = ["k8s-lb2", "k8s-aiCamunda", "k8s-master", "k8s-monitoring", "k8s-worker1", "k8s-worker2"]
    add(s, "6 VM en marche (vmrun list), dont k8s-lb2", all(n in out for n in names) and "Total running VMs: 6" in out, out.splitlines()[0] if out else "vide")
    bad = []
    for h in ("153", "155", "158", "160", "161", "162"):
        rc, o = O.ssh(h, "echo up", timeout=15)
        if o != "up":
            bad.append(h)
    add(s, "SSH répond sur les 6 VM", not bad, "KO: %s" % bad if bad else "6/6")
    ns = O.nodes_state()
    add(s, "3 nœuds Kubernetes Ready", len(ns) == 3 and all(v == "Ready" for v in ns.values()), ns)
    bp = O.unhealthy_pods()
    add(s, "aucun pod non sain", not bp, bp[:3])
    st = O.cnpg_state()
    add(s, "PostgreSQL (CNPG) sain 2/2", st.startswith("Cluster in healthy state") and st.endswith("|2"), st)
    for dep in ("ha01-canary-w1", "ha01-canary-w2"):
        age = O.canary_ack_age(dep)
        add(s, "canari %s écrit depuis < 30 s" % dep, age is not None and age < 30, None if age is None else round(age, 1))
    rc, o = O.kubectl("-n traefik get pods --no-headers")
    add(s, "Traefik : 2 pods Ready (.155 et .158)", o.count("1/1") == 2 and o.count(" Running ") == 2, o.replace("\n", " | ")[:100])
    for lb in ("160", "162"):
        up, bad2 = O.haproxy_all_up(lb)
        ok, d = O.lb_direct_ok(lb)
        add(s, "HAProxy .%s : tous les serveurs UP, web+API directs 200" % lb, up and ok, "%s %s" % (bad2, d))
        for n in ("155", "158"):
            rc, o = O.ssh(lb, "curl -s -o /dev/null -m 3 -w '%%{http_code}' http://192.168.148.%s:8082/ping" % n, timeout=20)
            add(s, "Traefik /ping .%s joignable depuis le LB .%s" % (n, lb), o.strip() == "200", o)


@safe
def sec_etcd():
    s = "B-etcd"
    rc, out = O.ssh("158", O.etcdctl("member list -w json") + " 2>/dev/null", timeout=30)
    ml = json.loads(out)["members"]
    ids = {m["ID"]: m["clientURLs"][0].split("//")[1].split(":")[0].split(".")[-1] for m in ml if m.get("clientURLs")}
    add(s, "5 membres votants (aucun learner), dont .162", len(ml) == 5 and not any(m.get("isLearner") for m in ml) and "162" in ids.values(), sorted(ids.values()))
    rc, out = O.ssh("158", O.etcdctl("endpoint status -w json") + " 2>/dev/null", timeout=30)
    st = json.loads(out)
    applied = {e["Endpoint"].split(":")[1][-3:]: e["Status"]["raftAppliedIndex"] for e in st}
    spread = max(applied.values()) - min(applied.values())
    lead = [e["Endpoint"].split(":")[1][-3:] for e in st if e["Status"]["header"]["member_id"] == e["Status"]["leader"]]
    tries, ok5 = 0, False
    while tries < 3 and not ok5:                       # `.153` a des rafales CPU (33 en 6 h) : une mesure isolée peut tomber dessus -> 3 essais espacés de 12 s
        tries += 1
        ok5 = O.etcd_healthy() == 5
        if not ok5 and tries < 3:
            time.sleep(12)
    add(s, "5/5 sains et synchronisés (écart d'index appliqué <= 5)", ok5 and spread <= 5, "écart=%d, essais=%d" % (spread, tries))
    add(s, "leader actuel (information)", True, ".%s ; terme %s" % (lead[0] if lead else "?", st[0]["Status"]["raftTerm"]), level="WARN")
    ver = st[0]["Status"]["version"]
    add(s, "version etcd >= 3.4 (move-leader disponible)", tuple(int(x) for x in ver.split(".")[:2]) >= (3, 4), ver)
    det = D.LiveDetector([O.NET + x for x in O.ETCD_ALL], period=5.0)
    det.start()
    time.sleep(16)
    cur = {k.split(".")[-1]: v for k, v in det.current().items()}
    det.stop()
    lim = {"153": 0.5, "162": 0.05}                    # `.153` (nœud fragile) : 500 ms ; `.162` (candidat leader) : 50 ms ; autres : 100 ms
    add(s, "fsync WAL p99 : .162 < 50 ms (leader possible), .153 < 500 ms (nœud fragile), les autres < 100 ms",
        all(v is not None and v < lim.get(k, 0.1) for k, v in cur.items()) and len(cur) == 5, {k: ("-" if v is None else "%.0f ms" % (v * 1000)) for k, v in cur.items()})
    mid162 = [k for k, v in ids.items() if v == "162"]
    add(s, "transfert du leader vers .162 SÛR (membre votant, synchrone, rapide)", bool(mid162) and spread <= 5 and (cur.get("162") or 9) < 0.05,
        "commande préparée : etcdctl --endpoints=<leader> move-leader %x (NON exécutée)" % (mid162[0] if mid162 else 0), level="FAIL")
    rc, o = O.ssh("160", "curl -s 'http://127.0.0.1:9090/api/v1/query' --data-urlencode 'query=max(increase(etcd_server_leader_changes_seen_total[1h]))' | python3 -c \"import sys,json; r=json.load(sys.stdin)['data']['result']; print(round(float(r[0]['value'][1]),2) if r else 0)\"", timeout=30)
    try:
        ch = float(o.strip())
    except ValueError:
        ch = 99
    add(s, "changements de leader etcd sur 1 h <= 2 (règle ETCD_LEADER_FLAPPING > 5 non déclenchable par l'essai)", ch <= 2, ch)


@safe
def sec_dns():
    s = "C-dns"
    mode, pub, status = {}, {}, {}
    for lb in ("160", "162"):
        st = O.dns_state(lb)
        mode[lb], pub[lb] = st.get("mode"), st.get("publish")
        status[lb] = {k: v.get("status") for k, v in (st.get("lbs") or {}).items()}
    add(s, "2 contrôleurs en mode enforce", mode == {"160": "enforce", "162": "enforce"}, mode)
    add(s, "les 2 contrôleurs voient .160 et .162 UP", all(v == {"162": "UP", "160": "UP"} for v in status.values()), status)
    add(s, "publication nominale = [.162] seul sur les 2 contrôleurs (LB actif .162)", pub["160"] == pub["162"] == ["192.168.148.162"], pub)
    a160, a162 = O.dns_answers("160"), O.dns_answers("162")
    ok = a160 == a162 and all(v[0] == ["192.168.148.162"] and v[1] == 30 for v in a160.values())
    add(s, "dnsmasq .160 et .162 : même réponse (.162, TTL 30) pour helios et api.helios", ok, a160)
    import hashlib as h
    repo = h.sha256(open(os.path.join(O.HERE, "..", "dns", "helios-dns-health.py"), "rb").read().replace(b"\r\n", b"\n")).hexdigest()[:16]
    dep = {}
    for lb in ("160", "162"):
        rc, o = O.ssh(lb, "sha256sum /usr/local/bin/helios-dns-health.py | cut -c1-16; systemctl cat helios-dns-health | grep -o 'HELIOS_DNS_ORDER=[0-9,]*'", timeout=25)
        dep[lb] = o.replace("\n", " ")
    add(s, "contrôleurs déployés == dépôt et ordre 162,160", all(repo in v and "HELIOS_DNS_ORDER=162,160" in v for v in dep.values()), dep)
    hosts = ps("Get-Content C:\\Windows\\System32\\drivers\\etc\\hosts | Select-String 'helios.orange.tn' | ForEach-Object { $_.Line.Trim() }")
    ips = re.findall(r"(\d+\.\d+\.\d+\.\d+)\s+helios\.orange\.tn", hosts)
    add(s, "Windows : hosts liste .160 AVANT .162 (web et api_app immunisés contre la perte de .162)", ips[:2] == ["192.168.148.160", "192.168.148.162"], ips)
    nrpt = ps("(Get-DnsClientNrptPolicy | Where-Object { $_.Namespace -eq '.helios.orange.tn' } | ForEach-Object { $_.NameServers -join ',' })")
    add(s, "Windows : NRPT .helios.orange.tn -> .160,.162", nrpt.strip() == "192.168.148.160,192.168.148.162", nrpt)
    for h2 in ("155", "158"):
        rc, o = O.ssh(h2, "grep -E '^nameserver' /etc/resolv.conf | head -2 | tr '\\n' ' '", timeout=20)
        add(s, "nœud .%s : /etc/resolv.conf = .160 puis .162 (sans cache système)" % h2, o.startswith("nameserver 192.168.148.160 nameserver 192.168.148.162"), o)
    rc, o = O.ssh("160", "systemctl is-active dnsmasq helios-dns-health haproxy | tr '\\n' ' '", timeout=20)
    add(s, ".160 : dnsmasq, contrôleur DNS et HAProxy actifs (survivant)", o.strip() == "active active active", o)


@safe
def sec_capacite(seconds):
    s = "D-capacite"
    rc, o = O.ssh("160", "echo $(awk '{print $1}' /proc/loadavg) $(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo)", timeout=20)
    load, mem = float(o.split()[0]), int(o.split()[1])
    add(s, ".160 : load1 < 2 et MemAvailable >= 500 Mo avant essai", load < 2 and mem >= 500, "load1=%.2f mem=%d Mo" % (load, mem))
    rc, o = O.ssh("155", "ok=0; ko=0; for i in $(seq 1 20); do if kubectl --server=https://192.168.148.160:6443 --tls-server-name=api.helios.orange.tn --request-timeout=5s get --raw=/readyz >/dev/null 2>&1; then ok=$((ok+1)); else ko=$((ko+1)); fi; done; echo ok=$ok ko=$ko", timeout=120)
    add(s, "API Kubernetes AUTHENTIFIÉE via .160 seul : 20/20", o.strip() == "ok=20 ko=0", o)
    if seconds <= 0:
        return
    out_f = os.path.join(os.environ.get("CLAUDE_JOB_DIR", O.RES), "tmp", "cap160-preflight.jsonl")
    os.makedirs(os.path.dirname(out_f), exist_ok=True)
    p = subprocess.run([sys.executable, os.path.join(O.PROBES, "ha01_load.py"), "--rps", "20", "--duration", str(seconds), "--out", out_f, "--ip", O.NET + "160"],
                       capture_output=True, text=True, timeout=seconds + 60)
    try:
        j = json.loads(p.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        j = {}
    add(s, "charge 20 req/s dirigée vers .160 SEUL pendant %d s : 100 %% et p95 < 1500 ms" % seconds, j.get("ok_pct") == 100.0 and (j.get("p95_ms") or 9e9) < 1500, j)
    rc, o = O.ssh("160", "echo $(awk '{print $1}' /proc/loadavg) $(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo)", timeout=20)
    add(s, ".160 après la charge : MemAvailable >= 500 Mo", int(o.split()[1]) >= 500, o)


@safe
def sec_sauvegardes():
    s = "E-sauvegardes"
    cmd = r"""
now=$(date +%s); cd /opt/helios-backups/ha01/offsite || exit 1
P=$(ls -t pg/*.sql.gz | head -1); E=$(ls -t etcd/*.db | head -1)
echo "PG $(( (now - $(stat -c %Y $P)) / 60 )) $(stat -c %s $P) $(gzip -t $P && echo ok || echo ko) $(zcat $P | grep -c '^CREATE TABLE') $P"
echo "ETCD $(( (now - $(stat -c %Y $E)) / 60 )) $(stat -c %s $E) $(sha256sum -c $E.sha256 >/dev/null 2>&1 && echo ok || echo ko) 0 $E"
echo "COUNT $(ls pg/*.sql.gz | wc -l) $(ls etcd/*.db | wc -l)"
echo "DISK $(df -BG --output=avail . | tail -1 | tr -dc 0-9)"
"""
    rc, out = O.ssh("161", "sudo -n bash -c '%s'" % cmd.replace("'", "'\\''"), timeout=90)
    d = {l.split()[0]: l.split()[1:] for l in out.splitlines() if l.strip()}
    pg, et = d.get("PG", []), d.get("ETCD", [])
    add(s, "dump PostgreSQL sur .161 (hors VM) : <= 120 min, gzip -t OK, > 10 Ko, >= 10 tables",
        bool(pg) and int(pg[0]) <= 120 and pg[2] == "ok" and int(pg[1]) > 10240 and int(pg[3]) >= 10, pg[:4], level="WARN")
    add(s, "snapshot etcd sur .161 (hors VM) : <= 120 min, sha256 OK, > 10 Mo",
        bool(et) and int(et[0]) <= 120 and et[2] == "ok" and int(et[1]) > 10 * 1024 * 1024, et[:3], level="WARN")
    add(s, "intégrité des derniers fichiers (gzip -t / sha256) sans condition d'âge", bool(pg) and pg[2] == "ok" and bool(et) and et[2] == "ok", "pg=%s etcd=%s" % (pg[2:3], et[2:3]))
    add(s, "rétention : < 25 fichiers par type (le script n'en supprime qu'au-delà de 30)", bool(d.get("COUNT")) and all(int(x) < 25 for x in d["COUNT"]), d.get("COUNT"))
    add(s, "espace libre sur .161 >= 5 Go", bool(d.get("DISK")) and int(d["DISK"][0]) >= 5, d.get("DISK"))
    add(s, "sauvegarde FRAÎCHE juste avant l'arrêt (étape du lancement : helios-offsite-backup.service)", True, "planifiée dans les commandes du lancement", level="WARN")


@safe
def sec_relance(do_test):
    s = "F-relance"
    vmrun = r"C:\Program Files (x86)\VMware\VMware Workstation\vmrun.exe"
    add(s, "vmrun présent et .vmx de k8s-lb2 présent", os.path.exists(vmrun) and os.path.exists(VMX), VMX)
    add(s, "p4_vmstop : relance après 600 s d'observation ; filet Windows indépendant à observe+900 s ; abandon -> relance immédiate ; `finally` -> relance d'urgence", True,
        "vm_revive.ps1 + vmrun start nogui (voir p4_vmstop.py)", level="WARN")
    if do_test:
        tmp = os.path.join(os.environ.get("CLAUDE_JOB_DIR", O.RES), "tmp")
        os.makedirs(tmp, exist_ok=True)
        l1, l2 = os.path.join(tmp, "p5-revive1.log"), os.path.join(tmp, "p5-revive2.log")
        for f in (l1, l2, os.path.join(tmp, "p5-revive2.stop")):
            if os.path.exists(f):
                os.rename(f, f + ".%d.old" % int(time.time()))
        ps_file = os.path.join(O.HERE, "vm_revive.ps1")
        subprocess.run(["powershell", "-NoProfile", "-File", ps_file, "-Vmx", VMX, "-Seconds", "6", "-StopFile", os.path.join(tmp, "p5-revive1.stop"), "-LogFile", l1], timeout=60, capture_output=True)
        t1 = open(l1, encoding="utf-8-sig", errors="replace").read()
        add(s, "test du filet n°1 : échéance atteinte, VM EN MARCHE -> « tourne déjà », aucune action", "tourne" in t1 and "vmrun start" not in t1, t1.strip().splitlines()[-1][-70:])
        p = subprocess.Popen(["powershell", "-NoProfile", "-File", ps_file, "-Vmx", VMX, "-Seconds", "60", "-StopFile", os.path.join(tmp, "p5-revive2.stop"), "-LogFile", l2],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(3)
        open(os.path.join(tmp, "p5-revive2.stop"), "w").write("disarmed")
        p.wait(timeout=40)
        t2 = open(l2, encoding="utf-8-sig", errors="replace").read()
        add(s, "test du filet n°2 : désarmement par fichier-témoin avant l'échéance -> fin sans action", "sarm" in t2 and "vmrun start" not in t2, t2.strip().splitlines()[-1][-70:])
    out = ps("& '%s' -T ws list" % vmrun)
    add(s, "k8s-lb2 toujours en marche après les tests du filet", "k8s-lb2" in out, "")


@safe
def sec_hote():
    s = "G-hote"
    h = O.host_avail_mb()
    mn, n = P4.ram_history()
    add(s, "RAM hôte >= 2000 Mo MAINTENANT", h >= 2000, "%d Mo (récupérable : ops\\trim_host_ram.ps1, non destructif)" % h)
    add(s, "RAM hôte >= 2000 Mo aux 3 points de contrôle (~10 min, ~5 min, maintenant) — exigence du plan", n >= 3 and mn >= 2000, "min=%s Mo, points=%d" % (mn, n))
    free = float(ps("[math]::Round((Get-PSDrive C).Free/1GB,1)").replace(",", "."))
    add(s, "disque C: libre >= 20 Go (redémarrage de .162 : ~1,5 Go d'écriture)", free >= 20, "%.1f Go (C: à 93 %% — risque de gel d'E/S connu)" % free)
    ws = D.windows(time.time() - 600, time.time())
    add(s, "aucun gel d'E/S de l'hôte dans les 10 dernières minutes", ws == [], D.describe(ws))
    add(s, "historique des gels d'E/S (information) : 7 en ~30 h, 2 relances sur 5 suivies d'un gel", True, "voir GATE-4.md R4", level="WARN")
    add(s, "harnais actif (sondes Windows < 15 s, échantillonneur < 60 s)", O.file_age(O.PROBE_WIN) < 15 and O.file_age(O.SAMPLES) < 60, "%.0fs/%.0fs" % (O.file_age(O.PROBE_WIN), O.file_age(O.SAMPLES)))
    rc, o = O.ssh("161", "systemctl is-active ha01-probe-161", timeout=20)
    add(s, "sonde .161 (ha01-probe-161) active", o.strip() == "active", o)
    rc, o = O.ssh("162", "awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo", timeout=20)
    add(s, ".162 : MemAvailable >= 500 Mo (avant arrêt)", o.strip().isdigit() and int(o) >= 500, o)


@safe
def sec_alimentation():
    """Ajout du 4 nov. 2026 (reprise après la coupure de courant du PC, arrêt système à 17:57:51Z) : l'hôte doit être alimenté par le SECTEUR avec une réserve de batterie.
    LECTURE SEULE (Windows.Forms SystemInformation.PowerStatus). Poste sans batterie : seul le secteur est exigé. Aucun autre contrôle n'est modifié."""
    s = "I-alimentation"
    nom = "PC sur secteur (PowerLineStatus=Online) et batterie >= 40 % (ou aucune batterie)"
    out = ps("Add-Type -AssemblyName System.Windows.Forms; $p=[System.Windows.Forms.SystemInformation]::PowerStatus; "
             "'{0}|{1}|{2}' -f $p.PowerLineStatus,[int]($p.BatteryLifePercent*100),$p.BatteryChargeStatus")
    parts = (out.strip().splitlines()[-1] if out.strip() else "").split("|")
    if len(parts) != 3 or not parts[1].strip().isdigit():
        add(s, nom, False, "lecture impossible : %s" % out[:120])
        return
    ligne, pct, charge = parts[0].strip(), int(parts[1]), parts[2].strip()
    sans_batterie = "NoSystemBattery" in charge
    add(s, nom, ligne == "Online" and (sans_batterie or pct >= 40), "%s ; batterie %s ; BatteryChargeStatus=%s" % (ligne, "absente" if sans_batterie else "%d %%" % pct, charge))


@safe
def sec_garde_fous():
    s = "H-garde-fous"
    p = os.path.join(O.RES, "CONFIRMATION-162.txt")
    add(s, "AUCUNE confirmation valide pour .162 : p4_vmstop.py REFUSE d'éteindre (garde-fou armé)", not os.path.exists(p), "CONFIRMATION-162.txt absent" if not os.path.exists(p) else "PRÉSENT : %s" % p)
    bad = []
    for h in ("153", "155", "158", "160", "161", "162"):
        rc, o = O.ssh(h, "sudo -n sh -c \"iptables -S OUTPUT; iptables -S FORWARD; iptables -S INPUT\" 2>/dev/null | grep -c 'ha01-'; systemctl list-units --all --no-legend 'ha01-p*' 2>/dev/null | grep -vc 'ha01-probe-161'", timeout=25)
        if o.split() != ["0", "0"]:
            bad.append((h, o.replace("\n", "/")))
    add(s, "aucune règle iptables ha01-* ni minuterie ha01-p* résiduelle sur les 6 VM", not bad, bad or "6/6 propres")
    # VMDOWN-POLICY-20261105 : l'agent legacy :9999 est retiré ; la « pause » de p4_vmstop = drapeau de maintenance de la VM visée.
    add(s, "mécanisme de maintenance disponible (dossier helios-maintenance présent, aucun drapeau actif)",
        ps(r"if (Test-Path 'C:\Users\houss\helios-maintenance') { (Get-ChildItem 'C:\Users\houss\helios-maintenance' -Filter *.flag -ErrorAction SilentlyContinue | Measure-Object).Count } else { 'ABSENT' }").strip() == "0", "")
    rc, o = O.ssh("153", "awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo", timeout=20)
    add(s, ".153 (non touchée) : Ready et MemAvailable >= 500 Mo", o.strip().isdigit() and int(o) >= 500, o)
    ns = argparse.Namespace(confirmed=False)
    chk = P4.preflight(ns, "162", lambda m: None, True)
    badc = [c["check"] for c in chk if not c["ok"]]
    add(s, "préflight générique de p4_vmstop.py --vm 162 (dry-run) : tous OK", not badc, badc[:4] or "%d contrôles OK" % len(chk))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--capacity-s", type=int, default=30)
    ap.add_argument("--no-capacity", action="store_true")
    ap.add_argument("--no-revive-test", action="store_true")
    a = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    t0 = time.time()
    print("PRÉFLIGHT .162 — %s UTC — LECTURE SEULE (aucune extinction, aucun arrêt de service, aucune coupure)" % O.iso())
    sec_etat()
    sec_etcd()
    sec_dns()
    sec_capacite(0 if a.no_capacity else a.capacity_s)
    sec_sauvegardes()
    sec_relance(not a.no_revive_test)
    sec_hote()
    sec_alimentation()
    sec_garde_fous()
    nf = sum(1 for r in RESULTS if r["status"] == "FAIL")
    nw = sum(1 for r in RESULTS if r["status"] == "WARN")
    npass = sum(1 for r in RESULTS if r["status"] == "PASS")
    print("\nRÉSUMÉ : %d PASS · %d WARN (à lire) · %d FAIL — %.0f s" % (npass, nw, nf, time.time() - t0))
    for r in RESULTS:
        if r["status"] == "FAIL":
            print("  FAIL [%s] %s : %s" % (r["section"], r["check"], r["detail"]))
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    p = os.path.join(O.RES, "p5-162-preflight-%s.json" % stamp)
    json.dump({"ts": time.time(), "iso": O.iso(), "all_ok": nf == 0, "pass": npass, "warn": nw, "fail": nf, "checks": RESULTS}, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("résultats :", p)
    return 0 if nf == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
