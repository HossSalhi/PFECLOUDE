#!/usr/bin/env python3
"""
HA-01 Phase 4 (DIAGNOSTIC, aucune VM éteinte) — rejoue la fenêtre « un worker vient de mourir » pour le CHEMIN WEB.

Constat de l'arrêt réel de .155 (4 nov. 12:35Z, 20/27) : web ~96 %, séries d'échecs de 11 à 32 s. Pendant ~45 s (jusqu'au passage NotReady + mise à jour des
endpoints), les pods de la VM morte restent dans les Services ; chaque saut qui répartit sur ces pods peut envoyer une requête dans le vide :
HAProxy -> Traefik (contrôle de santé `GET /` qui traverse l'application) -> pod frontend -> nginx -> IP de cluster `backend` (kube-proxy).

Ce script REPRODUIT cette fenêtre SANS éteindre de VM, en rendant le worker « victime » muet pour le chemin web :
  - sur CHAQUE LB (.160, .162) : iptables OUTPUT DROP vers <victime>:8000,8443,8082 (Traefik de la victime injoignable, SYN sans réponse) ;
  - sur le SURVIVANT : iptables OUTPUT + FORWARD DROP vers le(s) bloc(s) /26 de pods de la victime (Traefik hostNetwork et pods du survivant
    n'atteignent plus les pods de la victime ; les connexions déjà établies vers eux se bloquent comme vers un pair mort).
Durée par défaut 45 s (= le temps réel avant que les endpoints du nœud mort soient retirés). Mesure : sondes web/api_app des vues Windows et .161,
journaux HAProxy (passages DOWN/UP des serveurs de Traefik). Critère indicatif : plus longue série d'échecs <= 10 s et <= 12 s d'échecs au total
sur web et api_app (= l'équivalent de « >= 98 % sur 600 s »).

Sécurité (dans cet ordre) : préflight complet (sinon RIEN n'est fait) · script de retrait installé sur les 3 machines · minuterie systemd SUR CHAQUE machine
(retire les règles même si ce script ou la session disparaît) · abandon automatique (sonde par nom en échec > 60 s, etcd < 5) · rétablissement dans `finally`
et vérification qu'aucune règle `ha01-pbh` ne subsiste.

  python p4_webpath_blackhole.py --label base [--victim 155] [--survivor 158] [--duration 45] [--dry-run]
Code retour : 0 exécuté · 2 abandon · 3 préflight KO (rien fait) · 4 filet non armé (rien fait).
"""
import argparse
import concurrent.futures as cf
import ipaddress
import json
import os
import re
import subprocess
import sys
import time

import ha01_ops as O

D = O.ha01_hoststall
UNIT = "ha01-pbh-undo"
UNDO_PATH = "/usr/local/sbin/ha01-pbh-undo.sh"
UNDO_SH = """#!/bin/sh
# HA-01 — retire les regles iptables du test « chemin web » (commentaire ha01-pbh). Idempotent.
for ch in OUTPUT FORWARD; do
  n=0
  while iptables -S "$ch" 2>/dev/null | grep -q 'ha01-pbh' && [ "$n" -lt 20 ]; do
    iptables -S "$ch" | grep 'ha01-pbh' | sed 's/^-A/-D/' | while read -r r; do iptables $r; done
    n=$((n+1))
  done
done
exit 0
"""
NODE_NAME = {"155": "k8s-worker2", "158": "k8s-worker1"}
LBS = ("160", "162")


def victim_cidrs(victim, say):
    """Blocs /26 des pods (hors hostNetwork) hébergés par la victime, d'après kubectl."""
    rc, out = O.kubectl("get pods -A -o wide --no-headers", timeout=60)
    ips = []
    for l in out.splitlines():
        f = l.split()
        if NODE_NAME[victim] in f and "Running" in f:
            for tok in f:
                if re.fullmatch(r"\d+\.\d+\.\d+\.\d+", tok) and not tok.startswith(O.NET):
                    ips.append(tok)
    nets = sorted({str(ipaddress.ip_network("%s/26" % ip, strict=False)) for ip in ips})
    say("pods de .%s : %d IP -> blocs %s" % (victim, len(ips), nets))
    return nets


def rules_for(host, victim, cidrs):
    """Commandes d'injection pour une machine (liste de commandes shell)."""
    if host in LBS:
        return ["sudo -n iptables -I OUTPUT 1 -d %s%s -p tcp -m multiport --dports 8000,8443,8082 -m comment --comment ha01-pbh -j DROP" % (O.NET, victim)]
    cmds = []
    for c in cidrs:
        cmds.append("sudo -n iptables -I OUTPUT 1 -d %s -m comment --comment ha01-pbh -j DROP" % c)
        cmds.append("sudo -n iptables -I FORWARD 1 -d %s -m comment --comment ha01-pbh -j DROP" % c)
    return cmds


def count_rules(host):
    rc, out = O.ssh(host, "sudo -n sh -c \"iptables -S OUTPUT; iptables -S FORWARD\" 2>/dev/null | grep -c ha01-pbh", timeout=25)
    try:
        return int(out.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return -1


def inject(host, victim, cidrs):
    cmd = " && ".join(rules_for(host, victim, cidrs)) + " && echo ok"
    rc, out = O.ssh(host, cmd, timeout=40)
    return host, rc == 0 and out.strip().endswith("ok")


def restore(host):
    rc, out = O.ssh(host, "sudo -n %s; echo done" % UNDO_PATH, timeout=40)
    return host, count_rules(host)


def haproxy_events(lb, t0, t1):
    a = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(t0))
    b = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(t1))
    rc, out = O.ssh(lb, "sudo -n journalctl -u haproxy --since '%s' --until '%s' --utc --no-pager -o short-iso 2>/dev/null | "
                        "grep -E '\\[(WARNING|ALERT)\\].*(Server ingress_|backend .ingress_)' | cut -c1-230" % (a, b), timeout=60)
    return [l for l in out.splitlines() if l.strip()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--victim", default="155", choices=sorted(NODE_NAME))
    ap.add_argument("--survivor", default="158", choices=sorted(NODE_NAME))
    ap.add_argument("--duration", type=int, default=45)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--load-rps", type=float, default=0, help="charge GET supplémentaire (web + api_app) vers --load-lb pendant l'injection : échantillon statistique")
    ap.add_argument("--load-lb", default="162", choices=["160", "162"])
    a = ap.parse_args()
    if a.victim == a.survivor:
        print("la victime et le survivant doivent différer")
        return 3
    run = O.Run("p4-pbh-%s" % a.label)
    say = run.say
    hosts = list(LBS) + [a.survivor]
    chk = []

    def add(name, ok, detail):
        chk.append({"check": name, "ok": bool(ok), "detail": detail})
        say("PREFLIGHT %-72s %s  %s" % (name[:72], "OK " if ok else "KO ", str(detail)[:105]))
    ns = O.nodes_state()
    add("3 nœuds Ready, etcd 5/5", len(ns) == 3 and all(v == "Ready" for v in ns.values()) and O.etcd_healthy() == 5, ns)
    st = O.cnpg_state()
    add("CNPG sain 2/2, aucun pod non sain", st.startswith("Cluster in healthy state") and st.endswith("|2") and not O.unhealthy_pods(), st)
    for dep in ("ha01-canary-w1", "ha01-canary-w2"):
        age = O.canary_ack_age(dep)
        add("canari %s écrit depuis < 30 s" % dep, age is not None and age < 30, None if age is None else round(age, 1))
    add("harnais actif", O.file_age(O.PROBE_WIN) < 15 and O.file_age(O.SAMPLES) < 60, "%.0fs/%.0fs" % (O.file_age(O.PROBE_WIN), O.file_age(O.SAMPLES)))
    h = O.host_avail_mb()
    add("RAM hôte >= 1500 Mo", h >= 1500, h)
    ws = D.windows(time.time() - 300, time.time())
    add("aucun gel d'E/S de l'hôte dans les 5 dernières minutes", ws == [], D.describe(ws))
    for lb in LBS:
        ok, d = O.lb_direct_ok(lb)
        up, bad = O.haproxy_all_up(lb)
        add("LB .%s sert en direct, serveurs UP" % lb, ok and up, "%s %s" % (d, bad))
    for hst in hosts:
        rc, out = O.ssh(hst, "sudo -n iptables -V", timeout=20)
        add(".%s : iptables disponible, aucune règle ha01-pbh résiduelle" % hst, rc == 0 and count_rules(hst) == 0, out[:40])
    cidrs = victim_cidrs(a.victim, say)
    add("blocs de pods de la victime identifiés", bool(cidrs), cidrs)
    run.save("preflight.json", chk)
    if not all(c["ok"] for c in chk):
        say("STOP: préconditions non satisfaites — aucune action.")
        return 3
    if a.dry_run:
        say("dry-run: préconditions OK — rien n'est injecté.")
        return 0

    # Calage sur le Bridge HELIOS : l'injection (~45 s + retrait) doit tenir entièrement entre deux cycles de détection (période 300 s).
    s2n = O.bridge_seconds_to_next_cycle()
    if s2n is not None and s2n < a.duration + 75:
        say("calage Bridge HELIOS : prochain cycle dans %.0f s -> attente %.0f s" % (s2n, s2n + 20))
        time.sleep(s2n + 20)
    for hst in hosts:
        rc, out = O.ssh(hst, "sudo -n tee %s >/dev/null && sudo -n chmod 755 %s && echo ok" % (UNDO_PATH, UNDO_PATH), timeout=30, stdin=UNDO_SH)
        if not (rc == 0 and out.endswith("ok")):
            say("STOP: script de retrait non installé sur .%s — AUCUNE injection." % hst)
            return 4
    for hst in hosts:
        if not O.deadman_arm(hst, UNIT, a.duration + 60, UNDO_PATH):
            say("STOP: minuterie de sécurité NON armée sur .%s — AUCUNE injection." % hst)
            for h2 in hosts:
                O.deadman_disarm(h2, UNIT)
            return 4
    say("minuteries de sécurité armées sur %s (retrait automatique dans %d s)" % (hosts, a.duration + 60))
    dets = D.LiveDetector([O.NET + s for s in O.ETCD_ALL], period=5.0)
    dets.start()
    abort, t_inj, t_end = None, None, None
    load_proc, load_out = None, os.path.join(run.dir, "load.jsonl")
    try:
        if a.load_rps > 0:
            load_proc = subprocess.Popen([sys.executable, os.path.join(O.PROBES, "ha01_load.py"), "--rps", str(a.load_rps), "--duration", str(a.duration + 6),
                                          "--out", load_out, "--ip", O.NET + a.load_lb], stdout=open(os.path.join(run.dir, "load-summary.txt"), "w"), stderr=subprocess.STDOUT)
            time.sleep(4)
        t_inj = time.time()
        with cf.ThreadPoolExecutor(max_workers=3) as ex:
            res = list(ex.map(lambda h2: inject(h2, a.victim, cidrs), hosts))
        say("INJECTION (victime .%s muette pour le chemin web) : %s" % (a.victim, res))
        if not all(ok for _, ok in res):
            abort = "injection incomplète"
        t_hold = time.time()
        while not abort and time.time() - t_hold < a.duration:
            runs = {p: O.tail_fail_run(O.PROBE_WIN, p) for p in O.BY_NAME}
            if max(runs.values()) > 60:
                abort = "sonde par nom en échec depuis %ss" % max(runs.values())
            elif O.host_avail_mb() < 600:
                abort = "RAM hôte < 600 Mo"
            time.sleep(3)
    finally:
        t_end = time.time()
        with cf.ThreadPoolExecutor(max_workers=3) as ex:
            left = list(ex.map(restore, hosts))
        say("RÉTABLISSEMENT : règles ha01-pbh restantes %s" % dict(left))
        if all(n == 0 for _, n in left):
            for h2 in hosts:
                O.deadman_disarm(h2, UNIT)
        else:
            say("ATTENTION : des règles subsistent — les minuteries RESTENT armées (retrait automatique)")
    if abort:
        say("ABANDON: %s" % abort)
    time.sleep(20)
    stalls = list(dets.windows()) + (D.windows(t_inj - 60, t_end + 60) or [])
    dets.stop()
    t_a, t_b = t_inj - 5, t_end + 25
    vant = {"Windows": O.window_records_local(O.PROBE_WIN, t_a, t_b),
            ".161": O.window_records_remote("161", O.P161_FILE, t_a, t_b)}
    ev = {lb: haproxy_events(lb, t_inj - 3, t_end + 25) for lb in LBS}
    run.save("haproxy-events.json", ev)
    rows, ok_all = [], True
    for vname, recs in vant.items():
        an = O.analyse_vantage(recs, stalls)
        for probe in ("web", "api_app", "lb160_web", "lb162_web", "kube_api"):
            v = an["raw"].get(probe)
            if not v:
                continue
            n_fail = v.get("failures")
            crit = probe in ("web", "api_app")
            ok = v["longest_failure_s"] <= 10 and (n_fail is None or n_fail <= 12)
            if crit:
                ok_all = ok_all and ok
            rows.append("%-8s %-10s %s%s%s" % (vname, probe, O.fmt_probe(an["raw"], probe), (" | échecs≈%s" % n_fail) if n_fail is not None else "",
                                                ("  séries: %s" % v.get("failure_runs", [])[:6]) if v.get("failure_runs") else ""))
            if crit:
                rows[-1] += "   => %s" % ("OK" if ok else "KO")
    say("=== RÉSULTAT (fenêtre d'injection %.0f s, victime .%s) ===" % (t_end - t_inj, a.victim))
    for r in rows:
        say("  " + r)
    for lb in LBS:
        downs = [l for l in ev[lb] if "is DOWN" in l or "no server available" in l or " is UP" in l]
        say("  HAProxy .%s : %d évènements ingress (DOWN/UP/aucun serveur)" % (lb, len(downs)))
        for l in downs[:10]:
            say("     " + re.sub(r"^\S+ \S+ haproxy\[\d+\]: ", "", l)[:190])
    if load_proc is not None:
        try:
            load_proc.wait(timeout=a.duration + 60)
        except subprocess.TimeoutExpired:
            load_proc.terminate()
        recs_l = []
        try:
            with open(load_out, encoding="utf-8", errors="replace") as fh:
                for l in fh:
                    try:
                        r = json.loads(l)
                    except ValueError:
                        continue
                    if "path" in r and "ok" in r and t_inj - 1 <= O.epoch_of(r["ts"]) <= t_end + 1:
                        recs_l.append(r)
        except OSError:
            pass
        say("--- CHARGE %.0f req/s vers le LB .%s pendant l'injection (%d requêtes) ---" % (a.load_rps, a.load_lb, len(recs_l)))
        for pth in ("/", "/api/test/health-readiness"):
            rs = sorted((r for r in recs_l if r["path"] == pth), key=lambda r: r["ts"])
            if not rs:
                continue
            lat = sorted(r["ms"] for r in rs if r["ok"])
            ko = [r for r in rs if not r["ok"]]
            run_max = cur = 0
            for r in rs:
                cur = cur + 1 if not r["ok"] else 0
                run_max = max(run_max, cur)
            pc = lambda p: lat[int(p * (len(lat) - 1))] if lat else 0
            say("  %-28s n=%d échecs=%d (%.1f %%) | succès p50=%d p95=%d max=%d ms | plus longue suite d'échecs consécutifs: %d | erreurs: %s"
                % (pth, len(rs), len(ko), 100.0 * len(ko) / len(rs), pc(0.5), pc(0.95), lat[-1] if lat else 0, run_max,
                   sorted({(r.get("err") or "HTTP %s" % r.get("status"))[:40] for r in ko})[:3]))
            rows.append("charge %s : n=%d échecs=%d (%.1f %%) p50=%d p95=%d" % (pth, len(rs), len(ko), 100.0 * len(ko) / len(rs), pc(0.5), pc(0.95)))
    verdict = "CRITÈRE INDICATIF %s" % ("TENU" if ok_all and not abort else "NON TENU")
    say(verdict)
    run.save("summary.md", "# p4_webpath_blackhole %s\n\n%s\n\n```\n%s\n```\n" % (a.label, verdict, "\n".join(rows)))
    return 2 if abort else 0


if __name__ == "__main__":
    sys.exit(main())
