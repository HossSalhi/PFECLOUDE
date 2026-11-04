#!/usr/bin/env python3
"""
HA-01 — sonde de disponibilité (lecture seule, 1 requête/s par cible).

Mesure ce que verrait un vrai client, depuis le poste Windows :
  web       https://helios.orange.tn/                        (nom -> DNS/hosts, bascule de LB)
  api_app   https://helios.orange.tn/api/test/health-readiness (frontend -> backend)
  kube_api  https://api.helios.orange.tn:6443/readyz         (nom -> LB -> apiserver)
  lbNNN_*   mêmes tests mais en IP directe d'un LB (indépendant du DNS) :
            permet de séparer « le LB est mort » de « le DNS a mal basculé ».

Chaque ligne JSONL : ts (UTC, ms), probe, ok, status, ms, ip (pair TCP réellement joint), err.
Sans dépendance externe (stdlib). Arrêt : Ctrl+C, --duration, ou fichier STOP à côté de --out.
"""
import argparse
import json
import os
import random
import re
import socket
import ssl
import struct
import sys
import threading
import time
from datetime import datetime, timezone

TARGETS = {
    "web":       dict(host="helios.orange.tn",     port=443,  path="/",                           sni="helios.orange.tn",     hosthdr="helios.orange.tn"),
    "api_app":   dict(host="helios.orange.tn",     port=443,  path="/api/test/health-readiness",  sni="helios.orange.tn",     hosthdr="helios.orange.tn"),
    "kube_api":  dict(host="api.helios.orange.tn", port=6443, path="/readyz",                     sni="api.helios.orange.tn", hosthdr="api.helios.orange.tn"),
    "lb160_web": dict(host="192.168.148.160",      port=443,  path="/",                           sni="helios.orange.tn",     hosthdr="helios.orange.tn"),
    "lb162_web": dict(host="192.168.148.162",      port=443,  path="/",                           sni="helios.orange.tn",     hosthdr="helios.orange.tn"),
    "lb160_api": dict(host="192.168.148.160",      port=6443, path="/readyz",                     sni="api.helios.orange.tn", hosthdr="api.helios.orange.tn"),
    "lb162_api": dict(host="192.168.148.162",      port=6443, path="/readyz",                     sni="api.helios.orange.tn", hosthdr="api.helios.orange.tn"),
}
DEFAULT_SET = ["web", "api_app", "kube_api", "lb160_web", "lb162_web", "lb160_api", "lb162_api"]

CONNECT_TIMEOUT = 3.0   # par adresse (comme un navigateur qui passe à l'adresse suivante)
READ_TIMEOUT = 5.0
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

_lock = threading.Lock()


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"



# --- Résolution DNS « comme un client VM » (2e point de vue, indépendant de l'hôte Windows) -------------------
DNS_SERVERS = []          # ex. ["192.168.148.160", "192.168.148.162"] ; vide = résolution du système d'exploitation
_DNS_CUR = [0]            # serveur courant « collant » (comme systemd-resolved : on ne change qu'en cas d'échec)
_IP_RE = re.compile(r"^\d+\.\d+\.\d+\.\d+$")


def _dns_query(name, server, timeout=2.0):
    qid = random.randint(0, 65535)
    q = struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0)
    q += b"".join(bytes([len(p)]) + p.encode() for p in name.split(".")) + b"\x00" + struct.pack(">HH", 1, 1)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        s.sendto(q, (server, 53))
        data, _ = s.recvfrom(1024)
    finally:
        s.close()
    if len(data) < 12 or struct.unpack(">H", data[:2])[0] != qid:
        raise OSError("réponse DNS invalide")
    an = struct.unpack(">H", data[6:8])[0]
    i = 12
    while data[i] != 0:                      # saute le nom de la question
        i += data[i] + 1
    i += 5
    ips, ttls = [], []
    for _ in range(an):
        if data[i] & 0xC0 == 0xC0:
            i += 2
        else:
            while data[i] != 0:
                i += data[i] + 1
            i += 1
        rtype, _cls, ttl, rdl = struct.unpack(">HHIH", data[i:i + 10])
        i += 10
        if rtype == 1 and rdl == 4:
            ips.append(".".join(str(b) for b in data[i:i + 4]))
            ttls.append(ttl)
        i += rdl
    return ips, (min(ttls) if ttls else 0)


DNS_CACHE = [False]      # --dns-cache : respecte le TTL des réponses, comme systemd-resolved sur les VM (cache par nom)
_CACHE = {}              # nom -> (expiration epoch, [ips])


def resolve(name):
    """Liste d'adresses IPv4 pour `name` ; en mode DNS : serveur courant d'abord, bascule sur l'autre en cas d'échec.
    Avec --dns-cache, une réponse reste valable pendant son TTL (l'effet « client qui garde un LB mort en cache »)."""
    if not DNS_SERVERS or _IP_RE.match(name):
        return [name]
    if DNS_CACHE[0]:
        hit = _CACHE.get(name)
        if hit and time.time() < hit[0]:
            return list(hit[1])
    n = len(DNS_SERVERS)
    last = None
    for k in range(n):
        srv = DNS_SERVERS[(_DNS_CUR[0] + k) % n]
        try:
            ips, ttl = _dns_query(name, srv)
            if ips:
                _DNS_CUR[0] = (_DNS_CUR[0] + k) % n
                if DNS_CACHE[0] and ttl > 0:
                    _CACHE[name] = (time.time() + ttl, list(ips))
                return ips
        except Exception as e:  # noqa: BLE001
            last = e
    raise OSError("résolution DNS impossible pour %s (%s)" % (name, last))


def connect_any(host, port, timeout):
    """Connexion TCP en essayant chaque adresse dans l'ordre (comme un navigateur/curl)."""
    if not DNS_SERVERS:
        return socket.create_connection((host, port), timeout=timeout)
    last = None
    for ip in resolve(host):
        try:
            return socket.create_connection((ip, port), timeout=timeout)
        except OSError as e:
            last = e
    raise last or OSError("aucune adresse")


def one_request(t):
    """Retourne (ok, status, ip, err). Itère sur toutes les adresses du nom, comme curl/un navigateur."""
    sock = None
    try:
        sock = connect_any(t["host"], t["port"], CONNECT_TIMEOUT)
        ip = sock.getpeername()[0]
        sock.settimeout(READ_TIMEOUT)
        s = CTX.wrap_socket(sock, server_hostname=t["sni"])
        req = ("GET %s HTTP/1.1\r\nHost: %s\r\nUser-Agent: ha01-probe\r\nAccept: */*\r\nConnection: close\r\n\r\n"
               % (t["path"], t["hosthdr"])).encode()
        s.sendall(req)
        buf = b""
        while len(buf) < 65536:
            chunk = s.recv(8192)
            if not chunk:
                break
            buf += chunk
            if b"\r\n" in buf and len(buf) > 12 and b"\r\n\r\n" in buf:
                break
        line = buf.split(b"\r\n", 1)[0].decode("latin1", "replace")
        parts = line.split(" ")
        status = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        s.close()
        return (status == 200), status, ip, None if status == 200 else "HTTP %s" % (line[:60],)
    except Exception as e:  # noqa: BLE001 - on veut tout journaliser
        ip = None
        try:
            if sock is not None:
                ip = sock.getpeername()[0]
        except Exception:  # noqa: BLE001
            pass
        return False, 0, ip, "%s: %s" % (type(e).__name__, str(e)[:80])
    finally:
        try:
            if sock is not None:
                sock.close()
        except Exception:  # noqa: BLE001
            pass


def worker(name, out, stop_evt, interval):
    t = TARGETS[name]
    nxt = time.time()
    while not stop_evt.is_set():
        t0 = time.time()
        ok, status, ip, err = one_request(t)
        ms = int((time.time() - t0) * 1000)
        rec = {"ts": now_iso(), "probe": name, "ok": ok, "status": status, "ms": ms, "ip": ip, "err": err}
        with _lock:
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            out.flush()
        nxt += interval
        d = nxt - time.time()
        if d > 0:
            stop_evt.wait(d)
        else:
            nxt = time.time()


def main():
    ap = argparse.ArgumentParser(description="HA-01 availability prober")
    ap.add_argument("--out", required=True, help="fichier JSONL de sortie (append)")
    ap.add_argument("--probes", default=",".join(DEFAULT_SET), help="liste séparée par des virgules")
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--duration", type=float, default=0, help="secondes (0 = jusqu'à STOP/Ctrl+C)")
    ap.add_argument("--dns-servers", default="", help="ex. 192.168.148.160,192.168.148.162 : résout les noms via ces serveurs (point de vue d'une VM) au lieu du système")
    ap.add_argument("--dns-cache", action="store_true", help="avec --dns-servers : respecte le TTL des réponses (cache comme systemd-resolved)")
    a = ap.parse_args()
    if a.dns_servers:
        DNS_SERVERS.extend([x.strip() for x in a.dns_servers.split(',') if x.strip()])
    DNS_CACHE[0] = bool(a.dns_cache and a.dns_servers)
    names = [n.strip() for n in a.probes.split(",") if n.strip()]
    for n in names:
        if n not in TARGETS:
            sys.exit("sonde inconnue: %s" % n)
    stop_file = os.path.join(os.path.dirname(os.path.abspath(a.out)), "STOP-" + os.path.basename(a.out))
    if os.path.exists(stop_file):
        os.remove(stop_file)
    stop_evt = threading.Event()
    with open(a.out, "a", encoding="utf-8") as out:
        out.write(json.dumps({"ts": now_iso(), "probe": "_meta", "event": "start", "probes": names, "pid": os.getpid()}) + "\n")
        out.flush()
        ths = [threading.Thread(target=worker, args=(n, out, stop_evt, a.interval), daemon=True) for n in names]
        for th in ths:
            th.start()
        t_end = time.time() + a.duration if a.duration else None
        try:
            while not stop_evt.is_set():
                time.sleep(1)
                if t_end and time.time() >= t_end:
                    break
                if os.path.exists(stop_file):
                    break
        except KeyboardInterrupt:
            pass
        stop_evt.set()
        for th in ths:
            th.join(timeout=10)
        out.write(json.dumps({"ts": now_iso(), "probe": "_meta", "event": "stop"}) + "\n")
        out.flush()
    if os.path.exists(stop_file):
        os.remove(stop_file)


if __name__ == "__main__":
    main()
