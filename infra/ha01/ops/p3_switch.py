#!/usr/bin/env python3
"""
HA-01 Phase 3 — bascule des ports applicatifs d'UN répartiteur de charge entre ingress-nginx (NodePorts 31756/31411) et Traefik
(32080/32443), avec filet de sécurité, charge réelle, observation et RETOUR ARRIÈRE AUTOMATIQUE.

  python p3_switch.py --lb 162 --to traefik [--observe 300] [--dry-run]       # .162 d'abord, puis --lb 160
  python p3_switch.py --lb 162 --to nginx                                     # retour (même procédure, sens inverse)

Ce qui change dans /etc/haproxy/haproxy.cfg (rien d'autre) : les 2 serveurs de ingress_http (:31756 -> :32080), les 2 de
ingress_https (:31411 -> :32443) et `check port 31756` -> `check port 32080`. Validation `haproxy -c` AVANT installation, rechargement
gracieux (`systemctl reload`), sauvegarde datée + copie de retour /etc/haproxy/haproxy.cfg.ha01-p3-prev.
Filet : minuterie systemd sur le LB qui restaure la copie de retour si ce script disparaît. Abandon automatique (serveurs non UP,
LB direct KO > 10 s, sonde par nom > 15 s, etcd, mémoire, RAM hôte) -> restauration immédiate.
Charge : 20 req/s forcés vers CE LB pendant l'observation (le trafic réel passe surtout par le LB primaire du DNS).
Prérequis (--to traefik) : shadow opérationnel (p3_shadow.py apply), recette PASS de moins de 24 h (p3_shadow.py accept).
Après les DEUX LB : mettre à jour infra/ha01/haproxy/haproxy.cfg (canonique) avec les nouveaux ports, observer 48 h, puis
décommissionner ingress-nginx SEULEMENT avec l'accord explicite de l'utilisateur.
Code retour : 0 OK · 1 échec (restauré) · 3 préflight KO (rien fait) · 4 filet non armé (rien fait).
"""
import argparse
import glob
import json
import os
import subprocess
import sys
import time

import ha01_ops as O

PORTS = {"nginx": (31756, 31411), "traefik": (32080, 32443),
         "traefik-host": (8000, 8443)}      # Traefik en hostNetwork (écart E13) : HAProxy le joint directement sur l'IP du nœud, sans NodePort ni kube-proxy


def detect(cfg):
    """Jeu de ports actuellement utilisé par la config (2 http, 2 https, 1 check), ou None."""
    for k in PORTS:
        if counts(cfg, k) == {"http": 2, "https": 2, "check": 1}:
            return k
    return None
CFG = "/etc/haproxy/haproxy.cfg"
PREV = CFG + ".ha01-p3-prev"
RESTORE_PATH = "/usr/local/sbin/ha01-p3-restore.sh"
RESTORE_SH = """#!/bin/sh
# HA-01 Phase 3 - filet : restaure la config HAProxy d'avant la bascule et recharge. Idempotent.
P=/etc/haproxy/haproxy.cfg.ha01-p3-prev
[ -s "$P" ] || exit 0
cp -p "$P" /etc/haproxy/haproxy.cfg && /usr/sbin/haproxy -c -f /etc/haproxy/haproxy.cfg && systemctl reload haproxy
exit 0
"""


def counts(cfg, which):
    ch, cs = PORTS[which]
    return {"http": sum(cfg.count("192.168.148.%s:%d" % (ip, ch)) for ip in ("155", "158")),
            "https": sum(cfg.count("192.168.148.%s:%d" % (ip, cs)) for ip in ("155", "158")),
            "check": cfg.count("check port %d" % ch)}


def transform(cfg, frm, to):
    (ch, cs), (nh, ns_) = PORTS[frm], PORTS[to]
    out = cfg
    for ip in ("155", "158"):
        out = out.replace("192.168.148.%s:%d" % (ip, ch), "192.168.148.%s:%d" % (ip, nh))
        out = out.replace("192.168.148.%s:%d" % (ip, cs), "192.168.148.%s:%d" % (ip, ns_))
    return out.replace("check port %d" % ch, "check port %d" % nh)


def latest_accept_pass(max_age_h=24):
    files = sorted(glob.glob(os.path.join(O.RES, "p3-accept-*.md")))
    if not files:
        return False, "aucune recette trouvée"
    f = files[-1]
    age_h = (time.time() - os.path.getmtime(f)) / 3600
    return ("(PASS)" in open(f, encoding="utf-8").read(400) and age_h <= max_age_h), "%s (%.1f h)" % (os.path.basename(f), age_h)


def preflight(a, lb, other, frm, to, say):
    chk = []

    def add(name, ok, detail):
        chk.append({"check": name, "ok": bool(ok), "detail": detail})
        say("PREFLIGHT %-70s %s  %s" % (name[:70], "OK " if ok else "KO ", str(detail)[:105]))
    ns = O.nodes_state()
    add("3 nœuds Ready, etcd 5/5", len(ns) == 3 and all(v == "Ready" for v in ns.values()) and O.etcd_healthy() == 5, ns)
    st = O.cnpg_state()
    add("CNPG sain 2/2, aucun pod non sain", st.startswith("Cluster in healthy state") and st.endswith("|2") and not O.unhealthy_pods(), st)
    add("harnais actif", O.file_age(O.PROBE_WIN) < 15 and O.file_age(O.SAMPLES) < 60, "%.0fs/%.0fs" % (O.file_age(O.PROBE_WIN), O.file_age(O.SAMPLES)))
    h = O.host_avail_mb()
    add("RAM hôte >= 2000 Mo", h >= 2000, h)
    for w in ("155", "158"):
        m = O.mem_avail(w)
        add(".%s MemAvailable >= 600 Mo" % w, (m or 0) >= 600, m)
    for x in (lb, other):
        ok, d = O.lb_direct_ok(x)
        add("LB .%s sert web + API en direct" % x, ok, d)
        ok, bad = O.haproxy_all_up(x)
        add("HAProxy .%s : tous les serveurs UP" % x, ok, bad)
    rc, cfg = O.ssh(lb, "sudo -n cat %s" % CFG, timeout=30)
    c = counts(cfg, frm)
    add("config de .%s utilise les ports de %s (2 http, 2 https, 1 check)" % (lb, frm), c == {"http": 2, "https": 2, "check": 1}, c)
    rc, out = O.ssh(lb, "sudo -n haproxy -c -f %s 2>&1 | tail -1; test -x /usr/sbin/haproxy && echo haproxy-bin-ok; sudo -n systemctl list-units 'ha01-*' --all --no-legend | wc -l" % CFG, timeout=30)
    add("config actuelle valide, /usr/sbin/haproxy présent, aucune minuterie ha01-* résiduelle",
        "valid" in out.lower() and "haproxy-bin-ok" in out and out.strip().splitlines()[-1] == "0", out.replace("\n", " | "))
    if to in ("traefik", "traefik-host"):
        rc, out = O.kubectl("-n traefik get pods --no-headers")
        add("Traefik : 2 pods Ready", out.count(" Running ") == 2 and out.count("1/1") == 2, out.replace("\n", " | ")[:120])
        rc, out = O.kubectl("-n ingress-nginx get pods --no-headers")
        add("ingress-nginx : 2 pods Ready (retour arrière possible)", out.count(" Running ") == 2 and out.count("1/1") == 2, out.replace("\n", " | ")[:120])
        ok, d = latest_accept_pass()
        add("recette du shadow PASS de moins de 24 h", ok, d)
    if to == "traefik-host":
        import p3_harden
        rc, hn = O.kubectl("-n traefik get deploy traefik -o jsonpath='{.spec.template.spec.hostNetwork}'")
        add("Traefik en hostNetwork", hn.replace("'", "") == "true", hn)
        for ip in ("155", "158"):
            r1, r2 = p3_harden.http_status(ip, 8000, False), p3_harden.http_status(ip, 8443, True)
            add("Traefik répond DIRECTEMENT sur .%s:8000 et :8443" % ip, r1 in (200, 301, 302, 404) and r2 in (200, 301, 302, 404), "http=%s https=%s" % (r1, r2))
    ws = O.ha01_hoststall.windows(time.time() - 300, time.time())
    add("aucun gel d'E/S de l'hôte dans les 5 dernières minutes", ws == [], O.ha01_hoststall.describe(ws))
    return chk, cfg


def load_summary(path, stalls):
    ok = n = 0
    lat = []
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for l in fh:
                try:
                    r = json.loads(l)
                except ValueError:
                    continue
                if "ok" not in r or "ts" not in r or O.ha01_hoststall.inside(stalls, O.epoch_of(r["ts"]), 5, 10):
                    continue
                n += 1
                ok += 1 if r["ok"] else 0
                if r["ok"]:
                    lat.append(r.get("ms", 0))
    except OSError:
        pass
    lat.sort()
    return n, ok, (lat[int(0.95 * (len(lat) - 1))] if lat else 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lb", required=True, choices=["160", "162"])
    ap.add_argument("--to", required=True, choices=["traefik", "traefik-host", "nginx"])
    ap.add_argument("--observe", type=int, default=300)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    lb, other = a.lb, ("162" if a.lb == "160" else "160")
    to = a.to
    run = O.Run("p3-switch-%s-%s" % (lb, to))
    say = run.say
    rc, cfg0 = O.ssh(lb, "sudo -n cat %s" % CFG, timeout=30)
    frm = detect(cfg0)                        # le jeu de ports ACTUEL est lu sur le LB (nginx, traefik ou traefik-host)
    if frm is None or frm == to:
        say("STOP: jeu de ports actuel de .%s = %s ; rien à faire ou config inattendue pour aller vers %s." % (lb, frm, to))
        return 3
    say("config actuelle de .%s : ports de %s -> %s" % (lb, frm, to))
    chk, cfg = preflight(a, lb, other, frm, to, say)
    run.save("preflight.json", chk)
    if not all(c["ok"] for c in chk):
        say("STOP: préconditions non satisfaites — aucune action effectuée.")
        return 3
    new_cfg = transform(cfg, frm, to)
    if not new_cfg.endswith("\n"):
        new_cfg += "\n"
    cn = counts(new_cfg, to)
    if cn != {"http": 2, "https": 2, "check": 1} or any(counts(new_cfg, frm).values()):
        say("STOP: transformation inattendue de la config (%s / restes: %s) — aucune action." % (cn, counts(new_cfg, frm)))
        return 3
    if a.dry_run:
        say("dry-run: préconditions OK ; la config .%s passerait de %s à %s (%s). Rien n'est appliqué." % (lb, frm, to, cn))
        run.save("haproxy.cfg.new", new_cfg)
        return 0
    ts = O.now().strftime("%Y%m%dT%H%M%SZ")
    unit = "ha01-p3-restore-%s-%s" % (lb, ts.lower())
    rc, out = O.ssh(lb, "sudo -n tee %s >/dev/null && sudo -n chmod 755 %s && echo ok" % (RESTORE_PATH, RESTORE_PATH), timeout=30, stdin=RESTORE_SH)
    if not out.endswith("ok"):
        say("STOP: installation du script de restauration impossible — aucune action.")
        return 4
    rc, out = O.ssh(lb, "sudo -n cp -p %s %s.bak-ha01-p3-%s && sudo -n cp -p %s %s && echo ok" % (CFG, CFG, ts, CFG, PREV), timeout=30)
    if not out.endswith("ok"):
        say("STOP: sauvegarde de la config impossible — aucune action.")
        return 4
    rc, out = O.ssh(lb, "sudo -n tee /tmp/ha01-haproxy-new.cfg >/dev/null && sudo -n haproxy -c -f /tmp/ha01-haproxy-new.cfg 2>&1 | tail -2", timeout=40, stdin=new_cfg)
    if "valid" not in out.lower():
        say("STOP: la nouvelle config est invalide (%s) — aucune action." % out[:160])
        return 4
    if not O.deadman_arm(lb, unit, a.observe + 240, RESTORE_PATH):
        say("STOP: minuterie de sécurité NON armée sur .%s — aucune action." % lb)
        return 4
    say("minuterie de sécurité armée (%s) : restauration automatique dans %d s si ce script disparaît" % (unit, a.observe + 240))

    load_out = os.path.join(run.dir, "load.jsonl")
    det = O.ha01_hoststall.LiveDetector([O.NET + s for s in O.ETCD_ALL], period=5.0)
    det.start()
    t_switch = time.time()
    rc, out = O.ssh(lb, "sudo -n cp %s %s && sudo -n systemctl reload haproxy; sleep 1; systemctl is-active haproxy" % ("/tmp/ha01-haproxy-new.cfg", CFG), timeout=40)
    say("BASCULE .%s : %s -> %s ; reload : %s" % (lb, frm, to, out.replace("\n", " ")))
    abort = None
    load = None
    etcd_bad_n, host_min, mem_min = 0, 99999, {"155": 9999, "158": 9999}
    try:
        up_deadline = time.time() + 30
        while time.time() < up_deadline:
            ok, bad = O.haproxy_all_up(lb)
            if ok:
                break
            time.sleep(3)
        else:
            abort = "serveurs HAProxy non UP 30 s après la bascule: %s" % bad
        if abort is None:
            say("serveurs HAProxy UP %.0f s après la bascule" % (time.time() - t_switch))
            load = subprocess.Popen([sys.executable, os.path.join(O.PROBES, "ha01_load.py"), "--rps", "20", "--duration", str(a.observe + 5),
                                     "--out", load_out, "--ip", O.NET + lb], stdout=open(os.path.join(run.dir, "load-summary.txt"), "w"), stderr=subprocess.STDOUT)
        t_end = time.time() + a.observe
        while abort is None and time.time() < t_end:
            cyc = time.time()
            ok_up, bad = O.haproxy_all_up(lb)
            runs = {p: O.tail_fail_run(O.PROBE_WIN, p) for p in O.BY_NAME}
            lbrun = max(O.tail_fail_run(O.PROBE_WIN, "lb%s_web" % lb), O.tail_fail_run(O.PROBE_WIN, "lb%s_api" % lb))
            h = O.etcd_healthy()
            etcd_bad_n = etcd_bad_n + 1 if h < 4 else 0
            hm = O.host_avail_mb()
            host_min = min(host_min, hm)
            for w in ("155", "158"):
                m = O.mem_avail(w)
                if m is not None:
                    mem_min[w] = min(mem_min[w], m)
            ns = O.nodes_state()
            if not ok_up:
                abort = "serveurs HAProxy non UP: %s" % bad
            elif lbrun > 10:
                abort = "LB .%s en direct en échec depuis %ss" % (lb, lbrun)
            elif max(runs.values()) > 15:
                abort = "sonde par nom en échec depuis %ss" % max(runs.values())
            elif etcd_bad_n >= 2:
                abort = "etcd dégradé (%s sains) sur 2 cycles" % h
            elif min(mem_min.values()) < 250:
                abort = "MemAvailable d'un worker < 250 Mo (%s)" % mem_min
            elif hm < 600:
                abort = "RAM hôte %s Mo < 600" % hm
            elif any(v != "Ready" for v in ns.values()) or len(ns) != 3:
                abort = "nœud non Ready: %s" % ns
            if abort:
                say("ABANDON: %s" % abort)
                break
            time.sleep(max(0.0, 5 - (time.time() - cyc)))
    finally:
        if abort:
            for k in range(3):
                rc, out = O.ssh(lb, "sudo -n %s; systemctl is-active haproxy" % RESTORE_PATH, timeout=40)
                rc2, cfg_now = O.ssh(lb, "sudo -n cat %s" % CFG, timeout=30)
                if counts(cfg_now, frm) == {"http": 2, "https": 2, "check": 1}:
                    say("RESTAURATION de la config d'origine (%s) : confirmée" % frm)
                    break
                say("restauration tentative %d non confirmée (%s)" % (k + 1, out[:100]))
                time.sleep(3)
            O.deadman_disarm(lb, unit)
        if load is not None:
            try:
                load.wait(timeout=a.observe + 60)
            except subprocess.TimeoutExpired:
                load.terminate()
    t_stop = time.time()
    det.stop()
    stalls = list(det.windows()) + (O.ha01_hoststall.windows(t_switch - 60, t_stop) or [])
    recs = O.window_records_local(O.PROBE_WIN, t_switch, t_stop)
    an = O.analyse_vantage(recs, stalls)
    n, okn, p95 = load_summary(load_out, stalls)
    rc, cfg_final = O.ssh(lb, "sudo -n cat %s" % CFG, timeout=30)
    ok_up, bad = O.haproxy_all_up(lb)
    rows = [("gels d'E/S de l'hôte pendant l'observation (informatif, E6)", True, O.ha01_hoststall.describe(stalls)),
            ("config finale de .%s = ports de %s" % (lb, to if not abort else frm), counts(cfg_final, to if not abort else frm) == {"http": 2, "https": 2, "check": 1}, counts(cfg_final, to)),
            ("HAProxy .%s : tous les serveurs UP" % lb, ok_up, bad)]
    for p in ("lb%s_web" % lb, "lb%s_api" % lb):
        v = an["hors"].get(p)
        rows.append(("[Windows] %s >= 99,9 %% (hors gels)" % p, bool(v) and v["availability_pct"] >= 99.9, "hors gel: %s | BRUT: %s" % (O.fmt_probe(an["hors"], p), O.fmt_probe(an["raw"], p))))
    for p, minp, maxl in (("web", 99.0, 10), ("api_app", 99.0, 10), ("kube_api", 99.0, 15)):
        v = an["hors"].get(p)
        rows.append(("[Windows] %s >= %s %% et série <= %s s (hors gels)" % (p, minp, maxl), bool(v) and v["availability_pct"] >= minp and v["longest_failure_s"] <= maxl,
                     "hors gel: %s | BRUT: %s" % (O.fmt_probe(an["hors"], p), O.fmt_probe(an["raw"], p))))
    rows.append(("charge 20 req/s forcée vers .%s : >= 99,9 %% de succès (hors gels), p95 < 1500 ms" % lb, n > 0 and okn / n >= 0.999 and p95 < 1500, "%d/%d p95=%d ms" % (okn, n, p95)))
    rows.append(("mémoire : workers >= 300 Mo (minimum mesuré)", min(mem_min.values()) >= 300, mem_min))
    rows.append(("RAM hôte >= 1500 Mo (minimum mesuré)", host_min >= 1500, host_min))
    rows.append(("pas d'abandon (sinon config d'origine restaurée)", abort is None, abort))
    ok_all = O.write_report(run, "Bascule .%s : %s -> %s" % (lb, frm, to), rows, "", {"lb": lb, "to": to, "t_switch": t_switch, "abort": abort})
    if not abort:
        O.deadman_disarm(lb, unit)
        say("minuterie de sécurité désarmée ; copie de retour conservée: %s" % PREV)
    say("RÉSULTAT: %d/%d critères satisfaits -> %s" % (sum(1 for _, ok, _ in rows if ok), len(rows), run.dir))
    for c, ok, v in rows:
        say("  %s %-90s %s" % ("OK" if ok else "KO", c[:90], str(v)[:110]))
    return 0 if (ok_all and not abort) else 1


if __name__ == "__main__":
    sys.exit(main())
