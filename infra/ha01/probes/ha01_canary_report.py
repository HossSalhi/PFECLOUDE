#!/usr/bin/env python3
"""
HA-01 — analyse du canari d'écriture PostgreSQL.

Récupère (--fetch) le journal du pod ha01-canary et la liste des id présents dans ha01.canary via un nœud
vivant, ou lit des fichiers déjà enregistrés. Calcule, sur une fenêtre [--from, --to] (epoch ou ISO UTC) :
  - écritures acquittées, échecs (lignes non numériques), fins de session
  - plus long trou entre deux écritures acquittées consécutives (= indisponibilité d'écriture observée)
  - écritures acquittées PERDUES (id journalisé comme acquitté mais absent de la base) = RPO observé
Usage :
  python ha01_canary_report.py --fetch --from 2026-11-03T15:00:00Z --to 2026-11-03T15:30:00Z [--save DIR]
  python ha01_canary_report.py --log canary.log --ids ids.txt
"""
import argparse
import subprocess
import sys
from datetime import datetime, timezone

SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "-o", "LogLevel=ERROR"]
VIA = ["192.168.148.155", "192.168.148.158", "192.168.148.153"]
KENV = "export KUBECONFIG=$HOME/.kube/config; "


def to_epoch(s):
    if s is None:
        return None
    try:
        return float(s)
    except ValueError:
        return datetime.strptime(s.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S%z").timestamp()


def ssh_any(cmd, timeout=120):
    for ip in VIA:
        try:
            r = subprocess.run(SSH + ["kube@" + ip, KENV + cmd], capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            continue
        if r.returncode not in (255, 124):
            return r.stdout
    raise SystemExit("aucun noeud joignable")


def fetch_log(deploy="ha01-canary"):
    """Journal d'un canari (ha01-canary = .153, informatif ; ha01-canary-w1 / -w2 = workers, font foi)."""
    return ssh_any("kubectl -n pfe logs deploy/%s --request-timeout=60s" % deploy)


def fetch_ids():
    """Tous les id présents dans ha01.canary (la base est partagée par les canaris)."""
    return ssh_any("P=$(kubectl -n pfe get pods -l cnpg.io/instanceRole=primary -o jsonpath='{.items[0].metadata.name}' --request-timeout=30s); "
                   "kubectl -n pfe exec $P -c postgres --request-timeout=60s -- psql -U postgres -d ha01 -At -c 'select id from canary order by id'")


def fetch(deploy="ha01-canary"):
    return fetch_log(deploy), fetch_ids()


def _hit(wins, ts, after_s):
    return any(a - 5 <= ts <= b + after_s for a, b in wins)


def _hit_seg(wins, t_start, t_end, after_s):
    return any(t_start <= b + after_s and t_end >= a - 5 for a, b in wins)


def analyse(log_text, ids_text, t0, t1, exclude=None, exclude_after_s=60):
    """exclude : fenêtres (début, fin) en epoch de gels d'E/S de l'hôte (cf. ha01_hoststall.py). Les ÉCHECS et les TROUS
    qui les chevauchent (élargies de exclude_after_s après, pour la reconnexion) sont classés « hors gel » ; les chiffres
    bruts restent publiés (failures_total, max_gap_s). Les écritures acquittées PERDUES ne sont jamais exemptées."""
    excl = list(exclude or [])
    ack = []          # (epoch, id)
    fails_all = []    # (epoch, texte)
    sess_end = 0
    for line in log_text.splitlines():
        parts = line.strip().split(" ", 1)
        if len(parts) < 2:
            continue
        try:
            ts = float(parts[0])
        except ValueError:
            continue
        if (t0 and ts < t0) or (t1 and ts > t1):
            continue
        txt = parts[1].strip()
        if txt.isdigit():
            ack.append((ts, int(txt)))
        elif txt == "SESSION_END":
            sess_end += 1
        else:
            fails_all.append((ts, txt[:90]))
    fails = [f for f in fails_all if not _hit(excl, f[0], exclude_after_s)]
    res = {"acked": len(ack), "failures": len(fails), "failures_total": len(fails_all),
           "failures_excluded": len(fails_all) - len(fails), "session_end": sess_end}
    res["first_ack_ts"] = ack[0][0] if ack else None
    res["last_ack_ts"] = ack[-1][0] if ack else None
    gaps = []
    for (a, _), (b, _) in zip(ack, ack[1:]):
        gaps.append((b - a, a))
    gmax = max(gaps) if gaps else (0, 0)
    res["max_gap_s"] = round(gmax[0], 2)
    res["max_gap_start_utc"] = datetime.fromtimestamp(gmax[1], timezone.utc).strftime("%H:%M:%S") if gaps else None
    kept = [(g, s) for g, s in gaps if not _hit_seg(excl, s, s + g, exclude_after_s)]
    res["max_gap_hors_gel_s"] = round(max(kept)[0], 2) if kept else 0.0
    big = [(round(g, 1), datetime.fromtimestamp(s, timezone.utc).strftime("%H:%M:%S")) for g, s in gaps if g > 1.0]
    res["gaps_over_1s"] = big[:10]
    if ids_text is not None:
        present = set(int(x) for x in ids_text.split() if x.strip().isdigit())
        lost = [i for _, i in ack if i not in present]
        res["acked_lost"] = len(lost)
        res["acked_lost_ids_sample"] = lost[:10]
    if fails:
        from collections import Counter
        res["failure_kinds"] = dict(Counter(t for _, t in fails).most_common(4))
        res["failure_window_utc"] = [datetime.fromtimestamp(fails[0][0], timezone.utc).strftime("%H:%M:%S"),
                                     datetime.fromtimestamp(fails[-1][0], timezone.utc).strftime("%H:%M:%S")]
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--log")
    ap.add_argument("--ids")
    ap.add_argument("--from", dest="t0")
    ap.add_argument("--to", dest="t1")
    ap.add_argument("--save", help="dossier où enregistrer canary.log et ids.txt (preuve)")
    ap.add_argument("--deploy", default="ha01-canary", help="ha01-canary (.153, informatif) | ha01-canary-w1 | ha01-canary-w2")
    ap.add_argument("--exclude-host-stalls", action="store_true",
                    help="classe « hors gel » les échecs/trous chevauchant un gel d'E/S de l'hôte (ha01_hoststall.py)")
    a = ap.parse_args()
    for _flux in (sys.stdout, sys.stderr):
        try:
            _flux.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    if a.fetch:
        log, ids = fetch(a.deploy)
        if a.save:
            open(a.save.rstrip("/\\") + "/canary-%s.log" % a.deploy, "w", encoding="utf-8").write(log)
            open(a.save.rstrip("/\\") + "/ids.txt", "w", encoding="utf-8").write(ids)
    else:
        log = open(a.log, encoding="utf-8").read()
        ids = open(a.ids, encoding="utf-8").read() if a.ids else None
    t0, t1 = to_epoch(a.t0), to_epoch(a.t1)
    excl = None
    if a.exclude_host_stalls:
        import os
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import ha01_hoststall
        import time as _t
        excl = ha01_hoststall.windows(t0 or (_t.time() - 86400), t1 or _t.time())
        print("%-22s %s" % ("gels_hote_exclus", ha01_hoststall.describe(excl)))
    res = analyse(log, ids, t0, t1, exclude=excl)
    for k, v in res.items():
        print("%-22s %s" % (k, v))


if __name__ == "__main__":
    sys.exit(main())
