#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HELIOS - Collecteur d'exemples REELS pour l'entrainement futur du ML sur les incidents host.

Pourquoi ce fichier existe : le classifieur ML actuel n'a jamais vu de metrique host (CPU/RAM/
disque des VMs) a l'entrainement -> il est structurellement aveugle sur ces incidents (mesure du
01/08 : NODE_HIGH_CPU classe a tort comme POD_PENDING_INSUFFICIENT_RESOURCES). Pour corriger ca il
faut un dataset REEL, pas invente : le 120-synthetique avait triche (F1 0.974 mais transfert reel
0.00) exactement pour cette raison.

Principe : a chaque cycle, on demande au detecteur (verite terrain kubelet/node-exporter/etcd,
memes regles qu'utilise Camunda) quel est l'incident EN COURS, puis on enregistre pour CHAQUE
noeud ses metriques host du moment + l'etiquette reelle (le type si ce noeud est la cible, sinon
NO_INCIDENT). Zero metrique inventee, zero etiquette devinee.

Tourne en continu (systemd, Restart=always). Le fichier CSV grossit tout seul ; l'entrainement se
fait plus tard, une fois un volume suffisant accumule (viser plusieurs jours, et si possible au
moins quelques dizaines d'exemples de CHAQUE type - certains comme ETCD_SLOW_FSYNC sont rares
naturellement, cf le README pour declencher des incidents controles).
"""
import csv
import json
import os
import time
import urllib.request
import urllib.parse

PROM = os.environ.get("HELIOS_PROM_URL", "http://192.168.148.160:9090")
BRIDGE = os.environ.get("HELIOS_BRIDGE_URL", "http://127.0.0.1:8099/detecter")
TOKEN = os.environ.get("HELIOS_BRIDGE_TOKEN", "")
SORTIE = os.environ.get("HELIOS_ML_DATASET", "/opt/pfe-ai-service/datasets/host_incidents_reels.csv")
PERIODE = int(os.environ.get("HELIOS_COLLECTEUR_PERIODE", "30"))
JOURNAL = "/var/log/helios-collecteur-ml.log"

# Les 3 vrais noeuds Kubernetes (le detecteur ne cible que ceux-la pour les regles "noeud"/"hote"
# issues de kube_node_status_condition ; .160/.161/.162 sont aussi surveillees par node-exporter
# mais ne sont pas des objets Node K8s, donc jamais "cible" d'un incident NODE_*).
NODE_IP = {
    "k8s-master": "192.168.148.153",
    "k8s-worker1": "192.168.148.158",
    "k8s-worker2": "192.168.148.155",
}

CHAMPS = [
    "timestamp", "node", "host_cpu_pct", "host_mem_pct", "host_disk_pct", "host_inodes_pct",
    "host_fd_pct", "host_disk_io_pct", "host_cpu_steal_pct", "host_oom_kills_5m",
    "host_network_errors_ps", "host_time_drift_seconds", "etcd_fsync_p99_seconds",
    "etcd_has_leader", "pvc_not_bound_count", "label",
]


def tracer(*parts):
    ligne = time.strftime("%Y-%m-%dT%H:%M:%S") + " " + " ".join(str(p) for p in parts)
    try:
        with open(JOURNAL, "a", encoding="utf-8") as f:
            f.write(ligne + "\n")
    except Exception:
        pass
    print(ligne, flush=True)


def prom_query(expr):
    """Meme mecanisme que detecter.py : une metrique absente/erreur rend 0.0, jamais une
    exception qui casserait le cycle de collecte."""
    url = PROM + "/api/v1/query?query=" + urllib.parse.quote(expr)
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            d = json.loads(r.read().decode())
        res = d.get("data", {}).get("result", [])
        return float(res[0]["value"][1]) if res else 0.0
    except Exception:
        return 0.0


def host_features(ip):
    """EXACTEMENT les memes formules que metric_features.py (_host_metric_queries) et
    detecter.py : le dataset doit mesurer ce que le modele verra en production."""
    ne = 'instance="%s:9100"' % ip
    etcd = 'instance="%s:2381"' % ip
    return {
        "host_cpu_pct": prom_query('100 - (avg by(instance)(rate(node_cpu_seconds_total{%s,mode="idle"}[3m])) * 100)' % ne),
        "host_mem_pct": prom_query('(1 - (node_memory_MemAvailable_bytes{%s} / node_memory_MemTotal_bytes{%s})) * 100' % (ne, ne)),
        "host_disk_pct": prom_query('(1 - (node_filesystem_avail_bytes{%s,mountpoint="/",fstype!~"tmpfs"} / node_filesystem_size_bytes{%s,mountpoint="/",fstype!~"tmpfs"})) * 100' % (ne, ne)),
        "host_inodes_pct": prom_query('(1 - (node_filesystem_files_free{%s,mountpoint="/"} / node_filesystem_files{%s,mountpoint="/"})) * 100' % (ne, ne)),
        "host_fd_pct": prom_query('(node_filefd_allocated{%s} / node_filefd_maximum{%s}) * 100' % (ne, ne)),
        "host_disk_io_pct": prom_query('max(rate(node_disk_io_time_seconds_total{%s}[5m])) * 100' % ne),
        "host_cpu_steal_pct": prom_query('avg(rate(node_cpu_seconds_total{%s,mode="steal"}[5m])) * 100' % ne),
        "host_oom_kills_5m": prom_query('increase(node_vmstat_oom_kill{%s}[5m])' % ne),
        "host_network_errors_ps": prom_query('max(rate(node_network_receive_errs_total{%s}[5m]))' % ne),
        "host_time_drift_seconds": prom_query('abs(node_time_seconds{%s} - timestamp(node_time_seconds{%s}))' % (ne, ne)),
        "etcd_fsync_p99_seconds": prom_query('histogram_quantile(0.99, sum by(le)(rate(etcd_disk_wal_fsync_duration_seconds_bucket{%s}[5m])))' % etcd),
        "etcd_has_leader": prom_query('etcd_server_has_leader{%s}' % etcd),
    }


def pvc_not_bound():
    return prom_query('count(kube_persistentvolumeclaim_status_phase{phase=~"Pending|Lost"} == 1) or vector(0)')


def verdict_detecteur():
    """Verite terrain du cycle courant, via le meme /detecter que Camunda interroge."""
    req = urllib.request.Request(
        BRIDGE, data=json.dumps({"source": "collecteur-ml"}).encode(),
        headers={"Content-Type": "application/json", "X-Helios-Token": TOKEN})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        tracer("ERREUR /detecter:", e)
        return {}


def ecrire_ligne(row):
    nouveau = not os.path.exists(SORTIE)
    os.makedirs(os.path.dirname(SORTIE), exist_ok=True)
    with open(SORTIE, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CHAMPS)
        if nouveau:
            w.writeheader()
        w.writerow(row)


def cycle():
    inc = verdict_detecteur()
    typ = inc.get("type", "NO_INCIDENT")
    cible = str(inc.get("cible", "") or "")
    pvc = pvc_not_bound()
    ts = int(time.time())
    for node, ip in NODE_IP.items():
        f = host_features(ip)
        f["pvc_not_bound_count"] = pvc
        # la cible peut arriver avec ou sans prefixe "k8s-" selon la regle qui a matche
        touche = cible in (node, node.replace("k8s-", ""))
        label = typ if touche else "NO_INCIDENT"
        row = {"timestamp": ts, "node": node, "label": label, **f}
        ecrire_ligne(row)
    tracer("cycle | detecteur=%s cible=%s | ligne(s) ecrite(s)=%d" % (typ, cible or "aucun", len(NODE_IP)))


def main():
    tracer("DEMARRAGE collecteur ML host | periode=%ds | sortie=%s" % (PERIODE, SORTIE))
    while True:
        try:
            cycle()
        except Exception as e:
            tracer("ERREUR cycle:", e)
        time.sleep(PERIODE)


if __name__ == "__main__":
    main()
