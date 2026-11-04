#!/usr/bin/env python3
"""
HA-01 Phase 2b — choix du LB « ACTIF » du DNS à santé contrôlée : l'IP que le contrôleur publie SEULE tant que son LB est sain.

  python p2b_primary.py --primary 162 [--observe 120] [--dry-run]       # recommandé (écart E12)
  python p2b_primary.py --primary 160                                    # retour à l'ordre historique (D3)

Pourquoi .162 : les scénarios HA-01 perdent .160 (jamais .162). Mesuré le 4 nov. avec .160 en tête (arrêt de HAProxy sur .160, DNS en enforce) :
le contrôleur bascule en 7 s mais un client DNS est coupé 8 s (sans cache), 17 s (cache à TTL) à 25 s (cache DNS de Windows) -> seuils du plan
(série <= 10 s, >= 99 %) non tenus. Avec .162 en tête, la perte de .160 ne change AUCUNE réponse DNS.
Déploiement (un LB à la fois, .162 puis .160) : sauvegarde du script et de l'unité dans /etc/helios-dns-health/backup, installation du script du dépôt
(ordre lu dans HELIOS_DNS_ORDER) et de l'unité (Environment=HELIOS_DNS_ORDER=...), autotest, redémarrage du contrôleur, vérification (mode enforce,
publication = l'IP choisie seule, dnsmasq du LB répond cette IP). Observation --observe s : aucune sonde par nom en échec ; sinon RETOUR ARRIÈRE
AUTOMATIQUE (ordre précédent). Aucun client n'est coupé : les deux LB restent sains et servent pendant tout le changement.
Code retour : 0 OK · 1 échec (retour arrière effectué) · 3 préflight KO (rien fait).
"""
import argparse
import os
import sys
import time

import ha01_ops as O

DNS_DIR = os.path.normpath(os.path.join(O.HERE, "..", "dns"))
UNIT = "/etc/systemd/system/helios-dns-health.service"
BIN = "/usr/local/bin/helios-dns-health.py"
BK = "/etc/helios-dns-health/backup"


def order_for(primary):
    return "162,160" if primary == "162" else "160,162"


def unit_text(primary):
    txt = open(os.path.join(DNS_DIR, "helios-dns-health.service"), encoding="utf-8").read()
    out = []
    for l in txt.splitlines():
        out.append("Environment=HELIOS_DNS_ORDER=%s" % order_for(primary) if l.startswith("Environment=HELIOS_DNS_ORDER=") else l)
    return "\n".join(out) + "\n"


def deploy(lb, primary, say):
    ts = O.now().strftime("%Y%m%dT%H%M%SZ")
    script = open(os.path.join(DNS_DIR, "helios-dns-health.py"), encoding="utf-8").read()
    rc, out = O.ssh(lb, "sudo -n mkdir -p %s && sudo -n cp -p %s %s/helios-dns-health.py.bak-ha01-%s && sudo -n cp -p %s %s/helios-dns-health.service.bak-ha01-%s && echo ok"
                    % (BK, BIN, BK, ts, UNIT, BK, ts), timeout=40)
    if not out.endswith("ok"):
        say("sauvegarde impossible sur .%s: %s" % (lb, out[:100]))
        return False
    rc, out = O.ssh(lb, "sudo -n tee %s >/dev/null && sudo -n chmod 755 %s && sudo -n python3 %s --selftest" % (BIN, BIN, BIN), timeout=60, stdin=script)
    if "selftest OK" not in out:
        say("autotest du contrôleur KO sur .%s: %s" % (lb, out[:160]))
        return False
    rc, out = O.ssh(lb, "sudo -n tee %s >/dev/null && sudo -n systemctl daemon-reload && sudo -n systemctl restart helios-dns-health; sleep 8; systemctl is-active helios-dns-health"
                    % UNIT, timeout=60, stdin=unit_text(primary))
    say("déploiement .%s (ordre %s) : service %s" % (lb, order_for(primary), out.strip().splitlines()[-1:] or out))
    return out.strip().endswith("active")


def verify(lb, primary):
    s = O.dns_state(lb)
    want = [O.NET + primary]
    ans = O.dns_answers(lb)
    ok = (s.get("mode") == "enforce" and s.get("publish") == want and all(v[0] == want for v in ans.values())
          and {k: v.get("status") for k, v in (s.get("lbs") or {}).items()} == {"160": "UP", "162": "UP"})
    return ok, {"mode": s.get("mode"), "publish": s.get("publish"), "dnsmasq": {k: v[0] for k, v in ans.items()}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--primary", required=True, choices=["160", "162"])
    ap.add_argument("--observe", type=int, default=120)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    prev = "160" if a.primary == "162" else "162"
    run = O.Run("p2b-primary%s" % a.primary)
    say = run.say
    chk = []

    def add(name, ok, detail):
        chk.append({"check": name, "ok": bool(ok), "detail": detail})
        say("PREFLIGHT %-72s %s  %s" % (name[:72], "OK " if ok else "KO ", str(detail)[:105]))
    ns = O.nodes_state()
    add("3 nœuds Ready, etcd 5/5", len(ns) == 3 and all(v == "Ready" for v in ns.values()) and O.etcd_healthy() == 5, ns)
    add("harnais actif", O.file_age(O.PROBE_WIN) < 15 and O.file_age(O.SAMPLES) < 60, "%.0fs/%.0fs" % (O.file_age(O.PROBE_WIN), O.file_age(O.SAMPLES)))
    for lb in ("162", "160"):
        ok, d = O.lb_direct_ok(lb)
        up, bad = O.haproxy_all_up(lb)
        add("LB .%s sain (direct + serveurs UP)" % lb, ok and up, "%s %s" % (d, bad))
        s = O.dns_state(lb)
        add("contrôleur DNS .%s : enforce, les 2 LB UP, publie l'IP de .%s seule" % (lb, prev),
            s.get("mode") == "enforce" and s.get("publish") == [O.NET + prev] and {k: v.get("status") for k, v in (s.get("lbs") or {}).items()} == {"160": "UP", "162": "UP"},
            {"mode": s.get("mode"), "publish": s.get("publish")})
    ws = O.ha01_hoststall.windows(time.time() - 300, time.time())
    add("aucun gel d'E/S de l'hôte dans les 5 dernières minutes", ws == [], O.ha01_hoststall.describe(ws))
    run.save("preflight.json", chk)
    if not all(c["ok"] for c in chk):
        say("STOP: préconditions non satisfaites — aucune action effectuée.")
        return 3
    if a.dry_run:
        say("dry-run: préconditions OK, rien n'est déployé.")
        return 0

    t0 = time.time()
    done = []
    ok_all = True
    for lb in ("162", "160"):                 # le LB visé en premier, puis l'autre
        if not deploy(lb, a.primary, say):
            ok_all = False
            break
        done.append(lb)
        ok, d = verify(lb, a.primary)
        say("VÉRIF .%s : %s %s" % (lb, "OK " if ok else "KO ", d))
        if not ok:
            ok_all = False
            break
        time.sleep(5)
    bad = None
    if ok_all:
        say("observation %d s : sondes par nom (Windows, .161)" % a.observe)
        t_obs = time.time()
        while time.time() - t_obs < a.observe:
            runs = {p: O.tail_fail_run(O.PROBE_WIN, p) for p in O.BY_NAME}
            if max(runs.values()) >= 15:
                bad = "sonde par nom en échec depuis %ss" % max(runs.values())
                break
            time.sleep(5)
        t1 = time.time()
        stalls = list(O.ha01_hoststall.windows(t0 - 30, t1) or [])
        rows = []
        for nm, recs in (("Windows", O.window_records_local(O.PROBE_WIN, t0, t1)), (".161", O.window_records_remote("161", O.P161_FILE, t0, t1))):
            an = O.analyse_vantage(recs, stalls)
            for p in O.BY_NAME:
                v = an["hors"].get(p)
                rows.append(("[%s] %s >= 99,5 %% et série <= 10 s pendant le changement (hors gels)" % (nm, p), bool(v) and v["availability_pct"] >= 99.5 and v["longest_failure_s"] <= 10,
                             "hors gel: %s | BRUT: %s" % (O.fmt_probe(an["hors"], p), O.fmt_probe(an["raw"], p))))
        for lb in ("162", "160"):
            ok, d = verify(lb, a.primary)
            rows.append(("contrôleur .%s : enforce, publie .%s seule, dnsmasq cohérent" % (lb, a.primary), ok, d))
        rows.append(("pas d'abandon", bad is None, bad))
        O.write_report(run, "LB actif du DNS : .%s" % a.primary, rows, "Gels d'E/S hôte : %s\n" % O.ha01_hoststall.describe(stalls), {"primary": a.primary})
        ok_all = all(ok for _, ok, _ in rows)
        for c, ok, v in rows:
            say("  %s %-80s %s" % ("OK" if ok else "KO", c[:80], str(v)[:110]))
    if not ok_all:
        say("ÉCHEC -> RETOUR ARRIÈRE vers .%s en tête" % prev)
        for lb in ("162", "160"):
            deploy(lb, prev, say)
        return 1
    say("TERMINÉ : le DNS publie .%s seule sur les 2 LB (ordre %s)." % (a.primary, order_for(a.primary)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
