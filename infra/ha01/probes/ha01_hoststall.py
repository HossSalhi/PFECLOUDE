#!/usr/bin/env python3
"""
HA-01 — repérage des « gels d'E/S de l'hôte » (lecture seule, depuis le Prometheus de .160).

Principe. Les 5 membres etcd tournent sur 5 VM différentes. Quand UNE VM tombe ou sature, les autres membres restent
rapides. Quand le SSD ou l'hôte Windows se fige, TOUTES les VM voient leur fsync WAL exploser EN MÊME TEMPS : une panne
de service ne produit jamais cette signature. Constaté le 4 nov. 05:00 UTC : p99(fsync WAL) = 3,3 à 7,0 s sur 4 membres
sur 5, et les compteurs du SSD hôte (Intel NVMe 512 Go, C: occupé à 93 %) affichent une latence maximale cumulée de
5,6 s en lecture / 5,9 s en écriture / 2,1 s en flush.

Critère (paramétrable) : au moins --min-members membres avec p99(fsync WAL sur 30 s) > --threshold s au même instant.
Une mesure de requête à l'instant T décrit [T-30 s, T] : la fenêtre d'un gel est donc [premier T - 30 s, dernier T].

Ces fenêtres servent à CLASSER les échecs des sondes/canaris (exogène vs imputable à HA-01). Elles ne cachent rien : les
rapports publient toujours le chiffre BRUT, le chiffre HORS GELS et la liste des fenêtres.

Usage :
  python ha01_hoststall.py [--hours 26] | [--from ISO --to ISO]  [--min-members 3] [--threshold 1.0]
Bibliothèque : windows(t0, t1) -> [(début, fin), ...] en epoch UTC, ou None si Prometheus est injoignable
(par ex. .160 arrêtée) : dans ce cas AUCUNE exemption n'est appliquée par les appelants.
"""
import argparse
import collections
import concurrent.futures
import json
import re
import sys
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

PROM = "http://192.168.148.160:9090"
METRICS_PORT = 2381                      # etcd --listen-metrics-urls (http), joignable sur les 5 membres
_BUCKET_RE = re.compile(r'^etcd_disk_wal_fsync_duration_seconds_bucket\{le="([^"]+)"\}\s+([0-9.eE+\-]+)')
LOOKBACK_S = 30        # fenêtre de rate() dans la requête
P99 = ('histogram_quantile(0.99, sum by (instance,le)'
       '(rate(etcd_disk_wal_fsync_duration_seconds_bucket[%ds])))' % LOOKBACK_S)


def _range(query, t0, t1, step):
    url = PROM + "/api/v1/query_range?" + urllib.parse.urlencode({"query": query, "start": t0, "end": t1, "step": step})
    with urllib.request.urlopen(url, timeout=60) as r:
        data = json.load(r)
    if data.get("status") != "success":
        raise RuntimeError(str(data)[:200])
    return data["data"]["result"]


def windows(t0, t1, min_members=3, threshold_s=1.0, step=5, merge_gap_s=30, with_peak=False):
    """Fenêtres de gel d'E/S de l'hôte entre t0 et t1 (epoch). None si Prometheus injoignable."""
    t1 = min(t1, time.time())
    try:
        # Prometheus limite à 11 000 points par série : on découpe par tranches de 12 h à pas de 5 s (8 640 points)
        pts, peak = [], {}
        a = t0
        while a < t1:
            b = min(a + 12 * 3600, t1)
            res = _range("count(%s > %s)" % (P99, threshold_s), a, b, step)
            if res:
                pts += [(float(x), int(float(y))) for x, y in res[0]["values"]]
            if with_peak:
                res = _range("max(%s)" % P99, a, b, step)
                if res:
                    for x, y in res[0]["values"]:
                        peak[float(x)] = float(y)
            a = b + step
    except Exception:  # noqa: BLE001  (Prometheus indisponible, réseau, JSON)
        return None
    out, cur = [], None
    for ts, n in sorted(pts):
        if n < min_members:
            continue
        if cur and ts - cur["end"] <= merge_gap_s:
            cur["end"] = ts
            cur["members"] = max(cur["members"], n)
            cur["peak_s"] = max(cur["peak_s"], peak.get(ts, 0.0))
        else:
            if cur:
                out.append(cur)
            cur = {"start": ts - LOOKBACK_S, "end": ts, "members": n, "peak_s": peak.get(ts, 0.0)}
    if cur:
        out.append(cur)
    if with_peak:
        return out
    return [(w["start"], w["end"]) for w in out]


def parse_fsync_buckets(text):
    """Texte /metrics d'etcd -> {borne le (float, inf pour +Inf): compte cumulé} du fsync WAL."""
    out = {}
    for line in text.splitlines():
        m = _BUCKET_RE.match(line)
        if m:
            out[float("inf") if m.group(1) == "+Inf" else float(m.group(1))] = float(m.group(2))
    return out


def quantile_from_buckets(delta, q=0.99):
    """Équivalent de histogram_quantile(q) de Prometheus sur des compteurs cumulés {le: n} (deltas sur la fenêtre).
    None s'il n'y a aucun échantillon."""
    items = sorted(delta.items())
    if not items or items[-1][1] <= 0:
        return None
    rank = q * items[-1][1]
    prev_le, prev_c = 0.0, 0.0
    for le, c in items:
        if c >= rank:
            if le == float("inf"):
                return prev_le                       # comme Prometheus : borne haute du dernier bucket fini
            if c == prev_c:
                return le
            return prev_le + (le - prev_le) * (rank - prev_c) / (c - prev_c)
        prev_le, prev_c = le, c
    return prev_le


class LiveDetector(threading.Thread):
    """Détecteur de gels d'E/S EN DIRECT, sans Prometheus ni `.160` : lit http://<ip>:2381/metrics de chaque membre etcd
    joignable toutes les `period` s, calcule le p99 du fsync WAL sur `lookback_s` (comme Prometheus) et note les instants
    où >= min_members membres dépassent threshold_s. Indispensable quand `.160` (donc Prometheus) est isolée ou éteinte.
        d = LiveDetector(["192.168.148.155", ...]); d.start(); ...; d.stop(); d.join(); d.windows()
    """

    def __init__(self, ips, period=5.0, lookback_s=30.0, min_members=3, threshold_s=1.0):
        super().__init__(daemon=True)
        self.ips, self.period, self.lookback = list(ips), float(period), float(lookback_s)
        self.min_members, self.threshold = int(min_members), float(threshold_s)
        self.samples = {ip: collections.deque() for ip in self.ips}
        self.points = []                 # (epoch, nombre de membres lents, {ip: p99 en s})
        self._halt = threading.Event()

    @staticmethod
    def _scrape(ip):
        try:
            with urllib.request.urlopen("http://%s:%d/metrics" % (ip, METRICS_PORT), timeout=2.5) as r:
                return parse_fsync_buckets(r.read().decode("utf-8", "replace"))
        except Exception:  # noqa: BLE001 - membre injoignable : ignoré ce tour-ci
            return None

    def run(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(self.ips))) as ex:
            while not self._halt.is_set():
                t = time.time()
                p99 = {}
                for ip, b in zip(self.ips, ex.map(self._scrape, self.ips)):
                    if b is None:
                        continue
                    dq = self.samples[ip]
                    dq.append((t, b))
                    while dq and t - dq[0][0] > self.lookback + self.period:
                        dq.popleft()
                    if len(dq) >= 2:
                        b0 = dq[0][1]
                        p99[ip] = quantile_from_buckets({le: max(0.0, b.get(le, 0.0) - b0.get(le, 0.0)) for le in b})
                self.points.append((t, sum(1 for v in p99.values() if v is not None and v > self.threshold), p99))
                self._halt.wait(max(0.0, self.period - (time.time() - t)))

    def stop(self):
        self._halt.set()

    def current(self):
        """Dernier p99 par membre (s) — pour les journaux de test."""
        return dict(self.points[-1][2]) if self.points else {}

    def windows(self, merge_gap_s=30):
        out, cur = [], None
        for t, n, _ in self.points:
            if n < self.min_members:
                continue
            if cur and t - cur[1] <= merge_gap_s:
                cur[1] = t
            else:
                if cur:
                    out.append((cur[0], cur[1]))
                cur = [t - self.lookback, t]
        if cur:
            out.append((cur[0], cur[1]))
        return out


def inside(wins, ts, before_s=5.0, after_s=5.0):
    """ts (epoch) tombe-t-il dans une fenêtre élargie de before_s/after_s ?"""
    return any(a - before_s <= ts <= b + after_s for a, b in (wins or []))


def overlaps(wins, t_start, t_end, before_s=5.0, after_s=5.0):
    """Le segment [t_start, t_end] chevauche-t-il une fenêtre élargie ?"""
    return any(t_start <= b + after_s and t_end >= a - before_s for a, b in (wins or []))


def fmt(ts, local=False):
    d = datetime.fromtimestamp(ts, timezone.utc)
    if local:
        d = datetime.fromtimestamp(ts)
    return d.strftime("%m-%d %H:%M:%S")


def describe(wins):
    """Texte court pour les rapports."""
    if wins is None:
        return "classification impossible (Prometheus injoignable) : aucune exemption appliquée"
    if not wins:
        return "aucun"
    return "; ".join("%s -> %s UTC" % (fmt(a), fmt(b)[6:]) for a, b in wins)


def _epoch(s):
    try:
        return float(s)
    except ValueError:
        return datetime.strptime(s.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S%z").timestamp()


def main():
    for _flux in (sys.stdout, sys.stderr):
        try:
            _flux.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--hours", type=float, default=26)
    ap.add_argument("--from", dest="t0")
    ap.add_argument("--to", dest="t1")
    ap.add_argument("--min-members", type=int, default=3)
    ap.add_argument("--threshold", type=float, default=1.0)
    a = ap.parse_args()
    t1 = _epoch(a.t1) if a.t1 else time.time()
    t0 = _epoch(a.t0) if a.t0 else t1 - a.hours * 3600
    ws = windows(t0, t1, a.min_members, a.threshold, with_peak=True)
    if ws is None:
        print("Prometheus injoignable (%s) : classification impossible." % PROM)
        return 2
    print("Gels d'E/S simultanés (>= %d membres etcd avec p99 fsync WAL > %.1f s) du %s au %s UTC : %d"
          % (a.min_members, a.threshold, fmt(t0), fmt(t1), len(ws)))
    for w in ws:
        print("  %s -> %s UTC (%s locale)  durée ~%3d s  membres=%d/5  p99 max=%.1f s"
              % (fmt(w["start"]), fmt(w["end"])[6:], fmt(w["start"], True)[6:], w["end"] - w["start"], w["members"], w["peak_s"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
