#!/usr/bin/env python3
"""
HELIOS HA-01 — contrôle de santé des load-balancers et publication DNS (actif/passif).

Tourne sur CHAQUE LB (.160 et .162) et alimente le dnsmasq LOCAL via `hostsdir` : chaque LB décide donc
de manière indépendante, sans dépendre de l'autre (un checker central serait un SPOF).

Sondes par LB (toutes doivent réussir pour que le LB soit « sain ») :
  web  GET http://IP:80/   Host: helios.orange.tn      -> 200/301/302/404
  tls  TCP :443 + poignée de main TLS (SNI helios.orange.tn)
  api  GET https://IP:6443/readyz (SNI api.helios.orange.tn) -> 200

Règle de publication (helios.orange.tn ET api.helios.orange.tn) :
  primaire sain            -> [primaire]            (actif)
  sinon secondaire sain    -> [secondaire]          (passif qui prend le relais)
  sinon                    -> [primaire, secondaire] (FAIL-OPEN : jamais zéro enregistrement)
Hystérésis : FALL échecs consécutifs => DOWN ; RISE succès consécutifs ET HOLD_DOWN_S écoulées => de nouveau éligible.

Modes (fichier /etc/helios-dns-health/mode, relu à chaque cycle) :
  shadow  (défaut) : journalise la décision, n'écrit RIEN (dnsmasq garde ses host-record statiques)
  enforce          : écrit /etc/dnsmasq-helios.d/helios.hosts (dnsmasq recharge seul via inotify)

  python3 helios-dns-health.py            # service
  python3 helios-dns-health.py --once     # une passe de sondes, affichage, sortie
  python3 helios-dns-health.py --selftest # vérifie hystérésis et fail-open sans réseau
"""
import json
import os
import socket
import ssl
import sys
import time

LBS = {"160": "192.168.148.160", "162": "192.168.148.162"}
# Primaire (LB « actif » publié seul tant qu'il est sain) d'abord. Configurable : HELIOS_DNS_ORDER=162,160 (écart E12 du 4 nov. 2026 :
# les scénarios HA-01 perdent .160, jamais .162 -> publier .162 en tête rend la perte de .160 invisible pour le DNS ; mesuré avec .160 en
# tête : coupure de 8 s (sans cache) à 25 s (cache DNS de Windows) à la perte de .160). Valeur invalide -> ordre historique.
ORDER = [x.strip() for x in os.environ.get("HELIOS_DNS_ORDER", "160,162").split(",") if x.strip() in LBS]
if sorted(ORDER) != sorted(LBS):
    ORDER = ["160", "162"]
NAMES = ["helios.orange.tn", "api.helios.orange.tn"]
HOSTS_FILE = os.environ.get("HELIOS_DNS_HOSTS", "/etc/dnsmasq-helios.d/helios.hosts")
STATE_FILE = os.environ.get("HELIOS_DNS_STATE", "/var/lib/helios-dns-health/state.json")
MODE_FILE = os.environ.get("HELIOS_DNS_MODE", "/etc/helios-dns-health/mode")
PERIOD = float(os.environ.get("HELIOS_DNS_PERIOD", "3"))
FALL = int(os.environ.get("HELIOS_DNS_FALL", "3"))
RISE = int(os.environ.get("HELIOS_DNS_RISE", "5"))
HOLD_DOWN_S = float(os.environ.get("HELIOS_DNS_HOLD", "60"))
TIMEOUT = float(os.environ.get("HELIOS_DNS_TIMEOUT", "2"))

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE


def log(msg):
    print(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) + " " + msg, flush=True)


def _http(ip, port, tls, sni, host, path):
    s = socket.create_connection((ip, port), timeout=TIMEOUT)
    try:
        s.settimeout(TIMEOUT)
        if tls:
            s = CTX.wrap_socket(s, server_hostname=sni)
        s.sendall(("GET %s HTTP/1.1\r\nHost: %s\r\nConnection: close\r\nUser-Agent: helios-dns-health\r\n\r\n" % (path, host)).encode())
        buf = s.recv(256)
        line = buf.split(b"\r\n", 1)[0].decode("latin1", "replace").split(" ")
        return int(line[1]) if len(line) > 1 and line[1].isdigit() else 0
    finally:
        try:
            s.close()
        except Exception:  # noqa: BLE001
            pass


def probe_lb(ip):
    """Retourne (sain, détail)."""
    try:
        st = _http(ip, 80, False, None, "helios.orange.tn", "/")
        if st not in (200, 301, 302, 404):
            return False, "web:%s" % st
    except Exception as e:  # noqa: BLE001
        return False, "web:%s" % type(e).__name__
    try:
        s = socket.create_connection((ip, 443), timeout=TIMEOUT)
        s.settimeout(TIMEOUT)
        t = CTX.wrap_socket(s, server_hostname="helios.orange.tn")
        t.close()
    except Exception as e:  # noqa: BLE001
        return False, "tls:%s" % type(e).__name__
    try:
        st = _http(ip, 6443, True, "api.helios.orange.tn", "api.helios.orange.tn", "/readyz")
        if st != 200:
            return False, "api:%s" % st
    except Exception as e:  # noqa: BLE001
        return False, "api:%s" % type(e).__name__
    return True, "ok"


class LbState:
    def __init__(self):
        self.status = None          # None (inconnu) | "UP" | "DOWN"
        self.ok = 0
        self.fail = 0
        self.last_down = None       # horodatage du dernier passage DOWN
        self.detail = ""

    def feed(self, healthy, detail, now):
        self.detail = detail
        if healthy:
            self.ok += 1
            self.fail = 0
        else:
            self.fail += 1
            self.ok = 0
        changed = None
        if self.status is None:
            self.status = "UP" if healthy else "DOWN"
            if not healthy:
                self.last_down = now
            changed = self.status
        elif self.status == "UP" and self.fail >= FALL:
            self.status = "DOWN"
            self.last_down = now
            changed = "DOWN"
        elif self.status == "DOWN" and self.ok >= RISE:
            self.status = "UP"
            changed = "UP"
        return changed

    def eligible(self, now):
        if self.status != "UP":
            return False
        return self.last_down is None or (now - self.last_down) >= HOLD_DOWN_S


def decide(states, now):
    prim, sec = ORDER[0], ORDER[1]
    if states[prim].eligible(now):
        return [prim]
    if states[sec].eligible(now):
        return [sec]
    return [prim, sec]           # fail-open


def render(selected):
    ips = [LBS[k] for k in selected]
    return "# géré par helios-dns-health (mode enforce) — ne pas éditer à la main\n" + \
           "\n".join("%s %s" % (ip, " ".join(NAMES)) for ip in ips) + "\n"


def write_atomic(path, content):
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    try:
        if open(path, encoding="utf-8").read() == content:
            return False
    except FileNotFoundError:
        pass
    tmp = os.path.join(d, "." + os.path.basename(path) + ".tmp")   # préfixe '.' : ignoré par dnsmasq
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(content)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    return True


def read_mode():
    try:
        m = open(MODE_FILE, encoding="utf-8").read().strip().lower()
        return m if m in ("shadow", "enforce") else "shadow"
    except FileNotFoundError:
        return "shadow"


def selftest():
    """Simule des séquences de sondes (sans réseau) et vérifie les invariants."""
    global FALL, RISE, HOLD_DOWN_S
    FALL, RISE, HOLD_DOWN_S = 3, 5, 60
    st = {k: LbState() for k in ORDER}
    t = 1000.0
    out = []

    def step(h160, h162, dt=3.0):
        nonlocal t
        t += dt
        st["160"].feed(h160, "x", t)
        st["162"].feed(h162, "x", t)
        return decide(st, t)

    for _ in range(3):
        step(True, True)
    assert step(True, True) == ["160"], "actif nominal = primaire seul"
    r = [step(False, True) for _ in range(2)]
    assert r == [["160"], ["160"]], "2 échecs < FALL : pas de bascule (hystérésis)"
    assert step(False, True) == ["162"], "3e échec : bascule vers le secondaire"
    for _ in range(6):
        r = step(True, True)
    assert r == ["162"], "primaire revenu mais HOLD_DOWN non écoulé : le secondaire reste"
    r = step(True, True, dt=61)
    assert r == ["160"], "HOLD_DOWN écoulé : retour au primaire"
    for _ in range(3):
        r = step(False, False)
    assert sorted(r) == ["160", "162"], "tous KO : fail-open (jamais zéro enregistrement)"
    assert render(["162"]).count("192.168.148.162") == 1 and "helios.orange.tn" in render(["162"])
    print("selftest OK (hystérésis, bascule, hold-down, fail-open, rendu)")


def main():
    if "--selftest" in sys.argv:
        return selftest()
    states = {k: LbState() for k in ORDER}
    once = "--once" in sys.argv
    last_pub = None
    last_beat = 0
    log("démarrage mode=%s période=%ss fall=%s rise=%s hold=%ss" % (read_mode(), PERIOD, FALL, RISE, HOLD_DOWN_S))
    while True:
        t0 = time.time()
        now = t0
        for k in ORDER:
            healthy, detail = probe_lb(LBS[k])
            ch = states[k].feed(healthy, detail, now)
            if ch:
                log("LB .%s -> %s (%s)" % (k, ch, detail))
        sel = decide(states, now)
        mode = read_mode()
        if sel != last_pub:
            log("décision: publier %s (mode=%s)" % ([LBS[k] for k in sel], mode))
        if mode == "enforce":
            try:
                if write_atomic(HOSTS_FILE, render(sel)):
                    log("fichier DNS écrit: %s" % [LBS[k] for k in sel])
            except Exception as e:  # noqa: BLE001
                log("ERREUR écriture %s: %s (le dernier fichier valide est conservé)" % (HOSTS_FILE, e))
        last_pub = sel
        snap = {"ts": int(now), "mode": mode, "publish": [LBS[k] for k in sel],
                "lbs": {k: {"status": s.status, "ok": s.ok, "fail": s.fail, "detail": s.detail} for k, s in states.items()}}
        try:
            os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
            open(STATE_FILE, "w", encoding="utf-8").write(json.dumps(snap))
        except Exception:  # noqa: BLE001
            pass
        if once:
            print(json.dumps(snap, ensure_ascii=False, indent=1))
            return
        if now - last_beat >= 60:
            last_beat = now
            log("etat: %s" % json.dumps({k: (s.status, s.detail) for k, s in states.items()}))
        d = PERIOD - (time.time() - t0)
        if d > 0:
            time.sleep(d)


if __name__ == "__main__":
    sys.exit(main())
