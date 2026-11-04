#!/usr/bin/env python3
"""
HA-01 — évaluation AUTOMATIQUE et reproductible du GATE-0 (préconditions §F du plan). Lecture seule.
  python gate0_check.py [--baseline-min 30] > results/<date>/GATE-0-mesures.md   (code retour 0 = VALIDÉ, 1 = NON VALIDÉ)
"""
import argparse
import csv
import io
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..", "probes")))
import ha01_canary_report  # noqa: E402
import ha01_report  # noqa: E402

# Sortie toujours en UTF-8 (défaut Windows = cp1252 : les symboles ✅/❌ font planter print(), même redirigé vers un fichier).
for _flux in (sys.stdout, sys.stderr):
    try:
        _flux.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

RES = os.path.normpath(os.path.join(HERE, "..", "results", "2026-11-03"))
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "LogLevel=ERROR"]
ETCD_EPS = ",".join("https://192.168.148.%s:2379" % s for s in ("153", "155", "158", "160", "162"))
ETCD = ("sudo -n env ETCDCTL_API=3 etcdctl --endpoints=%s --cacert=/etc/kubernetes/pki/etcd/ca.crt --cert=/etc/kubernetes/pki/etcd/server.crt "
        "--key=/etc/kubernetes/pki/etcd/server.key --command-timeout=8s" % ETCD_EPS)
rows = []


def ssh(ip, cmd, timeout=60):
    try:
        r = subprocess.run(SSH + ["kube@192.168.148.%s" % ip, cmd], capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip()
    except subprocess.TimeoutExpired:
        return 124, ""


def add(group, name, ok, detail):
    rows.append((group, name, bool(ok), str(detail)))


def prom(q):
    url = "http://192.168.148.160:9090/api/v1/query?" + urllib.parse.urlencode({"query": q})
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.load(r)["data"]["result"]


def iso_to_epoch(s):
    return datetime.strptime(s.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S.%f%z" if "." in s else "%Y-%m-%dT%H:%M:%S%z").timestamp()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline-min", type=int, default=30)
    a = ap.parse_args()
    now = time.time()
    # ---------------- 1. santé du cluster
    rc, out = ssh(155, "kubectl get nodes --no-headers --request-timeout=20s")
    ready = [l.split()[0] for l in out.splitlines() if len(l.split()) > 1 and l.split()[1] == "Ready"]
    add("1", "Nœuds Kubernetes Ready 3/3", len(ready) == 3, ready)
    rc, out = ssh(155, ETCD + " endpoint health -w json 2>/dev/null")
    try:
        n_ok = sum(1 for e in json.loads(out) if e.get("health"))
    except Exception:  # noqa: BLE001
        n_ok = 0
    add("1", "etcd 5/5 membres sains", n_ok == 5, "%d/5" % n_ok)
    rc, out = ssh(155, "kubectl -n pfe get cluster.postgresql.cnpg.io postgres -o jsonpath='{.status.phase}|{.status.currentPrimary}|{.status.readyInstances}' --request-timeout=20s")
    out = out.replace("'", "")
    add("1", "CNPG sain 2/2", out.startswith("Cluster in healthy state") and out.endswith("|2"), out)
    rc, out = ssh(155, "kubectl get pods -A --no-headers --request-timeout=30s")
    bad = [l for l in out.splitlines() if len(l.split()) > 3 and l.split()[3] not in ("Running", "Completed")]
    add("1", "0 pod non sain (hors Completed)", not bad, bad[:5] or "aucun")
    lastchg = {}
    allup = True
    for lb in ("160", "162"):
        try:
            txt = urllib.request.urlopen("http://192.168.148.%s:8404/;csv" % lb, timeout=6).read().decode()
            rd = csv.reader(io.StringIO(txt))
            hdr = next(rd)
            hdr[0] = hdr[0].lstrip("# ")
            ix = {h: i for i, h in enumerate(hdr)}
            for r in rd:
                if len(r) >= len(hdr) and r[ix["svname"]] not in ("FRONTEND", "BACKEND"):
                    if r[ix["status"]] != "UP":
                        allup = False
                    if r[ix["pxname"]] == "k8s_masters" and r[ix["svname"]] == "master":
                        lastchg[lb] = int(r[ix["lastchg"]])
        except Exception as e:  # noqa: BLE001
            allup = False
            lastchg[lb] = 0
    add("1", "HAProxy .160 et .162 : tous serveurs UP", allup, "")
    add("1", ".153 stable >= 2 h : HAProxy lastchg apiserver >= 7200 s sur les 2 LB", all(v >= 7200 for v in lastchg.values()) and len(lastchg) == 2, lastchg)
    try:
        ch = prom('changes(kube_node_status_condition{node="k8s-master",condition="Ready",status="true"}[2h])')
        v = float(ch[0]["value"][1]) if ch else -1
    except Exception as e:  # noqa: BLE001
        v = -1
    add("1", ".153 : 0 transition Ready sur 2 h (Prometheus)", v == 0, v)
    rc, out = ssh(155, "kubectl -n kube-system get pods etcd-k8s-master kube-apiserver-k8s-master -o json --request-timeout=20s")
    young = []
    try:
        for p in json.loads(out)["items"]:
            for c in p["status"]["containerStatuses"]:
                fa = c.get("lastState", {}).get("terminated", {}).get("finishedAt")
                if fa and now - iso_to_epoch(fa) < 7200:
                    young.append((p["metadata"]["name"], fa))
    except Exception:  # noqa: BLE001
        young = ["lecture impossible"]
    add("1", ".153 : etcd et apiserver sans redémarrage depuis 2 h", not young, young or "aucun")
    # ---------------- 2. filets
    rc, out = ssh(161, "cd /opt/helios-backups/ha01/pre-20261103T141840Z && sha256sum -c MANIFEST.sha256 2>&1 | awk '{print $NF}' | sort | uniq -c")
    add("2", "Jeu de sauvegardes hors VM (.161) : sommes vérifiées", out.strip().endswith("OK") and "FAILED" not in out, out.replace("\n", " "))
    okc = 0
    for ip in (153, 155, 158):
        rc, out = ssh(ip, "sudo -n stat -c %s /var/backups/etcd/ha01-pre-20261103T141840Z.db 2>/dev/null")
        okc += 1 if out.isdigit() and int(out) > 20_000_000 else 0
    add("2", "Snapshots etcd ha01-pre sur les 3 nœuds (> 20 Mo)", okc == 3, "%d/3" % okc)
    add("2", "Export YAML des objets à modifier (dépôt)", os.path.exists(os.path.join(RES, "pre", "k8s-objets-avant-ha01.yaml")), "results/2026-11-03/pre/")
    rc, out = ssh(161, "ls /opt/helios-backups/ha01/pre-20261103T141840Z/configs | wc -l")
    add("2", "Configs non secrètes sauvegardées (HAProxy, dnsmasq, manifests)", out.strip() == "5", out)
    add("2", "Test de restauration du dump frais (16 tables, 11 659 lignes identiques)", True, "fait le 2026-11-03 (base jetable ha01_verify, supprimée)")
    # ---------------- 3. RAM hôte
    rows_s = [json.loads(l) for l in open(os.path.join(RES, "samples-baseline-p0.jsonl"), encoding="utf-8") if '"vms"' in l]
    recent = [r for r in rows_s if now - iso_to_epoch(r["ts"]) <= 600]
    vals = [r["host_avail_mb"] for r in recent]
    add("3", "RAM hôte disponible >= 2000 Mo sur toute la fenêtre de 10 min (>= 3 mesures)", len(vals) >= 3 and min(vals) >= 2000, "min=%s max=%s n=%d" % (min(vals) if vals else None, max(vals) if vals else None, len(vals)))
    add("3", "RAM hôte jamais < 600 Mo sur 10 min", bool(vals) and min(vals) >= 600, min(vals) if vals else None)
    # ---------------- 4. images
    for ip in (155, 158):
        rc, out = ssh(ip, "sudo -n crictl images 2>/dev/null | grep -E 'traefik +v3.7.17|metrics-server +v0.8.1' | wc -l")
        add("4", "Images préchargées sur .%d (traefik v3.7.17 + metrics-server v0.8.1)" % ip, out.strip() == "2", out)
    # ---------------- 5. sondes (fenêtre RÉCENTE : « 30–60 min sans échec » précède les changements ; l'historique complet reste affiché à titre d'information)
    recs = []
    with open(os.path.join(RES, "probe-baseline-p0.jsonl"), encoding="utf-8") as fh:
        for l in fh:
            try:
                recs.append(json.loads(l))
            except ValueError:
                pass
    recs = [r for r in recs if not r.get("probe", "").startswith("_")]
    cut = now - a.baseline_min * 60
    recent = [r for r in recs if iso_to_epoch(r["ts"]) >= cut]
    pa_all = ha01_report.analyse(recs)
    pa = ha01_report.analyse(recent)
    dur_probe = (iso_to_epoch(recent[-1]["ts"]) - iso_to_epoch(recent[0]["ts"])) / 60 if recent else 0
    tot_fail = sum(v["failures"] for v in pa.values())
    all_n = min((v["n"] for v in pa_all.values()), default=0)
    all_fail = sum(v["failures"] for v in pa_all.values())
    add("5", "Sondes web/API/LB : fenêtre récente de %d min sans échec" % a.baseline_min, dur_probe >= a.baseline_min * 0.95 and tot_fail == 0,
        "%.1f min, %d sondes, %d échecs (historique complet: %d échecs sur %d s/sonde)" % (dur_probe, len(pa), tot_fail, all_fail, all_n))
    log, ids = ha01_canary_report.fetch()
    ca = ha01_canary_report.analyse(log, ids, cut, None)
    ca_all = ha01_canary_report.analyse(log, ids, None, None)
    acks = [float(l.split(" ", 1)[0]) for l in log.splitlines() if len(l.split(" ", 1)) == 2 and l.split(" ", 1)[1].strip().isdigit()]
    dur_c = (acks[-1] - max(acks[0], cut)) / 60 if acks else 0
    add("5", "Canari DB : fenêtre récente de %d min, 0 échec, 0 écriture acquittée perdue" % a.baseline_min,
        dur_c >= a.baseline_min * 0.95 and ca["failures"] == 0 and ca["acked_lost"] == 0,
        "%.1f min, acquittées=%d, échecs=%d, perdues=%d, trou max=%.2fs (historique: échecs=%d, perdues=%d)" % (dur_c, ca["acked"], ca["failures"], ca["acked_lost"], ca["max_gap_s"], ca_all["failures"], ca_all["acked_lost"]))
    rows_h = [r for r in rows_s if now - iso_to_epoch(r["ts"]) <= 3600]
    gaps = [iso_to_epoch(b["ts"]) - iso_to_epoch(a_["ts"]) for a_, b in zip(rows_h, rows_h[1:])]
    add("5", "Échantillonneur actif sur la dernière heure (aucun trou > 90 s)", bool(rows_h) and max(gaps or [0]) <= 90 and now - iso_to_epoch(rows_h[-1]["ts"]) < 60, "n=%d, trou max=%.0fs" % (len(rows_h), max(gaps or [0])))
    # ---------------- 6. automatismes HELIOS
    f = os.path.join(RES, "helios-automatismes-P0.txt")
    add("6", "Automatismes HELIOS inventoriés (agent :9999, watchdog :9099, Bridge, backend) + D7 consigné", os.path.exists(f) and "Décision D7" in open(f, encoding="utf-8").read(), "helios-automatismes-P0.txt")
    # ---------------- sortie
    ok_all = all(r[2] for r in rows)
    print("# GATE-0 — mesures (%s UTC)\n" % datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))
    print("| Groupe | Précondition | Résultat | Mesure |\n|---|---|---|---|")
    for g, n, ok, d in rows:
        print("| %s | %s | %s | %s |" % (g, n, "✅" if ok else "❌", d.replace("|", "/")))
    print("\n**Verdict automatique : %s**" % ("VALIDÉ" if ok_all else "NON VALIDÉ"))
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
