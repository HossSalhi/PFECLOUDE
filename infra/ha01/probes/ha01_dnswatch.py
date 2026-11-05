#!/usr/bin/env python3
"""
HA-01 (4e scénario, perte de .162) — sonde de BASCULE DNS, autonome (aucune dépendance), utilisable sur Windows ET sur un nœud Linux.

  --mode os      : résout `--name` par le RÉSOLVEUR DU SYSTÈME (sur Windows : cache DNS Windows + NRPT + fichier hosts ; sur Linux : glibc, /etc/resolv.conf),
                   puis tente une connexion TCP vers la PREMIÈRE adresse renvoyée (comme la plupart des clients). C'est ce que vit réellement un client.
                   Ligne : {ts, mode, name, ips, resolve_ms, target, tcp_ok, tcp_ms, err}
  --mode direct  : interroge directement chaque dnsmasq (--servers 160,162) à haute cadence, UN FIL PAR SERVEUR (un serveur mort qui expire ne ralentit pas l'autre),
                   pour dater au dixième de seconde l'instant où le contrôleur survivant cesse de publier le LB mort.
                   Ligne : {ts, mode, server, name, ips, ttl, ms, err}

  python ha01_dnswatch.py --mode os --name api.helios.orange.tn --port 6443 --period 0.5 --duration 1500 --out dnswatch-windows.jsonl
  python ha01_dnswatch.py --mode direct --servers 160,162 --names helios.orange.tn,api.helios.orange.tn --period 0.2 --duration 1500 --out dnswatch-direct.jsonl

Lecture seule : ne modifie rien (requêtes DNS et connexions TCP sans envoi de données). Se termine seule après --duration.
"""
import argparse
import json
import random
import socket
import struct
import sys
import threading
import time
from datetime import datetime, timezone

NET = "192.168.148."
_lock = threading.Lock()


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def dns_query(name, server_ip, timeout=1.0):
    """Requête A directe (UDP/53). Retourne (liste d'IP, TTL minimal). Lève une exception si pas de réponse."""
    qid = random.randint(0, 65535)
    q = struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0)
    q += b"".join(bytes([len(p)]) + p.encode() for p in name.split(".")) + b"\x00" + struct.pack(">HH", 1, 1)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        s.sendto(q, (server_ip, 53))
        data, _ = s.recvfrom(1024)
    finally:
        s.close()
    if len(data) < 12 or struct.unpack(">H", data[:2])[0] != qid:
        raise OSError("réponse DNS invalide")
    an = struct.unpack(">H", data[6:8])[0]
    i = 12
    while data[i] != 0:                      # saute le nom de la question
        i += data[i] + 1
    i += 5                                   # \0 + QTYPE + QCLASS
    ips, ttls = [], []
    for _ in range(an):
        if data[i] & 0xC0 == 0xC0:
            i += 2
        else:
            while data[i] != 0:
                i += data[i] + 1
            i += 1
        rtype, _cls, ttl, rdlen = struct.unpack(">HHIH", data[i:i + 10])
        i += 10
        if rtype == 1 and rdlen == 4:
            ips.append(".".join(str(b) for b in data[i:i + 4]))
            ttls.append(ttl)
        i += rdlen
    return ips, (min(ttls) if ttls else None)


def write(fh, rec):
    with _lock:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        fh.flush()


def loop_os(a, fh, stop_at):
    while time.time() < stop_at:
        t0 = time.time()
        rec = {"ts": now_iso(), "mode": "os", "name": a.name}
        try:
            infos = socket.getaddrinfo(a.name, a.port, socket.AF_INET, socket.SOCK_STREAM)
            ips = []
            for inf in infos:
                if inf[4][0] not in ips:
                    ips.append(inf[4][0])
            rec["ips"] = ips
            rec["resolve_ms"] = int((time.time() - t0) * 1000)
            rec["target"] = ips[0] if ips else None
            t1 = time.time()
            try:
                s = socket.create_connection((ips[0], a.port), timeout=a.connect_timeout)
                s.close()
                rec["tcp_ok"] = True
            except OSError as e:
                rec["tcp_ok"] = False
                rec["err"] = type(e).__name__
            rec["tcp_ms"] = int((time.time() - t1) * 1000)
        except OSError as e:
            rec["ips"] = None
            rec["resolve_ms"] = int((time.time() - t0) * 1000)
            rec["tcp_ok"] = False
            rec["err"] = "resolve:" + type(e).__name__
        write(fh, rec)
        time.sleep(max(0.0, a.period - (time.time() - t0)))


def loop_direct(a, fh, server, stop_at):
    names = [n for n in a.names.split(",") if n]
    while time.time() < stop_at:
        t0 = time.time()
        for n in names:
            t1 = time.time()
            rec = {"ts": now_iso(), "mode": "direct", "server": server, "name": n}
            try:
                ips, ttl = dns_query(n, NET + server, timeout=a.dns_timeout)
                rec["ips"] = sorted(i.split(".")[-1] for i in ips)
                rec["ttl"] = ttl
            except Exception as e:  # noqa: BLE001
                rec["ips"] = None
                rec["err"] = type(e).__name__
            rec["ms"] = int((time.time() - t1) * 1000)
            write(fh, rec)
        time.sleep(max(0.0, a.period - (time.time() - t0)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["os", "direct"], required=True)
    ap.add_argument("--name", default="api.helios.orange.tn")
    ap.add_argument("--port", type=int, default=6443)
    ap.add_argument("--names", default="helios.orange.tn,api.helios.orange.tn")
    ap.add_argument("--servers", default="160,162")
    ap.add_argument("--period", type=float, default=0.5)
    ap.add_argument("--duration", type=float, default=600)
    ap.add_argument("--connect-timeout", type=float, default=1.0)
    ap.add_argument("--dns-timeout", type=float, default=1.0)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    stop_at = time.time() + a.duration
    with open(a.out, "a", encoding="utf-8") as fh:
        if a.mode == "os":
            loop_os(a, fh, stop_at)
        else:
            ths = [threading.Thread(target=loop_direct, args=(a, fh, s, stop_at), daemon=True) for s in a.servers.split(",") if s]
            for t in ths:
                t.start()
            for t in ths:
                t.join()
    return 0


if __name__ == "__main__":
    sys.exit(main())
