#!/usr/bin/env python3
"""
Lancement .162 (5 nov. 2026) — VEILLEUSE : script jetable CONSERVÉ COMME PREUVE (hors ops/). LECTURE SEULE : aucune écriture ailleurs que dans ce dossier,
aucune action sur le cluster, aucune libération de RAM. Lancée détachée (Start-Process) ; s'arrête quand le fichier veille.stop existe (ou après 4 h).

Relevé toutes les 30 s -> veille.jsonl (ajout uniquement). Au PREMIER événement : écrit EVENEMENT.txt (création exclusive) ; continue de journaliser.
Événements (plan §5) :
  - gel d'E/S de l'hôte : fenêtre Prometheus (>= 3 membres etcd avec fsync p99 > 1 s) dont la fin est postérieure à l'armement ;
  - baisse de RAM : TOUT point de l'échantillonneur (10 s) postérieur à l'armement < 2300 Mo ; < 2000 Mo pendant la fenêtre de charge déclarée dans
    FENETRE-CHARGE.json ({"debut": epoch, "fin": epoch}, où fin = fin de p5 + 60 s) ; plancher dur < 600 Mo (toujours) ;
  - dégradation etcd : un membre `GET :2381/health` non « true » (confirmé par une re-lecture 1 s plus tard), terme Raft != 1820, leader != .153 (SSH toutes les 60 s) ;
  - énergie / outils : secteur perdu, batterie < 40 %, sonde/échantillonneur Windows figés (> 30 s / > 60 s), unité ha01-probe-161 inactive (SSH toutes les 120 s).
"""
import calendar
import ctypes
import json
import os
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.normpath(os.path.join(HERE, ".."))
SAMPLES = os.path.join(RES, "samples-baseline-p0.jsonl")
PROBE = os.path.join(RES, "probe-baseline-p0.jsonl")
sys.path.insert(0, os.path.normpath(os.path.join(RES, "..", "..", "probes")))
import ha01_hoststall as D  # noqa: E402  (module léger, lecture de Prometheus uniquement)

NET = "192.168.148."
MEMBERS = ("153", "155", "158", "160", "162")
LEADER_153 = "6abe8269b59f1815"
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "LogLevel=ERROR"]
ETCD_EPS = ",".join("https://%s%s:2379" % (NET, s) for s in MEMBERS)
ETCDCTL = ("sudo -n env ETCDCTL_API=3 etcdctl --endpoints=%s --cacert=/etc/kubernetes/pki/etcd/ca.crt --cert=/etc/kubernetes/pki/etcd/server.crt "
           "--key=/etc/kubernetes/pki/etcd/server.key --command-timeout=6s endpoint status -w json" % ETCD_EPS)
ep = lambda ts: calendar.timegm(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"))
iso = lambda t: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


class SPS(ctypes.Structure):
    _fields_ = [("AC", ctypes.c_ubyte), ("Flag", ctypes.c_ubyte), ("Pct", ctypes.c_ubyte), ("Sav", ctypes.c_ubyte), ("Life", ctypes.c_ulong), ("Full", ctypes.c_ulong)]


def power():
    s = SPS()
    ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(s))
    return s.AC == 1, int(s.Pct)


def ssh(host, cmd, timeout=25):
    try:
        r = subprocess.run(SSH + ["kube@" + NET + host, cmd], capture_output=True, timeout=timeout)
        return r.returncode, r.stdout.decode("utf-8", "replace").strip()
    except subprocess.TimeoutExpired:
        return 124, ""


def health_bad():
    bad = []
    for m in MEMBERS:
        try:
            ok = json.load(urllib.request.urlopen("http://%s%s:2381/health" % (NET, m), timeout=2)).get("health") == "true"
        except Exception:  # noqa: BLE001
            ok = False
        if not ok:
            bad.append(m)
    return bad


def new_points(since):
    pts = []
    with open(SAMPLES, "rb") as fh:
        fh.seek(max(0, os.path.getsize(SAMPLES) - 60_000))
        for l in fh.read().decode("utf-8", "replace").splitlines():
            if '"host_avail_mb"' in l:
                try:
                    r = json.loads(l)
                    t = ep(r["ts"])
                except ValueError:
                    continue
                if t > since:
                    pts.append((t, r["host_avail_mb"]))
    return pts


def charge_window():
    p = os.path.join(HERE, "FENETRE-CHARGE.json")
    try:
        j = json.load(open(p, encoding="utf-8"))
        return float(j["debut"]), float(j["fin"])
    except Exception:  # noqa: BLE001
        return None


def main():
    t_arm = time.time()
    log = open(os.path.join(HERE, "veille.jsonl"), "a", encoding="utf-8")
    event, last_pt, last_ssh60, last_ssh120, prom_fail = None, t_arm, 0.0, 0.0, 0
    stop = os.path.join(HERE, "veille.stop")
    log.write(json.dumps({"ts": iso(t_arm), "evenement": "ARMEMENT", "pid": os.getpid()}) + "\n")
    log.flush()
    while not os.path.exists(stop) and time.time() - t_arm < 4 * 3600:
        t0 = time.time()
        rec = {"ts": iso(t0)}
        found = []
        # RAM : tous les nouveaux points
        pts = new_points(last_pt)
        if pts:
            last_pt = max(t for t, v in pts)
        cw = charge_window()
        low = []
        for t, v in pts:
            thr = 2000 if (cw and cw[0] <= t <= cw[1]) else 2300
            if v < thr or v < 600:
                low.append((iso(t)[11:19], v, thr))
        rec["ram_points"] = len(pts)
        rec["ram_min"] = min([v for t, v in pts], default=None)
        rec["fenetre_charge"] = bool(cw and cw[0] <= t0 <= cw[1])
        if low:
            found.append("baisse de RAM : %s" % low[:6])
        # gels d'E/S
        try:
            w = D.windows(max(t_arm - 60, t0 - 150), t0)
            prom_fail = 0 if w is not None else prom_fail + 1
            rec["gels"] = [[iso(a)[11:19], iso(b)[11:19]] for a, b in (w or [])]
            if any(b >= t_arm for a, b in (w or [])):
                found.append("gel d'E/S de l'hôte : %s" % rec["gels"])
            if prom_fail >= 3:
                found.append("détection des gels impossible (Prometheus injoignable 3 fois)")
        except Exception as e:  # noqa: BLE001
            prom_fail += 1
            rec["gels_err"] = type(e).__name__
        # santé etcd
        bad = health_bad()
        if bad:
            time.sleep(1.0)
            bad = sorted(set(bad) & set(health_bad()))
        rec["etcd_non_sains"] = bad
        if bad:
            found.append("etcd : membre(s) non sains confirmés %s" % bad)
        # énergie / fichiers du harnais
        ac, pct = power()
        rec["energie"] = {"secteur": ac, "batterie": pct}
        if not ac or pct < 40:
            found.append("énergie : secteur=%s batterie=%s %%" % (ac, pct))
        ages = {"sonde": round(time.time() - os.path.getmtime(PROBE), 1), "echantillonneur": round(time.time() - os.path.getmtime(SAMPLES), 1)}
        rec["harnais_age_s"] = ages
        if ages["sonde"] > 30 or ages["echantillonneur"] > 60:
            found.append("harnais Windows figé : %s" % ages)
        # SSH : terme/leader (60 s) et sonde .161 (120 s)
        if t0 - last_ssh60 >= 60:
            last_ssh60 = t0
            rc, out = ssh("158", ETCDCTL)
            try:
                st = json.loads(out)
                term = max(int(e["Status"]["raftTerm"]) for e in st)
                lead = "%x" % int(st[0]["Status"]["leader"])
                rec["etcd_terme_leader"] = [term, lead]
                if term != 1820 or lead != LEADER_153:
                    found.append("etcd : terme/leader %s/%s (attendu 1820/%s)" % (term, lead, LEADER_153))
                if any(e["Status"].get("errors") for e in st):
                    found.append("etcd : erreurs/alarmes dans endpoint status")
            except Exception as e:  # noqa: BLE001
                rec["etcd_terme_err"] = type(e).__name__
                found.append("etcd : endpoint status illisible")
        if t0 - last_ssh120 >= 120:
            last_ssh120 = t0
            rc, out = ssh("161", "systemctl is-active ha01-probe-161")
            rec["sonde_161"] = out
            if out != "active":
                found.append("sonde .161 inactive : %r" % out)
        if found:
            rec["EVENEMENT"] = found
            if event is None:
                event = found
                with open(os.path.join(HERE, "EVENEMENT.txt"), "x", encoding="utf-8") as fh:
                    fh.write("PREMIER ÉVÉNEMENT à %s UTC (armement %s)\n%s\n" % (iso(t0), iso(t_arm), "\n".join(found)))
        log.write(json.dumps(rec, ensure_ascii=False) + "\n")
        log.flush()
        time.sleep(max(0.0, 30 - (time.time() - t0)))
    log.write(json.dumps({"ts": iso(time.time()), "evenement": "ARRÊT", "premier_evenement": event}, ensure_ascii=False) + "\n")
    log.close()


if __name__ == "__main__":
    main()
