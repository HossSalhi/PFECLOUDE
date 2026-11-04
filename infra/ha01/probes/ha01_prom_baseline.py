#!/usr/bin/env python3
"""
HA-01 — baseline 24 h depuis le Prometheus de .160 (lecture seule) : etcd, redémarrages, flaps de nœuds, RAM/charge/pression CPU.
Reproductible : python ha01_prom_baseline.py [--hours 24] > results/<date>/baseline-prometheus-<label>.txt
NB : si .160 est arrêtée (scénario 1), Prometheus est indisponible : le script le signale et sort en code 2.
"""
import argparse
import datetime
import json
import sys
import time
import urllib.parse
import urllib.request

P = "http://192.168.148.160:9090"


def q(query, start=None, end=None, step=None):
    if start is None:
        url = P + "/api/v1/query?" + urllib.parse.urlencode({"query": query})
    else:
        url = P + "/api/v1/query_range?" + urllib.parse.urlencode({"query": query, "start": start, "end": end, "step": step})
    with urllib.request.urlopen(url, timeout=40) as r:
        return json.load(r)["data"]["result"]


def show(title, query, fmt="%.3f"):
    print("== " + title)
    for r in sorted(q(query), key=lambda x: x["metric"].get("node", x["metric"].get("instance", x["metric"].get("pod", "")))):
        m = r["metric"]
        lab = m.get("node") or m.get("instance") or m.get("pod") or str(m)
        print("  %-36s " % lab[:36] + fmt % float(r["value"][1]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=24)
    h = ap.parse_args().hours
    w = "%dh" % h
    try:
        urllib.request.urlopen(P + "/-/ready", timeout=5).read()
    except Exception as e:  # noqa: BLE001
        print("Prometheus indisponible:", e)
        return 2
    now = time.time()
    print("# baseline Prometheus — fenêtre %s se terminant le %s UTC" % (w, datetime.datetime.fromtimestamp(now, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")))
    show("etcd : changements de leader vus (%s)" % w, "increase(etcd_server_leader_changes_seen_total[%s])" % w, "%.1f")
    show("etcd : échecs d'envoi de heartbeat (%s)" % w, "increase(etcd_server_heartbeat_send_failures_total[%s])" % w, "%.0f")
    show("etcd : propositions échouées (%s)" % w, "increase(etcd_server_proposals_failed_total[%s])" % w, "%.0f")
    show("etcd : fsync WAL p99 max sur fenêtres 5 min (s)", "max_over_time(histogram_quantile(0.99, sum by (le,instance) (rate(etcd_disk_wal_fsync_duration_seconds_bucket[5m])))[%s:5m])" % w)
    show("etcd : commit backend p99 max sur fenêtres 5 min (s)", "max_over_time(histogram_quantile(0.99, sum by (le,instance) (rate(etcd_disk_backend_commit_duration_seconds_bucket[5m])))[%s:5m])" % w)
    show("nœuds K8s : transitions Ready (%s)" % w, 'changes(kube_node_status_condition{condition="Ready",status="true"}[%s])' % w, "%.0f")
    print("== redémarrages de conteneurs (> 0 sur %s)" % w)
    for r in sorted(q('increase(kube_pod_container_status_restarts_total{namespace=~"kube-system|cnpg-system|falco|metallb-system|ingress-nginx|pfe"}[%s]) > 0' % w), key=lambda x: x["metric"].get("pod", "")):
        m = r["metric"]
        print("  %-50s %-16s +%d" % (m.get("pod", "?")[:50], m.get("container", "?")[:16], round(float(r["value"][1]))))
    mem = {r["metric"]["instance"]: float(r["value"][1]) / 1048576 for r in q("min_over_time(node_memory_MemAvailable_bytes[%s])" % w)}
    load = {r["metric"]["instance"]: float(r["value"][1]) for r in q("max_over_time(node_load1[%s])" % w)}
    psi = {r["metric"]["instance"]: float(r["value"][1]) for r in q("max_over_time(rate(node_pressure_cpu_waiting_seconds_total[5m])[%s:5m])" % w)}
    print("== VMs : MemAvailable min (Mo) / load1 max / pression CPU max (s/s)")
    for i in sorted(mem):
        print("  %-26s %7.0f   %6.1f   %5.2f" % (i, mem[i], load.get(i, -1), psi.get(i, -1)))
    print("== .153 : dernier instant où fsync WAL p99 > 0,1 s")
    res = q('histogram_quantile(0.99, sum by (le,instance) (rate(etcd_disk_wal_fsync_duration_seconds_bucket{instance="192.168.148.153:2381"}[5m])))', now - h * 3600, now, 300)
    bad = [float(t) for t, v in res[0]["values"] if v != "NaN" and float(v) > 0.1] if res else []
    if bad:
        print("  %s UTC (il y a %.0f min)" % (datetime.datetime.fromtimestamp(bad[-1], datetime.timezone.utc).strftime("%m-%d %H:%M"), (now - bad[-1]) / 60))
    else:
        print("  aucun")
    return 0


if __name__ == "__main__":
    sys.exit(main())
