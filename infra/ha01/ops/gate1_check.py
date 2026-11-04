#!/usr/bin/env python3
"""
HA-01 — évaluation AUTOMATIQUE et reproductible du GATE-1 (Phase 1 terminée et vérifiée). Lecture seule.
  python gate1_check.py [--since-change-min 30] [--shadow-hours 24] > results/<date>/GATE-1-mesures.md     (0 = VALIDÉ)
Critères : cf. infra/ha01/README-HA-01.md §Phase 1. Les chiffres sont recalculés à chaque exécution (rien n'est codé en dur).
"""
import argparse
import csv
import hashlib
import io
import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..", "probes")))
import ha01_canary_report  # noqa: E402
import ha01_hoststall  # noqa: E402
import ha01_report  # noqa: E402

# Sortie toujours en UTF-8 : sous Windows le défaut est cp1252, où « ✅ » fait planter print(), y compris quand la sortie
# est redirigée vers un fichier (cas de la commande planifiée du GATE-1). Corrigé le 4 nov. après un essai à blanc.
for _flux in (sys.stdout, sys.stderr):
    try:
        _flux.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

BASE = os.path.normpath(os.path.join(HERE, ".."))
RES = os.path.join(BASE, "results", "2026-11-03")
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "LogLevel=ERROR"]
rows = []


def ssh(ip, cmd, timeout=60):
    try:
        r = subprocess.run(SSH + ["kube@192.168.148.%s" % ip, cmd], capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip()
    except subprocess.TimeoutExpired:
        return 124, ""


def add(g, n, ok, d):
    rows.append((g, n, bool(ok), str(d)))


def epoch(ts):
    return datetime.strptime(ts.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S.%f%z" if "." in ts else "%Y-%m-%dT%H:%M:%S%z").timestamp()


def k(cmd, timeout=60):
    return ssh(155, "kubectl --request-timeout=30s " + cmd, timeout)


def csv_rows(lb):
    txt = urllib.request.urlopen("http://192.168.148.%s:8404/;csv" % lb, timeout=6).read().decode()
    rd = csv.reader(io.StringIO(txt))
    hdr = next(rd)
    hdr[0] = hdr[0].lstrip("# ")
    ix = {h: i for i, h in enumerate(hdr)}
    return [{h: r[i] for h, i in ix.items()} for r in rd if len(r) >= len(hdr)]


P161_LIVE = os.path.join(RES, "probe161-live.jsonl")


def fetch_probe161():
    """Copie le fichier VIVANT des sondes de .161 vers results/ (probe161-live.jsonl, remplacement atomique).
    Sans cela on jugerait une copie locale périmée (cas constaté le 4 nov. : arrêtée à 03:08 UTC). L'ancienne copie
    probe161-p0.jsonl n'est jamais touchée. Retourne (code, taille)."""
    tmp = P161_LIVE + ".tmp"
    try:
        with open(tmp, "wb") as fh:
            r = subprocess.run(SSH + ["kube@192.168.148.161", "cat /opt/helios-backups/ha01/probe161-p0.jsonl"],
                               stdout=fh, stderr=subprocess.PIPE, timeout=240)
        if r.returncode == 0 and os.path.getsize(tmp) > 0:
            os.replace(tmp, P161_LIVE)
            return 0, os.path.getsize(P161_LIVE)
        return r.returncode or 1, 0
    except (subprocess.TimeoutExpired, OSError):
        return 124, 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since-change-min", type=int, default=30, help="durée minimale d'observation après la DERNIÈRE modification")
    ap.add_argument("--shadow-hours", type=float, default=24)
    ap.add_argument("--last-change", help="ISO UTC de la dernière modification de la Phase 1 (défaut: lu dans phase1-journal.md)")
    a = ap.parse_args()
    now = time.time()
    repo_cfg = hashlib.sha256(open(os.path.join(BASE, "haproxy", "haproxy.cfg"), "rb").read()).hexdigest()
    # ---- C10
    for lb in (160, 162):
        rc, out = ssh(lb, "dpkg -l keepalived 2>/dev/null | grep -c '^ii'; systemctl is-active keepalived 2>&1 | head -1; ip -4 -br addr show ens33 | awk '{print $3}' | tr '\\n' ' '")
        add("C10", ".%d : keepalived absent, aucun VIP" % lb, out.splitlines()[0] == "0" and "192.168.148.159" not in out, out.replace("\n", " | "))
    # ---- C1
    for lb in (160, 162):
        rc, out = ssh(lb, "sha256sum /etc/haproxy/haproxy.cfg | cut -d' ' -f1; sudo -n haproxy -c -f /etc/haproxy/haproxy.cfg 2>&1 | tail -1; systemctl is-active haproxy")
        l = out.splitlines()
        add("C1", ".%d : config HAProxy = fichier canonique du dépôt, valide, service actif" % lb, len(l) == 3 and l[0] == repo_cfg and "valid" in l[1] and l[2] == "active", out.replace("\n", " | ")[:200])
        try:
            st = csv_rows(lb)
            apps = sorted(r["svname"] for r in st if r["pxname"] in ("ingress_http", "ingress_https") and r["svname"] not in ("FRONTEND", "BACKEND"))
            m = [r for r in st if r["pxname"] == "k8s_masters" and r["svname"] == "master"]
            up = all(r["status"].startswith("UP") for r in st if r["svname"] not in ("FRONTEND", "BACKEND"))
            add("C1", ".%d : backends applicatifs = .155/.158 seulement ; .153 en secours (bck=1) ; tous UP" % lb,
                apps == ["node-155", "node-155", "node-158", "node-158"] and m and m[0].get("bck") == "1" and up, "apps=%s bck(master)=%s tousUP=%s" % (apps, m[0].get("bck") if m else None, up))
        except Exception as e:  # noqa: BLE001
            add("C1", ".%d : stats HAProxy" % lb, False, e)
    # ---- C5 shadow
    for lb in (160, 162):
        rc, out = ssh(lb, "systemctl is-active helios-dns-health; systemctl show helios-dns-health -p ActiveEnterTimestamp --value; cat /etc/helios-dns-health/mode; "
                          "sudo -n journalctl -u helios-dns-health --no-pager --output=cat 2>/dev/null | grep -c -- '-> DOWN'; sudo -n journalctl -u helios-dns-health --no-pager --output=cat 2>/dev/null | grep -c 'décision: publier'")
        l = out.splitlines()
        try:
            t0 = datetime.strptime(l[1].replace(" UTC", ""), "%a %Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
            age_h = (now - t0) / 3600
        except Exception:  # noqa: BLE001
            age_h = 0
        add("C5", ".%d : contrôleur DNS actif en mode %s depuis %.1f h (>= %.0f h), 0 passage DOWN" % (lb, l[2] if len(l) > 2 else "?", age_h, a.shadow_hours),
            len(l) >= 5 and l[0] == "active" and age_h >= a.shadow_hours and l[3] == "0", " | ".join(l)[:200])
    # ---- C2
    rc, out = k("get node k8s-master -o jsonpath='{.spec.taints}'")
    add("C2", "taint control-plane NoSchedule sur .153", "NoSchedule" in out and "control-plane" in out, out)
    rc, out = k("-n ingress-nginx get pods --no-headers -o custom-columns=S:.status.phase,NODE:.spec.nodeName")
    nodes = sorted(l.split()[1] for l in out.splitlines() if l.split() and l.split()[0] == "Running")
    add("C2", "ingress-nginx : 2 réplicas Running, un par worker (.155/.158)", nodes == ["k8s-worker1", "k8s-worker2"], nodes)
    rc, out = k("-n ingress-nginx get pdb ingress-nginx-controller -o jsonpath='{.spec.minAvailable}'")
    add("C2", "PDB ingress-nginx minAvailable = 1", out.replace("'", "") == "1", out)
    rc, out = k("get pods -A --no-headers")
    bad = [l for l in out.splitlines() if len(l.split()) > 3 and l.split()[3] not in ("Running", "Completed")]
    add("C2", "aucun pod Pending/en défaut dans le cluster", not bad, bad[:5] or "aucun")
    rc, out = k("get pods -A -o wide --no-headers")
    on153 = sorted({l.split()[1].rsplit('-', 2)[0] if l.count('-') > 2 else l.split()[1] for l in out.splitlines() if "k8s-master" in l.split()})
    add("C2", "pods sur .153 = composants tolérés uniquement (informatif)", True, on153)
    # ---- C7
    rc, out = k("-n cnpg-system get deploy cnpg-controller-manager -o jsonpath='{.spec.template.spec.containers[0].args}'")
    add("C7", "opérateur CNPG : --leader-lease-duration=60 et --leader-renew-deadline=40", "leader-lease-duration=60" in out and "leader-renew-deadline=40" in out, out[:160])
    rc, out = k("-n cnpg-system get pods --no-headers -o custom-columns=S:.status.phase,R:.status.containerStatuses[0].restartCount,ST:.status.containerStatuses[0].state.running.startedAt")
    try:
        ph, rs, st = out.split()[:3]
        age_min = (now - epoch(st)) / 60
        add("C7", "opérateur CNPG : Running, 0 redémarrage depuis >= 60 min", ph == "Running" and rs == "0" and age_min >= 60, "restarts=%s, démarré il y a %.0f min" % (rs, age_min))
    except Exception:  # noqa: BLE001
        add("C7", "opérateur CNPG : état lisible", False, out)
    for ip in (155, 158):
        rc, out = ssh(ip, "/home/kube/bin/kubectl-cnpg version 2>&1 | head -1")
        add("C7", "plugin kubectl-cnpg présent sur .%d" % ip, "1.28" in out, out)
    # ---- C6
    rc, out = k("-n pfe get cronjob postgres-backup postgres-backup-b --no-headers -o custom-columns=N:.metadata.name,SUSP:.spec.suspend,LAST:.status.lastSuccessfulTime")
    add("C6", "CronJob A (inchangé) et B présents", "postgres-backup " in out + " " and "postgres-backup-b" in out, out.replace("\n", " | "))
    rc, out = k("-n pfe get pvc postgres-backups postgres-backups-b --no-headers")
    add("C6", "PVC postgres-backups (.158) et postgres-backups-b Bound", out.count("Bound") == 2, out.replace("\n", " | ")[:200])
    rc, out = ssh(161, "ls -1t /opt/helios-backups/ha01/offsite/pg/*.sql.gz 2>/dev/null | head -1 | xargs -r gzip -t && echo PG_OK; ls -1t /opt/helios-backups/ha01/offsite/etcd/*.db 2>/dev/null | head -1 | xargs -r stat -c %s; systemctl is-enabled helios-offsite-backup.timer")
    l = out.splitlines()
    add("C6", "sauvegarde hors cluster (.161) : dump valide, snapshot etcd > 10 Mo, timer activé", len(l) >= 3 and l[0] == "PG_OK" and l[1].isdigit() and int(l[1]) > 10_000_000 and l[2] == "enabled", out.replace("\n", " | "))
    tests = [f for f in os.listdir(RES) if f.startswith("restore-test-") and "PASS" in open(os.path.join(RES, f), encoding="utf-8").read()]
    add("C6", "tests de restauration PASS archivés (CronJob B + sauvegarde hors cluster)", len(tests) >= 2, tests)
    # ---- C3
    for ip in (153, 155, 158):
        rc, out = ssh(ip, "sudo -n grep 'server:' /etc/kubernetes/admin.conf; sudo -n env KUBECONFIG=/etc/kubernetes/admin.conf kubectl get nodes --no-headers --request-timeout=20s 2>&1 | wc -l")
        want = "api.helios.orange.tn:6443" if ip == 153 else "192.168.148.%d:6443" % ip
        add("C3", ".%d : admin.conf -> %s et kubectl fonctionne" % (ip, want), want in out and out.splitlines()[-1] == "3", out.replace("\n", " | ")[:160])
    # ---- observation
    last_change = a.last_change
    if not last_change:
        j = open(os.path.join(RES, "phase1-journal.md"), encoding="utf-8").read().splitlines()
        stamps = [l.split("|")[1].strip() for l in j if l.startswith("| 2") and not l.split("|")[2].strip().lower().startswith(("observation", "controle", "contrôle", "outil de mesure"))]
        last_change = stamps[-1] if stamps else None
    if last_change:
        t_chg = epoch(last_change if last_change.endswith("Z") else last_change + "Z")
        obs_min = (now - t_chg) / 60
        add("OBS", "observation >= %d min après la dernière modification (%s)" % (a.since_change_min, last_change), obs_min >= a.since_change_min, "%.0f min" % obs_min)
        # Gels d'E/S de l'hôte (cause EXOGÈNE mesurée le 4 nov. : SSD NVMe de l'hôte, cf. ha01_hoststall.py et écart E6 du README).
        # Les chiffres BRUTS sont toujours publiés ; seules les mesures qui chevauchent un gel sont classées « hors gel ».
        stalls = ha01_hoststall.windows(t_chg, now)
        add("OBS", "gels d'E/S de l'hôte repérés depuis la dernière modification (informatif : exemptés ci-dessous, chiffres bruts publiés)",
            True, "%s : %s" % ("n/a" if stalls is None else len(stalls), ha01_hoststall.describe(stalls)))
        rc161, _sz = fetch_probe161()   # la copie locale de .161 doit être rafraîchie, sinon données périmées jugées en silence
        for label, pf in (("Windows", os.path.join(RES, "probe-baseline-p0.jsonl")), (".161", P161_LIVE)):
            recs, last_ts = [], 0.0
            try:
                with open(pf, encoding="utf-8", errors="replace") as fh:
                    for l in fh:
                        try:
                            r = json.loads(l)
                        except ValueError:
                            continue
                        if r.get("probe", "").startswith("_"):
                            continue
                        t = epoch(r["ts"])
                        last_ts = max(last_ts, t)
                        if t >= t_chg:
                            recs.append((t, r))
            except OSError:
                pass
            age = max(0.0, time.time() - last_ts)
            fresh_ok = age < 120 and (label != ".161" or rc161 == 0)
            add("OBS", "sondes vues de %s : données fraîches (dernier échantillon < 120 s)" % label, fresh_ok,
                "âge=%.0f s%s" % (age, "" if label != ".161" or rc161 == 0 else " (copie vivante de .161 impossible, code %s)" % rc161))
            kept = [r for t, r in recs if not ha01_hoststall.inside(stalls, t, 5, 10)]
            pa_raw, pa = ha01_report.analyse([r for _, r in recs]), ha01_report.analyse(kept)

            def _st(p):
                return (min((v["availability_pct"] for v in p.values()), default=0),
                        max((v["longest_failure_s"] for v in p.values()), default=999))
            w_raw, l_raw = _st(pa_raw)
            w_ok, l_ok = _st(pa)
            f_excl = sum(1 for t, r in recs if not r.get("ok") and ha01_hoststall.inside(stalls, t, 5, 10))
            add("OBS", "sondes vues de %s depuis la dernière modification, HORS gels d'E/S hôte : disponibilité min >= 99,95 %% et série d'échecs <= 3 s" % label,
                bool(pa) and w_ok >= 99.95 and l_ok <= 3,
                "hors gels: min=%s%% série max=%ss | BRUT: min=%s%% série max=%ss | exclus: %d échantillons dont %d échecs | n/sonde=%s"
                % (w_ok, l_ok, w_raw, l_raw, len(recs) - len(kept), f_excl, min(v["n"] for v in pa.values()) if pa else 0))
        # Canaris DB : les deux canaris des WORKERS font foi (le canari de .153 est informatif : instrument hébergé sur le nœud malade).
        canaries = (("ha01-canary-w1", "worker .158, fait foi"), ("ha01-canary-w2", "worker .155, fait foi"),
                    ("ha01-canary", "INFORMATIF : hébergé sur .153, nœud le plus chargé"))
        logs = {}
        for dep, role in canaries:
            # TOUJOURS les journaux d'abord, la liste des id ENSUITE : l'inverse fait passer pour « perdues » les écritures
            # acquittées entre les deux lectures (faux positif constaté à l'essai à blanc du 4 nov.).
            try:
                logs[dep] = (ha01_canary_report.fetch_log(dep), time.time())
            except SystemExit:
                logs[dep] = None
        try:
            ids = ha01_canary_report.fetch_ids()
        except SystemExit:
            ids = None
            add("OBS", "canaris DB : liste des id lisible via un nœud vivant", False, "aucun nœud joignable")
        if ids is not None:
            for dep, role in canaries:
                if logs[dep] is None:
                    add("OBS", "canari %s (%s) : journal lisible" % (dep, role), False, "illisible")
                    continue
                log, t_fetch = logs[dep]
                ca = ha01_canary_report.analyse(log, ids, t_chg, None, exclude=stalls or [], exclude_after_s=60)
                age_ack = t_fetch - (ca["last_ack_ts"] or 0)   # fraîcheur au moment de la lecture du journal
                dur_min = ((ca["last_ack_ts"] or 0) - (ca["first_ack_ts"] or 0)) / 60
                detail = ("acquittées=%d | échecs hors gel=%d (bruts=%d, exclus=%d) | acquittées PERDUES=%d | trou max hors gel=%.1fs (brut %.1fs) | "
                          "mesuré %.0f min | dernier ack il y a %.0fs"
                          % (ca["acked"], ca["failures"], ca["failures_total"], ca["failures_excluded"], ca["acked_lost"],
                             ca["max_gap_hors_gel_s"], ca["max_gap_s"], dur_min, age_ack))
                if dep == "ha01-canary":
                    add("OBS", "canari %s (%s) : aucune écriture acquittée perdue" % (dep, role), ca["acked_lost"] == 0, detail)
                else:
                    add("OBS", "canari DB %s (%s) : vivant, >= %d min de mesure, 0 échec hors gel d'E/S hôte, 0 écriture acquittée perdue" % (dep, role, a.since_change_min),
                        ca["acked"] > 0 and ca["failures"] == 0 and ca["acked_lost"] == 0 and age_ack < 60 and dur_min >= a.since_change_min, detail)
    else:
        add("OBS", "dernière modification connue", False, "phase1-journal.md vide")
    # ---- RAM hôte et santé générale
    rows_s = [json.loads(l) for l in open(os.path.join(RES, "samples-baseline-p0.jsonl"), encoding="utf-8") if '"host_avail_mb"' in l]
    # Les 10 min QUI PRÉCÈDENT le démarrage de l'évaluation : sans borne basse, les échantillons pris pendant l'exécution même de ce
    # script (qui charge ~70 Mo de sondes en mémoire) comptaient et le faisaient échouer tout seul (constaté le 4 nov. 07:11Z).
    vals = [r["host_avail_mb"] for r in rows_s if 0 <= now - epoch(r["ts"]) <= 600]
    add("SYS", "RAM hôte >= 2000 Mo sur 10 min (re-mesure)", len(vals) >= 3 and min(vals) >= 2000, "min=%s n=%d" % (min(vals) if vals else None, len(vals)))
    rc, out = ssh(155, "sudo -n env ETCDCTL_API=3 etcdctl --endpoints=https://192.168.148.153:2379,https://192.168.148.155:2379,https://192.168.148.158:2379,https://192.168.148.160:2379,https://192.168.148.162:2379 --cacert=/etc/kubernetes/pki/etcd/ca.crt --cert=/etc/kubernetes/pki/etcd/server.crt --key=/etc/kubernetes/pki/etcd/server.key --command-timeout=8s endpoint health -w json 2>/dev/null")
    try:
        n_ok = sum(1 for e in json.loads(out) if e.get("health"))
    except Exception:  # noqa: BLE001
        n_ok = 0
    add("SYS", "etcd 5/5 sain", n_ok == 5, "%d/5" % n_ok)
    rc, out = k("-n pfe get cluster.postgresql.cnpg.io postgres -o jsonpath='{.status.phase}|{.status.readyInstances}'")
    out = out.replace("'", "")
    add("SYS", "CNPG sain 2/2", out.startswith("Cluster in healthy state") and out.endswith("|2"), out)
    ok_all = all(r[2] for r in rows)
    print("# GATE-1 — mesures (%s UTC)\n" % datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))
    print("| Groupe | Critère | Résultat | Mesure |\n|---|---|---|---|")
    for g, n, ok, d in rows:
        print("| %s | %s | %s | %s |" % (g, n, "✅" if ok else "❌", d.replace("|", "/")))
    print("\n**Verdict automatique : %s**" % ("VALIDÉ" if ok_all else "NON VALIDÉ"))
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
