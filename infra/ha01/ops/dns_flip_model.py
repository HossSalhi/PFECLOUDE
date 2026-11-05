#!/usr/bin/env python3
"""
HA-01 (4e scénario, perte de .162) — MODÈLE HORS LIGNE de la bascule DNS .162 -> .160. Aucune interaction avec le cluster : il importe le VRAI contrôleur
(`dns/helios-dns-health.py`, identique à celui déployé : même empreinte) et rejoue sa boucle avec une horloge simulée.

Ce que le modèle établit (à confirmer par la mesure de l'essai) :
  1. délai de bascule du contrôleur survivant (.160) après la mort brutale de .162 : le sondage de .162 expire (TIMEOUT 2 s) au lieu d'échouer tout de suite,
     FALL échecs consécutifs sont nécessaires, un cycle dure PERIOD (3 s) -> écriture du hostsdir ~8 à 11 s après la mort ;
  2. coupure vue par un client selon sa façon de résoudre :
       - sans cache (résout à chaque connexion)            : bascule + <= 1 s ;
       - cache à TTL (Windows, systemd-resolved, CoreDNS)  : la réponse périmée (.162 seul, TTL 30 s) reste valide jusqu'à 30 s ; si elle expire AVANT la bascule,
                                                              le client reçoit encore .162 avec un TTL neuf -> jusqu'à ~41 s au pire ;
       - fichier hosts statique (2 IP, .160 d'abord)       : 0 s (le client essaie .160 en premier).

  python dns_flip_model.py [--n 20000] [--json]
"""
import argparse
import importlib.util
import json
import os
import random
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("HELIOS_DNS_ORDER", "162,160")            # ordre déployé (écart E12)
_spec = importlib.util.spec_from_file_location("hdh", os.path.join(HERE, "..", "dns", "helios-dns-health.py"))
hdh = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hdh)

TTL = 30.0              # dnsmasq `local-ttl=30` (réponse mesurée : TTL 30 constant)
PROBE_OK_S = 0.03       # sondage d'un LB sain (web + TLS + API) : quelques dizaines de ms
INOTIFY_S = 0.1         # dnsmasq relit le hostsdir presque aussitôt


def flip_delay(phase, dead="162"):
    """Secondes entre la mort de `dead` (t=0) et l'écriture du hostsdir par le contrôleur survivant. `phase` = début du 1er cycle qui trouve `dead` mort."""
    st = {k: hdh.LbState() for k in hdh.ORDER}
    for _ in range(10):
        for k in st:
            st[k].feed(True, "ok", -100.0)
    k = 0
    while True:
        start = phase + hdh.PERIOD * k
        for lb in hdh.ORDER:                                   # même ordre que la boucle réelle (.162 d'abord)
            st[lb].feed(lb != dead, "x", start)
        sel = hdh.decide(st, start)
        n_dead_probes = 1                                      # seul `dead` est sondé en échec (expiration) dans ce cycle
        end = start + hdh.TIMEOUT * n_dead_probes + PROBE_OK_S * (len(hdh.ORDER) - n_dead_probes) + INOTIFY_S
        if dead not in sel:
            return end
        k += 1


def client_outage_ttl(flip, age):
    """Coupure d'un client à cache TTL dont la réponse en cache avait l'âge `age` (0..TTL) à la mort (t=0)."""
    expiry = TTL - age                                          # prochain renouvellement, relatif à la mort
    if expiry >= flip:
        return expiry                                           # il renouvelle APRÈS la bascule : reçoit .160
    return expiry + TTL                                         # renouvelle AVANT : reçoit encore .162 (TTL neuf), recommence 30 s plus tard


def pct(v, p):
    v = sorted(v)
    return v[min(len(v) - 1, int(p * (len(v) - 1)))]


def what_if(n=8000):
    """Options d'amélioration CHIFFRÉES (modèle seul, rien n'est appliqué) : TTL des réponses, période/expiration des sondes, FALL. Retourne une liste de lignes."""
    global TTL
    keep = (TTL, hdh.PERIOD, hdh.TIMEOUT, hdh.FALL)
    rows = []
    for lab, ttl, per, to, fall in (("ACTUEL  (TTL 30 s, période 3 s, sonde 2 s, FALL 3)", 30.0, 3.0, 2.0, 3),
                                    ("TTL 10 s seul", 10.0, 3.0, 2.0, 3),
                                    ("TTL 5 s + période 1 s + sonde 1 s + FALL 3", 5.0, 1.0, 1.0, 3),
                                    ("TTL 5 s + période 1 s + sonde 1 s + FALL 2", 5.0, 1.0, 1.0, 2)):
        TTL, hdh.PERIOD, hdh.TIMEOUT, hdh.FALL = ttl, per, to, fall
        r = run(n)
        rows.append((lab, r["bascule_controleur_s"], r["client_sans_cache_s"], r["client_cache_TTL_s"], r["part_des_clients_cache_au_dela_de_10s_pct"]))
    TTL, hdh.PERIOD, hdh.TIMEOUT, hdh.FALL = keep
    return rows


def run(n):
    rnd = random.Random(1)
    flips = [flip_delay(rnd.uniform(0, hdh.PERIOD)) for _ in range(n)]
    sample = [(f, rnd.uniform(0, TTL)) for f in flips]
    nocache = [f + rnd.uniform(0, 1.0) for f in flips]
    cache = [client_outage_ttl(f, a) for f, a in sample]
    res = {
        "constantes": {"ORDER": hdh.ORDER, "FALL": hdh.FALL, "PERIOD_s": hdh.PERIOD, "TIMEOUT_sonde_s": hdh.TIMEOUT, "RISE": hdh.RISE, "HOLD_DOWN_s": hdh.HOLD_DOWN_S, "TTL_dnsmasq_s": TTL},
        "bascule_controleur_s": {"min": round(min(flips), 1), "median": round(statistics.median(flips), 1), "p95": round(pct(flips, .95), 1), "max": round(max(flips), 1)},
        "client_sans_cache_s": {"min": round(min(nocache), 1), "median": round(statistics.median(nocache), 1), "p95": round(pct(nocache, .95), 1), "max": round(max(nocache), 1)},
        "client_cache_TTL_s": {"min": round(min(cache), 1), "median": round(statistics.median(cache), 1), "p95": round(pct(cache, .95), 1), "max": round(max(cache), 1)},
        "client_hosts_statique_s": {"min": 0.0, "median": 0.0, "p95": 0.0, "max": 0.0},
        "part_des_clients_cache_au_dela_de_30s_pct": round(100.0 * sum(1 for x in cache if x > 30) / len(cache), 1),
        "part_des_clients_cache_au_dela_de_10s_pct": round(100.0 * sum(1 for x in cache if x > 10) / len(cache), 1),
        "echantillons": n,
    }
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20000)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--what-if", action="store_true", help="chiffre des réglages alternatifs (rien n'est appliqué au cluster)")
    a = ap.parse_args()
    if a.what_if:
        print("OPTIONS CHIFFRÉES par le modèle (coupure d'un client, secondes ; rien n'est appliqué)")
        print("  %-52s | bascule médiane | sans cache méd./max | cache TTL méd./p95/max | cache > 10 s" % "réglage")
        for lab, f, nc, c, p10 in what_if():
            print("  %-52s | %6.1f s        | %5.1f / %5.1f s       | %5.1f / %5.1f / %5.1f s  | %4.1f %%" % (lab, f["median"], nc["median"], nc["max"], c["median"], c["p95"], c["max"], p10))
        return 0
    r = run(a.n)
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=1))
        return 0
    print("MODÈLE de la bascule DNS .162 -> .160 (%d tirages ; contrôleur réel importé)" % a.n)
    print("  constantes :", r["constantes"])
    for k, lab in (("bascule_controleur_s", "écriture du hostsdir par le contrôleur survivant"), ("client_sans_cache_s", "client SANS cache (résout à chaque connexion)"),
                   ("client_cache_TTL_s", "client à CACHE TTL 30 s (Windows / resolved / CoreDNS)"), ("client_hosts_statique_s", "client à fichier hosts statique (.160 d'abord)")):
        v = r[k]
        print("  %-62s min %5.1f | médiane %5.1f | p95 %5.1f | max %5.1f s" % (lab, v["min"], v["median"], v["p95"], v["max"]))
    print("  clients à cache coupés plus de 10 s : %.1f %% ; plus de 30 s : %.1f %%" % (r["part_des_clients_cache_au_dela_de_10s_pct"], r["part_des_clients_cache_au_dela_de_30s_pct"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
