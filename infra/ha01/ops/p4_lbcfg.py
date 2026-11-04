#!/usr/bin/env python3
"""
HA-01 (écart E16) — déploie la configuration HAProxy CANONIQUE (infra/ha01/haproxy/haproxy.cfg) sur UN répartiteur de charge, avec les garde-fous de
p3_switch.py : préflight · diff limité aux backends web (sinon STOP) · validation `haproxy -c` sur le LB AVANT installation · sauvegarde datée + copie de
retour · minuterie systemd côté LB qui restaure la config d'origine si ce script disparaît · rechargement gracieux · observation sous charge avec abandon
automatique -> RETOUR ARRIÈRE.

  python p4_lbcfg.py --lb 160 [--observe 120] [--dry-run]       # .160 d'abord (secours DNS), puis --lb 162 (LB actif du DNS)
  python p4_lbcfg.py --lb 160 --rollback                         # remet la config d'avant (copie /etc/haproxy/haproxy.cfg.ha01-p3-prev)

Changement E16 : contrôle de santé des backends web = `GET /ping` de Traefik (entrypoint `ping` :8082) au lieu de `GET /` qui traverse l'application ;
`timeout connect 1s` sur ces backends. Code retour : 0 OK · 1 échec (restauré) · 3 préflight KO (rien fait) · 4 filet non armé (rien fait).
"""
import argparse
import difflib
import hashlib
import os
import re
import subprocess
import sys
import time

import ha01_ops as O
import p3_switch as S

TARGET = os.path.normpath(os.path.join(O.HERE, "..", "haproxy", "haproxy.cfg"))
ALLOWED = {"backend ingress_http", "backend ingress_https"}


def sections(cfg):
    """{nom de section: lignes utiles (sans commentaires ni lignes vides, espaces normalisés)}. Les commentaires sont ignorés."""
    out, cur = {}, "(début)"
    for raw in cfg.replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if re.match(r"^(global|defaults|frontend|backend|listen)\b", raw):
            cur = " ".join(raw.split())
            out.setdefault(cur, [])
            continue
        out.setdefault(cur, []).append(" ".join(line.split()))
    return out


def differing_sections(a, b):
    sa, sb = sections(a), sections(b)
    return sorted(k for k in set(sa) | set(sb) if sa.get(k) != sb.get(k))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lb", required=True, choices=["160", "162"])
    ap.add_argument("--observe", type=int, default=120)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--rollback", action="store_true")
    a = ap.parse_args()
    lb, other = a.lb, ("162" if a.lb == "160" else "160")
    run = O.Run("p4-lbcfg-%s%s" % (lb, "-rollback" if a.rollback else ""))
    say = run.say
    if a.rollback:
        for k in range(3):
            rc, out = O.ssh(lb, "sudo -n %s; systemctl is-active haproxy" % S.RESTORE_PATH, timeout=40)
            rc2, cfg_now = O.ssh(lb, "sudo -n cat %s; echo; sudo -n cat %s" % (S.CFG, S.PREV), timeout=40)
            say("restauration tentative %d : %s" % (k + 1, out.replace("\n", " ")))
            ok, bad = O.haproxy_all_up(lb)
            if ok:
                say("HAProxy .%s : tous les serveurs UP après la restauration" % lb)
                return 0
            time.sleep(4)
        return 1
    target = open(TARGET, encoding="utf-8").read().replace("\r\n", "\n")
    if not target.endswith("\n"):
        target += "\n"
    chk = []

    def add(name, ok, detail):
        chk.append({"check": name, "ok": bool(ok), "detail": detail})
        say("PREFLIGHT %-72s %s  %s" % (name[:72], "OK " if ok else "KO ", str(detail)[:105]))
    ns = O.nodes_state()
    add("3 nœuds Ready, etcd 5/5", len(ns) == 3 and all(v == "Ready" for v in ns.values()) and O.etcd_healthy() == 5, ns)
    st = O.cnpg_state()
    add("CNPG sain 2/2, aucun pod non sain", st.startswith("Cluster in healthy state") and st.endswith("|2") and not O.unhealthy_pods(), st)
    add("harnais actif", O.file_age(O.PROBE_WIN) < 15 and O.file_age(O.SAMPLES) < 60, "%.0fs/%.0fs" % (O.file_age(O.PROBE_WIN), O.file_age(O.SAMPLES)))
    h = O.host_avail_mb()
    add("RAM hôte >= 1500 Mo", h >= 1500, h)
    for x in (lb, other):
        ok, d = O.lb_direct_ok(x)
        up, bad = O.haproxy_all_up(x)
        add("LB .%s sert web + API en direct, serveurs UP" % x, ok and up, "%s %s" % (d, bad))
    rc, cur = O.ssh(lb, "sudo -n cat %s" % S.CFG, timeout=30)
    cur = cur.replace("\r\n", "\n")
    diff_secs = differing_sections(cur, target)
    add("différences limitées aux backends web (%s)" % sorted(ALLOWED), bool(diff_secs) and set(diff_secs) <= ALLOWED, diff_secs)
    add("la cible contient 2 contrôles /ping sur :8082", target.count("uri /ping") == 2 and target.count("check port 8082") == 2, "")
    for n in ("155", "158"):
        rc, out = O.ssh(lb, "curl -s -o /dev/null -m 3 -w '%%{http_code}' http://192.168.148.%s:8082/ping" % n, timeout=20)
        add("/ping de Traefik joignable depuis .%s vers .%s:8082" % (lb, n), out.strip() == "200", out)
    rc, out = O.ssh(lb, "sudo -n systemctl list-units 'ha01-*' --all --no-legend | wc -l", timeout=30)
    add("aucune minuterie ha01-* résiduelle sur .%s" % lb, out.strip().splitlines()[-1] == "0", out)
    ws = O.ha01_hoststall.windows(time.time() - 300, time.time())
    add("aucun gel d'E/S de l'hôte dans les 5 dernières minutes", ws == [], O.ha01_hoststall.describe(ws))
    run.save("preflight.json", chk)
    diff_txt = "".join(difflib.unified_diff(cur.splitlines(True), target.splitlines(True), "deploye", "canonique", n=1))
    run.save("diff.txt", diff_txt)
    if not all(c["ok"] for c in chk):
        say("STOP: préconditions non satisfaites — aucune action.")
        return 3
    if a.dry_run:
        say("dry-run: préconditions OK ; diff enregistré (%d lignes). Rien n'est appliqué." % len(diff_txt.splitlines()))
        return 0
    ts = O.now().strftime("%Y%m%dT%H%M%SZ")
    unit = "ha01-p4-restore-%s-%s" % (lb, ts.lower())
    rc, out = O.ssh(lb, "sudo -n tee %s >/dev/null && sudo -n chmod 755 %s && echo ok" % (S.RESTORE_PATH, S.RESTORE_PATH), timeout=30, stdin=S.RESTORE_SH)
    if not out.endswith("ok"):
        say("STOP: installation du script de restauration impossible — aucune action.")
        return 4
    rc, out = O.ssh(lb, "sudo -n cp -p %s %s.bak-ha01-p4-%s && sudo -n cp -p %s %s && echo ok" % (S.CFG, S.CFG, ts, S.CFG, S.PREV), timeout=30)
    if not out.endswith("ok"):
        say("STOP: sauvegarde de la config impossible — aucune action.")
        return 4
    rc, out = O.ssh(lb, "sudo -n tee /tmp/ha01-haproxy-new.cfg >/dev/null && sudo -n haproxy -c -f /tmp/ha01-haproxy-new.cfg 2>&1 | tail -2", timeout=40, stdin=target)
    if "valid" not in out.lower():
        say("STOP: la nouvelle config est invalide (%s) — aucune action." % out[:160])
        return 4
    if not O.deadman_arm(lb, unit, a.observe + 240, S.RESTORE_PATH):
        say("STOP: minuterie de sécurité NON armée sur .%s — aucune action." % lb)
        return 4
    say("minuterie de sécurité armée (%s) : restauration automatique dans %d s si ce script disparaît" % (unit, a.observe + 240))
    load_out = os.path.join(run.dir, "load.jsonl")
    det = O.ha01_hoststall.LiveDetector([O.NET + s for s in O.ETCD_ALL], period=5.0)
    det.start()
    t_sw = time.time()
    rc, out = O.ssh(lb, "sudo -n cp /tmp/ha01-haproxy-new.cfg %s && sudo -n systemctl reload haproxy; sleep 1; systemctl is-active haproxy" % S.CFG, timeout=40)
    say("DÉPLOIEMENT sur .%s ; reload : %s" % (lb, out.replace("\n", " ")))
    abort, load = None, None
    host_min, etcd_bad = 99999, 0
    try:
        t_up = time.time() + 30
        while time.time() < t_up:
            ok, bad = O.haproxy_all_up(lb)
            if ok:
                break
            time.sleep(2)
        else:
            abort = "serveurs HAProxy non UP 30 s après le rechargement: %s" % bad
        if abort is None:
            say("serveurs HAProxy UP %.0f s après le rechargement" % (time.time() - t_sw))
            load = subprocess.Popen([sys.executable, os.path.join(O.PROBES, "ha01_load.py"), "--rps", "10", "--duration", str(a.observe + 5),
                                     "--out", load_out, "--ip", O.NET + lb], stdout=open(os.path.join(run.dir, "load-summary.txt"), "w"), stderr=subprocess.STDOUT)
        t_end = time.time() + a.observe
        while abort is None and time.time() < t_end:
            cyc = time.time()
            ok_up, bad = O.haproxy_all_up(lb)
            runs = {p: O.tail_fail_run(O.PROBE_WIN, p) for p in O.BY_NAME}
            lbrun = max(O.tail_fail_run(O.PROBE_WIN, "lb%s_web" % lb), O.tail_fail_run(O.PROBE_WIN, "lb%s_api" % lb))
            hh = O.etcd_healthy()
            etcd_bad = etcd_bad + 1 if hh < 4 else 0
            hm = O.host_avail_mb()
            host_min = min(host_min, hm)
            if not ok_up:
                abort = "serveurs HAProxy non UP: %s" % bad
            elif lbrun > 10:
                abort = "LB .%s en direct en échec depuis %ss" % (lb, lbrun)
            elif max(runs.values()) > 15:
                abort = "sonde par nom en échec depuis %ss" % max(runs.values())
            elif etcd_bad >= 2:
                abort = "etcd dégradé (%s sains) sur 2 cycles" % hh
            elif hm < 600:
                abort = "RAM hôte %s Mo < 600" % hm
            if abort:
                say("ABANDON: %s" % abort)
                break
            time.sleep(max(0.0, 4 - (time.time() - cyc)))
    finally:
        if abort:
            for k in range(3):
                rc, out = O.ssh(lb, "sudo -n %s; systemctl is-active haproxy" % S.RESTORE_PATH, timeout=40)
                rc2, cfg_now = O.ssh(lb, "sudo -n cat %s" % S.CFG, timeout=30)
                if "uri /ping" not in cfg_now:
                    say("RESTAURATION de la config d'origine : confirmée")
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
    stalls = list(det.windows()) + (O.ha01_hoststall.windows(t_sw - 60, t_stop) or [])
    recs = O.window_records_local(O.PROBE_WIN, t_sw, t_stop)
    an = O.analyse_vantage(recs, stalls)
    n, okn, p95 = S.load_summary(load_out, stalls)
    rc, cfg_final = O.ssh(lb, "sudo -n cat %s" % S.CFG, timeout=30)
    ok_up, bad = O.haproxy_all_up(lb)
    sha = hashlib.sha256(cfg_final.replace("\r\n", "\n").strip().encode()).hexdigest()[:16]
    want = hashlib.sha256(target.strip().encode()).hexdigest()[:16]
    rows = [("gels d'E/S de l'hôte pendant l'observation (informatif, E6)", True, O.ha01_hoststall.describe(stalls)),
            ("config finale de .%s = config canonique (sha256 %s)" % (lb, want), (sha == want) if not abort else ("uri /ping" not in cfg_final), sha),
            ("HAProxy .%s : tous les serveurs UP" % lb, ok_up, bad)]
    for p in ("lb%s_web" % lb, "lb%s_api" % lb):
        v = an["hors"].get(p)
        rows.append(("[Windows] %s >= 99,9 %% (hors gels)" % p, bool(v) and v["availability_pct"] >= 99.9, "hors gel: %s | BRUT: %s" % (O.fmt_probe(an["hors"], p), O.fmt_probe(an["raw"], p))))
    for p, minp, maxl in (("web", 99.0, 10), ("api_app", 99.0, 10), ("kube_api", 99.0, 15)):
        v = an["hors"].get(p)
        rows.append(("[Windows] %s >= %s %% et série <= %s s (hors gels)" % (p, minp, maxl), bool(v) and v["availability_pct"] >= minp and v["longest_failure_s"] <= maxl,
                     "hors gel: %s | BRUT: %s" % (O.fmt_probe(an["hors"], p), O.fmt_probe(an["raw"], p))))
    rows.append(("charge 10 req/s forcée vers .%s : >= 99,9 %% de succès (hors gels), p95 < 1500 ms" % lb, n > 0 and okn / n >= 0.999 and p95 < 1500, "%d/%d p95=%d ms" % (okn, n, p95)))
    rows.append(("RAM hôte >= 1500 Mo (minimum mesuré)", host_min >= 1500, host_min))
    rows.append(("pas d'abandon (sinon config d'origine restaurée)", abort is None, abort))
    ok_all = O.write_report(run, "Déploiement HAProxy canonique sur .%s" % lb, rows, "```\n" + diff_txt + "\n```", {"lb": lb, "t_deploy": t_sw, "abort": abort})
    if not abort:
        O.deadman_disarm(lb, unit)
        say("minuterie de sécurité désarmée ; copie de retour conservée: %s" % S.PREV)
    say("RÉSULTAT: %d/%d critères satisfaits -> %s" % (sum(1 for _, ok, _ in rows if ok), len(rows), run.dir))
    for c, ok, v in rows:
        say("  %s %-90s %s" % ("OK" if ok else "KO", c[:90], str(v)[:110]))
    return 0 if (ok_all and not abort) else 1


if __name__ == "__main__":
    sys.exit(main())
