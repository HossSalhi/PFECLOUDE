#!/usr/bin/env python3
"""
HA-01 — observation ACTIVE après une modification (lecture seule), avec abandon automatique.
  python p1_observe.py --minutes 9 --label "C1 .162" [--journal "ligne de journal"]

Toutes les 15 s : échecs des sondes (poste Windows ET .161) depuis le début, MemAvailable + pression CPU de .153, santé etcd (30 s),
RAM de l'hôte, état Ready des nœuds. ABANDON (code 2, motif imprimé) si :
  série d'échecs > 10 s sur web/api_app (n'importe quel point de vue) · .153 MemAvailable < 250 Mo · etcd < 4/5 · hôte < 600 Mo · nœud NotReady
Sortie : synthèse par sonde et point de vue ; code 0 = aucune anomalie, 1 = échecs isolés <= 10 s (détaillés), 2 = abandon.
"""
import argparse
import ctypes
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.normpath(os.path.join(HERE, "..", "results", "2026-11-03"))
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "LogLevel=ERROR"]
ETCD_EPS = ",".join("https://192.168.148.%s:2379" % s for s in ("153", "155", "158", "160", "162"))
ETCD = ("sudo -n env ETCDCTL_API=3 etcdctl --endpoints=%s --cacert=/etc/kubernetes/pki/etcd/ca.crt --cert=/etc/kubernetes/pki/etcd/server.crt "
        "--key=/etc/kubernetes/pki/etcd/server.key --command-timeout=6s" % ETCD_EPS)


def ssh(ip, cmd, timeout=30):
    try:
        r = subprocess.run(SSH + ["kube@192.168.148.%s" % ip, cmd], capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout
    except subprocess.TimeoutExpired:
        return 124, ""


def host_mb():
    class MS(ctypes.Structure):
        _fields_ = [("l", ctypes.c_ulong), ("ml", ctypes.c_ulong), ("tp", ctypes.c_ulonglong), ("ap", ctypes.c_ulonglong),
                    ("a", ctypes.c_ulonglong), ("b", ctypes.c_ulonglong), ("c", ctypes.c_ulonglong), ("d", ctypes.c_ulonglong), ("e", ctypes.c_ulonglong)]
    m = MS()
    m.l = ctypes.sizeof(MS)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return int(m.ap / 1048576)


def read_tail(path, n=60000):
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        fh.seek(max(0, fh.tell() - n))
        return fh.read().decode("utf-8", "replace").splitlines()


def parse(lines, since):
    out = []
    for l in lines:
        try:
            r = json.loads(l)
        except ValueError:
            continue
        if not r.get("probe", "").startswith("_") and r["ts"] >= since:
            out.append(r)
    return out


def runs(recs, probe):
    rs = sorted((r for r in recs if r["probe"] == probe), key=lambda r: r["ts"])
    best = cur = 0
    for r in rs:
        cur = 0 if r["ok"] else cur + 1
        best = max(best, cur)
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=9)
    ap.add_argument("--label", default="observation")
    a = ap.parse_args()
    t0 = time.time()
    since = datetime.fromtimestamp(t0, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    tend = t0 + a.minutes * 60
    stats = {"mem153_min": 9e9, "psi153_max": 0.0, "host_min": 9e9, "etcd_min": 5}
    last_etcd = 0
    abort = None
    print("[%s] observation '%s' pendant %.1f min (depuis %sZ)" % (datetime.now(timezone.utc).strftime("%H:%M:%S"), a.label, a.minutes, since), flush=True)
    while time.time() < tend and not abort:
        cyc = time.time()
        rc, out = ssh(153, "awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo; head -1 /proc/pressure/cpu | sed 's/.*avg10=\\([0-9.]*\\).*/\\1/'")
        v = out.split()
        if rc == 0 and len(v) >= 2:
            stats["mem153_min"] = min(stats["mem153_min"], int(v[0]))
            stats["psi153_max"] = max(stats["psi153_max"], float(v[1]))
            if int(v[0]) < 250:
                abort = ".153 MemAvailable %s Mo < 250" % v[0]
        h = host_mb()
        stats["host_min"] = min(stats["host_min"], h)
        if h < 600:
            abort = abort or "RAM hôte %d Mo < 600" % h
        if time.time() - last_etcd > 30:
            last_etcd = time.time()
            rc, out = ssh(155, ETCD + " endpoint health -w json 2>/dev/null")
            try:
                n = sum(1 for e in json.loads(out) if e.get("health"))
            except Exception:  # noqa: BLE001
                n = 0
            stats["etcd_min"] = min(stats["etcd_min"], n)
            if n < 4:
                abort = abort or "etcd %d/5" % n
            rc, out = ssh(155, "kubectl get nodes --no-headers --request-timeout=15s")
            bad = [l.split()[0] for l in out.splitlines() if len(l.split()) > 1 and l.split()[1] != "Ready"]
            if bad:
                abort = abort or "nœud(s) non Ready: %s" % bad
        win = parse(read_tail(os.path.join(RES, "probe-baseline-p0.jsonl")), since)
        rc, o161 = ssh(161, "tail -n 700 /opt/helios-backups/ha01/probe161-p0.jsonl")
        w161 = parse(o161.splitlines(), since)
        for lab, recs in (("Windows", win), (".161", w161)):
            for p in ("web", "api_app"):
                if runs(recs, p) > 10:
                    abort = abort or "%s: %s en échec > 10 s" % (lab, p)
        d = 15 - (time.time() - cyc)
        if d > 0 and not abort:
            time.sleep(d)
    # synthèse
    win = parse(read_tail(os.path.join(RES, "probe-baseline-p0.jsonl"), 400000), since)
    rc, o161 = ssh(161, "tail -n 6000 /opt/helios-backups/ha01/probe161-p0.jsonl", timeout=60)
    w161 = parse(o161.splitlines(), since)
    total_fail = 0
    print("--- synthèse '%s' (%.1f min) ---" % (a.label, (time.time() - t0) / 60))
    for lab, recs in (("Windows", win), (".161", w161)):
        by = {}
        for r in recs:
            d = by.setdefault(r["probe"], [0, 0])
            d[0] += 1
            d[1] += 0 if r["ok"] else 1
        total_fail += sum(v[1] for v in by.values())
        print("  %-8s %s" % (lab, "  ".join("%s %d/%d" % (p, v[0] - v[1], v[0]) for p, v in sorted(by.items()))))
    print("  .153 : MemAvailable min=%s Mo, pression CPU max=%.1f %% ; etcd min=%d/5 ; RAM hôte min=%d Mo" % (stats["mem153_min"], stats["psi153_max"], stats["etcd_min"], stats["host_min"]))
    if abort:
        print("ABANDON: %s" % abort)
        return 2
    print("RÉSULTAT: %s" % ("aucune anomalie" if total_fail == 0 else "%d échec(s) isolé(s) <= 10 s (voir sondes)" % total_fail))
    return 0 if total_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
