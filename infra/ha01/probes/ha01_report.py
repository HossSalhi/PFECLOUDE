#!/usr/bin/env python3
"""
HA-01 — rapport de disponibilité à partir des JSONL de ha01_probe.py.

Usage :
  python ha01_report.py probe-*.jsonl [--from 2026-11-03T14:30:00Z] [--to ...] [--json out.json]

Pour chaque sonde : n, disponibilité %, plus longue série d'échecs (secondes, du 1er au dernier échec
consécutif + intervalle), latence p50/p95/max, répartition des IP jointes.
"""
import argparse
import glob
import json
import sys
from collections import Counter
from datetime import datetime, timezone


def parse_ts(s):
    return datetime.strptime(s.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S.%f%z") if "." in s else \
        datetime.strptime(s.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S%z")


def pct(vals, p):
    if not vals:
        return 0
    v = sorted(vals)
    k = min(len(v) - 1, max(0, int(round((p / 100.0) * (len(v) - 1)))))
    return v[k]


def analyse(records, interval=1.0):
    by = {}
    for r in records:
        if r.get("probe", "").startswith("_"):
            continue
        by.setdefault(r["probe"], []).append(r)
    out = {}
    for name, rs in sorted(by.items()):
        rs.sort(key=lambda r: r["ts"])
        n = len(rs)
        ok = sum(1 for r in rs if r["ok"])
        lat = [r["ms"] for r in rs if r["ok"]]
        longest = 0.0
        cur_start = None
        last = None
        runs = []
        for r in rs:
            t = parse_ts(r["ts"]).timestamp()
            if not r["ok"]:
                if cur_start is None:
                    cur_start = t
                last = t
            else:
                if cur_start is not None:
                    runs.append((cur_start, last))
                    cur_start = None
        if cur_start is not None:
            runs.append((cur_start, last))
        for a, b in runs:
            longest = max(longest, (b - a) + interval)
        errs = Counter((r.get("err") or "")[:50] for r in rs if not r["ok"])
        ips = Counter(r.get("ip") for r in rs)
        out[name] = {
            "n": n, "ok": ok, "availability_pct": round(100.0 * ok / n, 3) if n else None,
            "failures": n - ok, "longest_failure_s": round(longest, 1),
            "failure_runs": [[datetime.fromtimestamp(a, timezone.utc).strftime("%H:%M:%S"),
                              round((b - a) + interval, 1)] for a, b in runs][:20],
            "lat_ms_p50": pct(lat, 50), "lat_ms_p95": pct(lat, 95), "lat_ms_max": max(lat) if lat else 0,
            "ips": dict(ips), "top_errors": dict(errs.most_common(3)),
            "first": rs[0]["ts"] if rs else None, "last": rs[-1]["ts"] if rs else None,
        }
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--from", dest="t0")
    ap.add_argument("--to", dest="t1")
    ap.add_argument("--json")
    ap.add_argument("--interval", type=float, default=1.0)
    a = ap.parse_args()
    paths = []
    for f in a.files:
        paths.extend(glob.glob(f) or [f])
    recs = []
    for p in paths:
        with open(p, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    recs.append(json.loads(line))
                except ValueError:
                    pass
    if a.t0:
        recs = [r for r in recs if r["ts"] >= a.t0]
    if a.t1:
        recs = [r for r in recs if r["ts"] <= a.t1]
    res = analyse(recs, a.interval)
    print("%-10s %7s %9s %6s %9s %8s %8s %8s  %s" % ("sonde", "n", "dispo %", "echecs", "serie max", "p50 ms", "p95 ms", "max ms", "IP jointes"))
    for name, v in res.items():
        print("%-10s %7d %9s %6d %8ss %8d %8d %8d  %s" % (
            name, v["n"], v["availability_pct"], v["failures"], v["longest_failure_s"],
            v["lat_ms_p50"], v["lat_ms_p95"], v["lat_ms_max"], v["ips"]))
        if v["failures"]:
            print("           séries d'échecs (début UTC, durée s): %s | erreurs: %s" % (v["failure_runs"][:6], v["top_errors"]))
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump(res, fh, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    sys.exit(main())
