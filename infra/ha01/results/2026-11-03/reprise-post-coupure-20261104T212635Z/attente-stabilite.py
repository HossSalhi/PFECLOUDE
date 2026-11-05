#!/usr/bin/env python3
"""
Reprise .162 (4 nov. 2026) — ÉTAPE 4 du plan : attente de stabilité. LECTURE SEULE et légère. Script jetable CONSERVÉ COMME PREUVE (hors ops/).
Journal en AJOUT UNIQUEMENT : attente-stabilite.jsonl (même dossier). Aucune écriture ailleurs, aucun seuil, aucune action sur le cluster.

  python attente-stabilite.py --until 2026-11-04T21:58:00Z --term 1820 --counter 8 [--period 300] [--max-seconds 560]

Relève toutes les `period` s : terme Raft etcd (SSH 1 appel), compteur de changements de leader + instant du dernier changement (Prometheus .160),
santé etcd des 5 membres (GET :2381/health), RAM disponible de l'hôte, gels d'E/S des 15 dernières minutes, fsync WAL p99 par membre, âge des journaux du
harnais, état de l'unité ha01-probe-161.
Événement = terme différent de --term OU compteur différent de --counter : le script s'arrête (code 3) et imprime la NOUVELLE échéance = instant de
l'événement + 62 min (60 min + 2 min de marge). Le seuil « <= 2 changements sur 1 h » n'est JAMAIS relâché.
Codes : 0 = échéance atteinte sans événement · 3 = événement (nouvelle échéance imprimée) · 2 = temps imparti à CET appel écoulé (rappeler avec la même échéance).
"""
import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, r"C:\Users\houss\IdeaProjects\PFE-Orange\infra\ha01\ops")
import ha01_ops as O  # noqa: E402

D = O.ha01_hoststall
HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "attente-stabilite.jsonl")
MARGE_S = 62 * 60


def prom(path, params):
    return json.load(urllib.request.urlopen(D.PROM + path + "?" + urllib.parse.urlencode(params), timeout=30))["data"]["result"]


def dernier_changement(now):
    """(epoch du dernier pas où le compteur max a changé, valeur courante) d'après Prometheus (pas de 15 s sur 100 min)."""
    res = prom("/api/v1/query_range", {"query": "max(etcd_server_leader_changes_seen_total)", "start": now - 6000, "end": now, "step": 15})
    vals = res[0]["values"] if res else []
    last, prev = None, None
    for ts, v in vals:
        if prev is not None and v != prev:
            last = float(ts)
        prev = v
    return last, (vals[-1][1] if vals else None)


def cycle(exp_term, exp_counter):
    now = time.time()
    rec = {"ts": O.iso(), "attendu": {"terme": exp_term, "compteur": exp_counter}}
    try:
        term, leader = O.etcd_term_leader()
        rec["terme"], rec["leader_id"] = term, ("%x" % int(leader)) if leader else None
    except Exception as e:  # noqa: BLE001
        term = None
        rec["terme_err"] = type(e).__name__
    lc, cnt = None, None
    try:
        lc, cnt = dernier_changement(now)
        rec["compteur_leader_changes"] = cnt
        rec["dernier_changement_prometheus_utc"] = None if lc is None else O.iso_ms(lc)[:19] + "Z"
    except Exception as e:  # noqa: BLE001
        rec["prometheus_err"] = type(e).__name__
    try:
        h = {}
        for s in O.ETCD_ALL:
            try:
                h[s] = json.load(urllib.request.urlopen("http://%s%s:2381/health" % (O.NET, s), timeout=3)).get("health") == "true"
            except Exception:  # noqa: BLE001
                h[s] = False
        rec["etcd_health"] = h
    except Exception as e:  # noqa: BLE001
        rec["health_err"] = type(e).__name__
    try:
        rec["ram_hote_mo"] = O.host_avail_mb()
    except Exception as e:  # noqa: BLE001
        rec["ram_err"] = type(e).__name__
    try:
        w = D.windows(now - 900, now)
        rec["gels_es_15min"] = None if w is None else [[O.iso_ms(a)[:19] + "Z", O.iso_ms(b)[:19] + "Z"] for a, b in w]
    except Exception as e:  # noqa: BLE001
        rec["gels_err"] = type(e).__name__
    try:
        q = 'histogram_quantile(0.99, sum by (instance,le)(rate(etcd_disk_wal_fsync_duration_seconds_bucket[1m])))'
        rec["fsync_p99_ms"] = {r["metric"]["instance"].split(":")[0].split(".")[-1]: round(float(r["value"][1]) * 1000, 1) for r in prom("/api/v1/query", {"query": q})}
    except Exception as e:  # noqa: BLE001
        rec["fsync_err"] = type(e).__name__
    rec["harnais_age_s"] = {"sonde": round(O.file_age(O.PROBE_WIN), 1), "echantillonneur": round(O.file_age(O.SAMPLES), 1)}
    try:
        rc, o = O.ssh("161", "systemctl is-active ha01-probe-161", timeout=20)
        rec["sonde_161"] = o.strip()
    except Exception as e:  # noqa: BLE001
        rec["sonde_161_err"] = type(e).__name__
    # événement ?
    ev = None
    if term is not None and term != exp_term:
        ev = "terme %s -> %s" % (exp_term, term)
    if cnt is not None and float(cnt) != float(exp_counter):
        ev = (ev + " ; " if ev else "") + "compteur %s -> %s" % (exp_counter, cnt)
    if ev:
        t_new = lc if (cnt is not None and float(cnt) != float(exp_counter) and lc is not None) else now
        rec["evenement"] = ev
        rec["evenement_instant_utc"] = O.iso_ms(t_new)[:19] + "Z"
        rec["nouvelle_echeance_utc"] = O.iso_ms(t_new + MARGE_S)[:19] + "Z"
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--until", required=True)
    ap.add_argument("--term", type=int, required=True)
    ap.add_argument("--counter", type=float, required=True)
    ap.add_argument("--period", type=int, default=300)
    ap.add_argument("--max-seconds", type=int, default=560)
    a = ap.parse_args()
    until = O.epoch_of(a.until)
    t_budget = time.time() + a.max_seconds
    while True:
        rec = cycle(a.term, a.counter)
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        fs = rec.get("fsync_p99_ms") or {}
        print("%s terme=%s compteur=%s dernier_chgt=%s ram=%s Mo gels15min=%s health=%s/5 fsync_max=%s ms harnais=%s s sonde161=%s%s" % (
            rec["ts"], rec.get("terme"), rec.get("compteur_leader_changes"), rec.get("dernier_changement_prometheus_utc"), rec.get("ram_hote_mo"),
            "?" if rec.get("gels_es_15min") is None else len(rec["gels_es_15min"]), sum(1 for v in (rec.get("etcd_health") or {}).values() if v),
            max(fs.values()) if fs else "?", rec["harnais_age_s"], rec.get("sonde_161"), (" EVENEMENT: " + rec["evenement"] + " -> nouvelle échéance " + rec["nouvelle_echeance_utc"]) if rec.get("evenement") else ""), flush=True)
        if rec.get("evenement"):
            return 3
        now = time.time()
        if now >= until:
            return 0
        nxt = min(now + a.period, until)
        if nxt >= t_budget:
            time.sleep(max(0.0, t_budget - now))
            return 2
        time.sleep(max(0.0, nxt - now))


if __name__ == "__main__":
    sys.exit(main())
