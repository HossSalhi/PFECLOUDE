#!/usr/bin/env python3
"""
HA-01 — générateur de charge HTTP(S) léger (lecture seule : uniquement des GET) pour les preuves de capacité.

  python ha01_load.py --rps 20 --duration 600 --out load.jsonl [--ip 192.168.148.162] [--paths /,/api/test/health-readiness]

--ip force la connexion vers un LB précis (SNI/Host restent helios.orange.tn) : test de capacité d'un seul LB.
Sans --ip : résolution OS (hosts/NRPT), comme un vrai client.
Sortie : une ligne JSONL par requête (ts, path, ok, status, ms, ip) + résumé final sur stdout.
Cadence : rps requêtes/s réparties sur --workers threads ; chaque requête a un délai total de 8 s.
"""
import argparse
import json
import socket
import ssl
import sys
import threading
import time
from datetime import datetime, timezone

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
HOST = "helios.orange.tn"
_lock = threading.Lock()


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def fetch(path, ip):
    t0 = time.time()
    sock = None
    try:
        sock = socket.create_connection((ip or HOST, 443), timeout=3)
        peer = sock.getpeername()[0]
        sock.settimeout(5)
        s = CTX.wrap_socket(sock, server_hostname=HOST)
        s.sendall(("GET %s HTTP/1.1\r\nHost: %s\r\nUser-Agent: ha01-load\r\nAccept: */*\r\nConnection: close\r\n\r\n" % (path, HOST)).encode())
        buf = b""
        while len(buf) < 262144:
            c = s.recv(16384)
            if not c:
                break
            buf += c
        line = buf.split(b"\r\n", 1)[0].decode("latin1", "replace").split(" ")
        st = int(line[1]) if len(line) > 1 and line[1].isdigit() else 0
        return st == 200, st, int((time.time() - t0) * 1000), peer
    except Exception:  # noqa: BLE001
        return False, 0, int((time.time() - t0) * 1000), None
    finally:
        try:
            if sock:
                sock.close()
        except Exception:  # noqa: BLE001
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rps", type=float, default=20)
    ap.add_argument("--duration", type=float, default=60)
    ap.add_argument("--out", required=True)
    ap.add_argument("--ip")
    ap.add_argument("--paths", default="/,/api/test/health-readiness")
    ap.add_argument("--workers", type=int, default=16)
    a = ap.parse_args()
    paths = [p for p in a.paths.split(",") if p]
    t_end = time.time() + a.duration
    interval = 1.0 / a.rps
    stats = {"n": 0, "ok": 0, "lat": []}
    idx = [0]
    nxt = [time.time()]
    out = open(a.out, "a", encoding="utf-8")

    def worker():
        while True:
            with _lock:
                now = time.time()
                if now >= t_end:
                    return
                when = nxt[0]
                if when >= t_end:      # ne pas exécuter de créneaux planifiés après la fin de la fenêtre
                    return
                nxt[0] += interval
                path = paths[idx[0] % len(paths)]
                idx[0] += 1
            d = when - time.time()
            if d > 0:
                time.sleep(d)
            ok, st, ms, peer = fetch(path, a.ip)
            rec = {"ts": now_iso(), "path": path, "ok": ok, "status": st, "ms": ms, "ip": peer}
            with _lock:
                stats["n"] += 1
                stats["ok"] += 1 if ok else 0
                if ok:
                    stats["lat"].append(ms)
                out.write(json.dumps(rec) + "\n")
                out.flush()

    ths = [threading.Thread(target=worker, daemon=True) for _ in range(a.workers)]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    lat = sorted(stats["lat"])
    pct = lambda p: lat[min(len(lat) - 1, int(p / 100 * (len(lat) - 1)))] if lat else 0  # noqa: E731
    print(json.dumps({"requests": stats["n"], "ok": stats["ok"], "ok_pct": round(100.0 * stats["ok"] / max(1, stats["n"]), 3),
                      "p50_ms": pct(50), "p95_ms": pct(95), "p99_ms": pct(99), "max_ms": lat[-1] if lat else 0,
                      "target_ip": a.ip or "dns"}))


if __name__ == "__main__":
    sys.exit(main())
