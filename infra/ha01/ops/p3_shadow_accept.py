#!/usr/bin/env python3
"""
HA-01 Phase 3 — recette du contrôleur candidat (Traefik, NodePorts 32080/32443) comparée à ingress-nginx (31756/31411).
Lecture seule (uniquement des GET). Vérifie, sur chaque worker (.155, .158) :
  1. statut + corps identiques pour `/` et `/api/test/health-readiness` (http et https)
  2. certificat TLS servi identique (empreinte SHA-256 du certificat feuille)
  3. ssl-redirect=false respecté (pas de 3xx sur :80)
  4. hôte inconnu -> même statut (404) des deux côtés
  5. SSE `/api/events/stream` : premier octet < 2 s (pas de buffering) et connexion maintenue >= --sse-seconds sans coupure
  6. charge : 30 req/s pendant 60 s sur le candidat : 100 % de succès
Usage : python p3_shadow_accept.py [--sse-seconds 120]      code retour 0 = recette réussie
"""
import argparse
import hashlib
import socket
import ssl
import sys
import threading
import time

# Sortie toujours en UTF-8 (défaut Windows = cp1252 : les symboles ✅/❌ font planter print(), même redirigé vers un fichier).
for _flux in (sys.stdout, sys.stderr):
    try:
        _flux.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

WORKERS ={"155": "192.168.148.155", "158": "192.168.148.158"}
OLD = {"http": 31756, "https": 31411}
NEW = {"http": 32080, "https": 32443}
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
HOST = "helios.orange.tn"
results = []


def _dechunk(raw):
    """Décode un corps HTTP « Transfer-Encoding: chunked » (suite de blocs <taille hexa>\\r\\n<données>\\r\\n, terminée par un bloc de taille 0)."""
    out, i = b"", 0
    while i < len(raw):
        j = raw.find(b"\r\n", i)
        if j < 0:
            break
        try:
            n = int(raw[i:j].split(b";")[0].strip() or b"0", 16)
        except ValueError:
            break
        if n == 0:
            break
        out += raw[j + 2:j + 2 + n]
        i = j + 2 + n + 2
    return out


def req(ip, port, path="/", tls=False, host=HOST, read_limit=262144, timeout=8):
    t0 = time.time()
    s = socket.create_connection((ip, port), timeout=timeout)
    try:
        s.settimeout(timeout)
        cert = None
        if tls:
            s = CTX.wrap_socket(s, server_hostname=HOST)
            cert = hashlib.sha256(s.getpeercert(binary_form=True)).hexdigest()
        s.sendall(("GET %s HTTP/1.1\r\nHost: %s\r\nUser-Agent: ha01-accept\r\nConnection: close\r\n\r\n" % (path, host)).encode())
        buf = b""
        while len(buf) < read_limit:
            c = s.recv(16384)
            if not c:
                break
            buf += c
        head, _, body = buf.partition(b"\r\n\r\n")
        st = int(head.split(b" ", 2)[1]) if head.startswith(b"HTTP/") else 0
        if b"transfer-encoding: chunked" in head.lower():
            body = _dechunk(body)       # Traefik répond en « chunked » : sans décodage on comparait le corps + les marqueurs de blocs (+13 octets)
        return {"status": st, "body": hashlib.sha256(body).hexdigest(), "len": len(body), "cert": cert, "ms": int((time.time() - t0) * 1000), "head": head.decode("latin1", "replace")}
    finally:
        s.close()


def sse(ip, port, seconds):
    s = socket.create_connection((ip, port), timeout=8)
    s = CTX.wrap_socket(s, server_hostname=HOST)
    s.settimeout(5)
    t0 = time.time()
    s.sendall(("GET /api/events/stream HTTP/1.1\r\nHost: %s\r\nAccept: text/event-stream\r\nConnection: keep-alive\r\n\r\n" % HOST).encode())
    first = None
    got = b""
    closed_at = None
    while time.time() - t0 < seconds:
        try:
            c = s.recv(4096)
            if not c:
                closed_at = time.time() - t0
                break
            got += c
            if first is None and b"connected" in got:
                first = time.time() - t0
        except socket.timeout:
            continue
        except OSError:
            closed_at = time.time() - t0
            break
    s.close()
    return first, closed_at, len(got)


def record(name, ok, detail):
    results.append((name, bool(ok), detail))
    print("%s  %-62s %s" % ("OK" if ok else "KO", name, detail), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sse-seconds", type=int, default=120)
    a = ap.parse_args()
    for w, ip in WORKERS.items():
        for path in ("/", "/api/test/health-readiness"):
            try:
                o_h, n_h = req(ip, OLD["http"], path), req(ip, NEW["http"], path)
                record(".%s http  %s identique" % (w, path), o_h["status"] == n_h["status"] == 200 and o_h["body"] == n_h["body"],
                       "ingress-nginx=%s/%dB traefik=%s/%dB" % (o_h["status"], o_h["len"], n_h["status"], n_h["len"]))
                o_s, n_s = req(ip, OLD["https"], path, tls=True), req(ip, NEW["https"], path, tls=True)
                record(".%s https %s identique" % (w, path), o_s["status"] == n_s["status"] == 200 and o_s["body"] == n_s["body"],
                       "ingress-nginx=%s traefik=%s" % (o_s["status"], n_s["status"]))
                if path == "/":
                    record(".%s certificat TLS identique" % w, o_s["cert"] == n_s["cert"] and o_s["cert"], "sha256 %s…" % (n_s["cert"] or "")[:16])
            except Exception as e:  # noqa: BLE001
                record(".%s %s" % (w, path), False, "%s: %s" % (type(e).__name__, e))
        try:
            n = req(ip, NEW["http"], "/")
            record(".%s ssl-redirect=false : pas de 3xx sur :80" % w, n["status"] == 200, "statut %s" % n["status"])
            o_u, n_u = req(ip, OLD["http"], "/", host="inconnu.example"), req(ip, NEW["http"], "/", host="inconnu.example")
            record(".%s hôte inconnu : même statut" % w, o_u["status"] == n_u["status"], "ingress-nginx=%s traefik=%s" % (o_u["status"], n_u["status"]))
        except Exception as e:  # noqa: BLE001
            record(".%s redirect/hôte inconnu" % w, False, "%s: %s" % (type(e).__name__, e))
    ip = WORKERS["155"]
    try:
        for label, port in (("ingress-nginx", OLD["https"]), ("traefik", NEW["https"])):
            first, closed, nbytes = sse(ip, port, a.sse_seconds) if label == "traefik" else sse(ip, port, 20)
            if label == "traefik":
                record("SSE traefik : 1er octet < 2 s (pas de buffering)", first is not None and first < 2.0, "ttfb=%s s" % (round(first, 2) if first else None))
                record("SSE traefik : connexion maintenue %d s sans coupure" % a.sse_seconds, closed is None, "fermée à %s s" % (round(closed, 1) if closed else "jamais"))
            else:
                print("   (référence ingress-nginx : ttfb=%s s, octets=%d)" % (round(first, 2) if first else None, nbytes))
    except Exception as e:  # noqa: BLE001
        record("SSE", False, "%s: %s" % (type(e).__name__, e))
    # charge 30 req/s pendant 60 s sur le candidat (les 2 workers alternés)
    stats = {"n": 0, "ok": 0, "lat": []}
    lock = threading.Lock()
    t_end = time.time() + 60
    nxt = [time.time()]

    def worker(i):
        ips = list(WORKERS.values())
        k = 0
        while True:
            with lock:
                when = nxt[0]
                if when >= t_end:
                    return
                nxt[0] += 1 / 30.0
            d = when - time.time()
            if d > 0:
                time.sleep(d)
            k += 1
            try:
                r = req(ips[(i + k) % 2], NEW["https"], "/", tls=True, read_limit=65536)
                ok = r["status"] == 200
                ms = r["ms"]
            except Exception:  # noqa: BLE001
                ok, ms = False, 0
            with lock:
                stats["n"] += 1
                stats["ok"] += 1 if ok else 0
                if ok:
                    stats["lat"].append(ms)
    ths = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(12)]
    [t.start() for t in ths]
    [t.join() for t in ths]
    lat = sorted(stats["lat"])
    p95 = lat[int(0.95 * (len(lat) - 1))] if lat else 0
    record("charge 30 req/s x 60 s sur Traefik : 100 % de succès", stats["n"] > 1500 and stats["ok"] == stats["n"], "%d/%d, p95=%d ms" % (stats["ok"], stats["n"], p95))
    ok_all = all(ok for _, ok, _ in results)
    print("\nRECETTE %s (%d/%d)" % ("RÉUSSIE" if ok_all else "ÉCHOUÉE", sum(1 for _, ok, _ in results if ok), len(results)))
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
