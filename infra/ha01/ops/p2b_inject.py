#!/usr/bin/env python3
"""
HA-01 Phase 2b — INJECTION de panne sur un répartiteur de charge (.160 ou .162), SANS éteindre de VM.

  python p2b_inject.py --mode stop    --lb 162 --label s162      # le SERVICE HAProxy meurt, la VM vit : connexions REFUSÉES
  python p2b_inject.py --mode isolate --lb 160 --label i160      # la VM est ISOLÉE du réseau en silence (iptables INPUT DROP,
                                                                 #   sauf loopback et SSH depuis le poste) : simule la VM morte
  options : --duration S (300 en stop, 240 en isolate)   --dry-run (préflight seul)   --no-cached-probe

Pourquoi « isolate » plutôt que le blackhole .153->.160 du plan : un blackhole limité à .153 ne fait JAMAIS basculer le DNS
(.160 reste sain pour les autres clients), donc ne reproduit pas la perte réelle de .160 ; l'isolation de .160 pour tous
(SYN sans réponse, etcd/DNS/HAProxy injoignables, contrôleurs DNS qui la voient DOWN) est fidèle, et c'est elle qui décide C4
(kubelet .153 reste-t-il Ready ?). Écart E9 du README.

Sécurité (dans cet ordre) : préflight complet (sinon AUCUNE action) · minuterie systemd SUR la machine visée qui rétablit tout
même si ce script ou la session disparaît · abandon automatique (etcd, mémoire, RAM hôte, sondes par nom > 60 s, nœud NotReady
> 2 min, canari muet) · rétablissement dans `finally` · récupération vérifiée. En « isolate » le départ est calé juste après un
cycle de détection du Bridge HELIOS pour qu'il ne voie jamais .160 isolée (D7 : ses réactions éventuelles sont archivées).

Mesure : 3 points de vue (Windows ; .161 sans cache DNS ; .161 AVEC cache DNS à TTL = comme systemd-resolved des VM), chacun
en BRUT et HORS gel d'E/S hôte (écart E6, détecteur en direct indépendant de Prometheus/.160), canaris des workers, nœuds,
etcd, mémoire, OOM/évictions, bascule DNS, récupération. Critères = tableau §F du plan. Code retour : 0 = tout ✅, 1 = au
moins un ❌, 2 = abandon, 3 = préflight KO (rien fait), 4 = filet non armé (rien fait).
"""
import argparse
import json
import os
import sys
import time

import ha01_ops as O

D = O.ha01_hoststall
VANTAGES = ("Windows", ".161 sans cache", ".161 avec cache")


def preflight(a, lb, other, duration):
    chk = []

    def add(name, ok, detail):
        chk.append({"check": name, "ok": bool(ok), "detail": detail})
    ns = O.nodes_state()
    add("3 nœuds Ready", len(ns) == 3 and all(v == "Ready" for v in ns.values()), ns)
    add("etcd 5/5 sain", O.etcd_healthy() == 5, O.etcd_healthy())
    st = O.cnpg_state()
    add("CNPG sain 2/2", st.startswith("Cluster in healthy state") and st.endswith("|2"), st)
    bad = O.unhealthy_pods()
    add("aucun pod non sain", not bad, bad[:5])
    for dep in ("ha01-canary-w1", "ha01-canary-w2"):
        age = O.canary_ack_age(dep)
        add("canari %s écrit depuis < 30 s" % dep, age is not None and age < 30, None if age is None else round(age, 1))
    add("harnais actif (sondes Windows < 15 s, échantillonneur < 60 s)", O.file_age(O.PROBE_WIN) < 15 and O.file_age(O.SAMPLES) < 60,
        "sondes %.0fs échantillonneur %.0fs" % (O.file_age(O.PROBE_WIN), O.file_age(O.SAMPLES)))
    rc, out = O.ssh("161", "systemctl is-active ha01-probe-161", timeout=20)
    add("sonde .161 (ha01-probe-161) active", out.strip() == "active", out)
    h = O.host_avail_mb()
    add("RAM hôte disponible >= 2000 Mo", h >= 2000, "%d Mo" % h)
    m = O.mem_avail("153")
    add(".153 MemAvailable >= 500 Mo", (m or 0) >= 500, m)
    for w in ("155", "158"):
        m = O.mem_avail(w)
        add(".%s MemAvailable >= 600 Mo" % w, (m or 0) >= 600, m)
    for x in (lb, other):
        ok, d = O.lb_direct_ok(x)
        add("LB .%s sert le web et l'API Kubernetes en direct" % x, ok, d)
        ok, bad = O.haproxy_all_up(x)
        add("HAProxy .%s : tous les serveurs UP" % x, ok, bad)
        s = O.dns_state(x)
        add("contrôleur DNS de .%s : les 2 LB UP" % x, {k: v.get("status") for k, v in (s.get("lbs") or {}).items()} == {"160": "UP", "162": "UP"},
            {"mode": s.get("mode"), "publish": s.get("publish"), "lbs": {k: v.get("status") for k, v in (s.get("lbs") or {}).items()}})
    modes = {x: O.dns_state(x).get("mode") for x in ("160", "162")}
    add("mode DNS identique sur les 2 LB (shadow = DNS statique, enforce = gating)", len(set(modes.values())) == 1 and None not in modes.values(), modes)
    rc, out = O.ssh(lb, "sudo -n systemctl list-units 'ha01-*' --all --no-legend | wc -l", timeout=20)
    add("aucune minuterie ha01-* résiduelle sur .%s" % lb, out.strip() == "0", out)
    client = ""
    if a.mode == "isolate":
        rc, out = O.ssh(lb, "sudo -n iptables -S INPUT", timeout=20)
        add("chaîne INPUT de .%s vide (policy ACCEPT, aucune règle)" % lb, out.strip().splitlines() == ["-P INPUT ACCEPT"], out)
        rc, out = O.ssh(lb, 'echo "${SSH_CLIENT%% *}"', timeout=20)
        client = out.strip()
        add("IP du poste vue par .%s (exception SSH)" % lb, bool(client) and client.count(".") == 3, client)
        rc, out = O.ssh(lb, "sudo -n test -x %s && echo present" % O.UNISOLATE_PATH, timeout=20)
        if out.strip() == "present" or a.dry_run:     # en dry-run on n'écrit rien sur la machine : le script est installé au lancement réel
            add("script de rétablissement sur .%s (%s)" % (lb, O.UNISOLATE_PATH), True, "présent" if out.strip() == "present" else "absent : sera installé au lancement réel")
        else:
            installed = O.install_unisolate(lb) and O.ssh(lb, "sudo -n %s; echo $?" % O.UNISOLATE_PATH, timeout=20)[1].endswith("0")
            add("script de rétablissement installé et inoffensif à vide sur .%s" % lb, installed, O.UNISOLATE_PATH)
    ws = D.windows(time.time() - 300, time.time())
    add("aucun gel d'E/S de l'hôte dans les 5 dernières minutes", ws == [], D.describe(ws))
    s_next = O.bridge_seconds_to_next_cycle()
    add("cycle de détection du Bridge HELIOS lisible (alignement)", s_next is not None or a.mode == "stop", None if s_next is None else round(s_next))
    return chk, client


def align_bridge(duration, say):
    """« isolate » : démarre juste après un cycle du Bridge pour que l'isolement (+ rétablissement) se termine avant le suivant."""
    s = O.bridge_seconds_to_next_cycle()
    if s is None:
        say("cycle du Bridge inconnu : pas d'alignement")
        return
    if s >= duration + 45:
        say("alignement Bridge : prochain cycle dans %.0f s >= %d s, départ immédiat" % (s, duration + 45))
        return
    say("alignement Bridge : attente de %.0f s (prochain cycle dans %.0f s < %d s)" % (s + 4, s, duration + 45))
    time.sleep(s + 4)


def start_cached_probe(unit, out_path, seconds, say):
    src = open(os.path.join(O.PROBES, "ha01_probe.py"), encoding="utf-8").read()
    rc, out = O.ssh("161", "cat > %s/ha01_probe_v2.py && echo ok" % O.TOOLS_161, timeout=40, stdin=src)
    if not out.endswith("ok"):
        say("ATTENTION: copie de la sonde v2 sur .161 impossible (%s)" % out)
        return False
    cmd = ("sudo -n systemd-run --quiet --unit=%s --property=RuntimeMaxSec=%d /usr/bin/python3 %s/ha01_probe_v2.py "
           "--probes web,api_app,kube_api --dns-servers 192.168.148.160,192.168.148.162 --dns-cache --duration %d --out %s; sleep 3; "
           "systemctl is-active %s.service" % (unit, seconds + 180, O.TOOLS_161, seconds, out_path, unit))
    rc, out = O.ssh("161", cmd, timeout=40)
    return out.strip().endswith("active")


def inject(mode, lb, client, say):
    if mode == "stop":
        rc, out = O.ssh(lb, "sudo -n systemctl stop haproxy; systemctl is-active haproxy", timeout=40)
        return out.strip().splitlines()[-1:] in (["inactive"], ["failed"]), out
    cmd = ("sudo -n iptables -A INPUT -i lo -m comment --comment ha01-isolate -j ACCEPT && "
           "sudo -n iptables -A INPUT -s %s -p tcp --dport 22 -m comment --comment ha01-isolate -j ACCEPT && "
           "sudo -n iptables -A INPUT -m comment --comment ha01-isolate -j DROP && sudo -n iptables -S INPUT" % client)
    rc, out = O.ssh(lb, cmd, timeout=40)
    return rc == 0 and "ha01-isolate -j DROP" in out, out


def restore(mode, lb, say):
    for k in range(4):
        if mode == "stop":
            rc, out = O.ssh(lb, "sudo -n systemctl start haproxy; sleep 1; systemctl is-active haproxy", timeout=40)
            ok = out.strip().splitlines()[-1:] == ["active"]
        else:
            rc, out = O.ssh(lb, "sudo -n %s; sudo -n iptables -S INPUT" % O.UNISOLATE_PATH, timeout=40)
            ok = rc == 0 and out.strip().splitlines() == ["-P INPUT ACCEPT"]
        if ok:
            return True
        say("RÉTABLISSEMENT tentative %d échouée (rc=%s): %s" % (k + 1, rc, out[:120]))
        time.sleep(3)
    return False


def lease_ages():
    rc, out = O.kubectl("-n kube-node-lease get lease -o jsonpath='{range .items[*]}{.metadata.name} {.spec.renewTime}{\"\\n\"}{end}'")
    ages = {}
    for l in out.replace("'", "").splitlines():
        p = l.split()
        if len(p) == 2:
            try:
                ages[p[0]] = round(time.time() - O.epoch_of(p[1] if "." in p[1] else p[1].replace("Z", ".0Z")), 1)
            except ValueError:
                pass
    return ages


def evaluate(c):
    """c : dict des données collectées -> (rows, extra_md). Pur (aucun effet) : testable avec des données fabriquées."""
    rows, md = [], []
    lb, other, mode = c["lb"], c["other"], c["mode"]
    stalls = c["stalls"]
    rows.append(("gels d'E/S de l'hôte pendant le test (informatif ; mesures concernées classées hors gel, E6)", True, D.describe(stalls)))
    per = {}
    for vname, recs in c["vantages"].items():
        per[vname] = O.analyse_vantage(recs, stalls)
    for vname in VANTAGES:
        if vname not in per or not per[vname]["raw"]:
            rows.append(("[%s] données de sonde présentes" % vname, vname == ".161 avec cache" and not c.get("cached_expected", True), "aucune donnée"))
            continue
        h, r = per[vname]["hors"], per[vname]["raw"]
        for probe, minp, maxl in (("web", 99.0, 10), ("api_app", 99.0, 10), ("kube_api", 99.0, 15)):
            v = h.get(probe)
            rows.append(("[%s] %s >= %s %% et série d'échecs <= %s s (hors gels)" % (vname, probe, minp, maxl),
                         bool(v) and v["availability_pct"] >= minp and v["longest_failure_s"] <= maxl,
                         "hors gel: %s | BRUT: %s" % (O.fmt_probe(h, probe), O.fmt_probe(r, probe))))
        if vname == "Windows":
            for p in ("lb%s_web" % other, "lb%s_api" % other):
                v = h.get(p)
                rows.append(("[Windows] LB survivant .%s en direct (%s) >= 99,5 %% (hors gels)" % (other, p), bool(v) and v["availability_pct"] >= 99.5,
                             "hors gel: %s | BRUT: %s" % (O.fmt_probe(h, p), O.fmt_probe(r, p))))
            for p in ("lb%s_web" % lb, "lb%s_api" % lb):
                v = r.get(p)
                # La fenêtre d'analyse déborde de 90 s après le rétablissement : on juge la plus LONGUE série d'échecs (>= 80 % de la panne), pas la disponibilité globale.
                rows.append(("[Windows] contrôle de l'injection : LB visé .%s en direct (%s) en échec pendant >= 80 %% de la panne" % (lb, p),
                             bool(v) and v["longest_failure_s"] >= 0.8 * c["duration"], "BRUT: %s" % O.fmt_probe(r, p)))
    for dep, ca in c["canaries"].items():
        rows.append(("canari %s : 0 échec hors gel, 0 écriture acquittée perdue" % dep, ca["failures"] == 0 and ca["acked_lost"] == 0,
                     "acquittées=%d échecs hors gel=%d (bruts %d) perdues=%d trou max hors gel=%.1fs (brut %.1fs)"
                     % (ca["acked"], ca["failures"], ca["failures_total"], ca["acked_lost"], ca["max_gap_hors_gel_s"], ca["max_gap_s"])))
    rows.append(("nœuds : 0 transition NotReady", not c["notready"], c["notready"] or "aucune"))
    rows.append(("etcd : au moins %d membres sains pendant tout le test" % c["etcd_expected"], c["etcd_min"] >= c["etcd_expected"], "min=%s" % c["etcd_min"]))
    rows.append(("etcd : <= 2 élections pendant le test (terme Raft)", c["term_after"] is not None and c["term_before"] is not None and c["term_after"] - c["term_before"] <= 2,
                 "terme %s -> %s, leader %s -> %s" % (c["term_before"], c["term_after"], c["leader_before"], c["leader_after"])))
    rows.append(("mémoire : workers >= 300 Mo et .153 >= 500 Mo (minimum mesuré)", c["mem_min"]["155"] >= 300 and c["mem_min"]["158"] >= 300 and c["mem_min"]["153"] >= 500, c["mem_min"]))
    rows.append(("0 OOMKilled et 0 éviction pour mémoire pendant le test", c["oom"] == 0 and not c["mem_evictions"], "OOM=%s évictions=%s" % (c["oom"], len(c["mem_evictions"]))))
    rows.append(("RAM hôte >= 1500 Mo (minimum mesuré)", c["host_min"] >= 1500, "%s Mo" % c["host_min"]))
    rows.append(("retour : LB sain, HAProxy UP et nœuds Ready en <= 300 s", c["recovered_s"] is not None and c["recovered_s"] <= 300,
                 "%s s" % (None if c["recovered_s"] is None else round(c["recovered_s"]))))
    rows.append(("retour : DNS revenu à son état nominal en <= 180 s", c["dns_nominal_s"] is not None and c["dns_nominal_s"] <= 180,
                 "%s s (mode %s)" % (None if c["dns_nominal_s"] is None else round(c["dns_nominal_s"]), c["dns_mode"])))
    rows.append(("pas d'abandon", c["abort"] is None, c["abort"]))
    md.append("## Bascule DNS observée (interrogation directe de dnsmasq toutes les secondes)")
    md.append("- mode du contrôleur : **%s** ; temps entre l'injection et la sortie du LB visé des réponses de dnsmasq .%s : **%s**"
              % (c["dns_mode"], other, "—" if c["dns_switch_s"] is None else "%.0f s" % c["dns_switch_s"]))
    md.append("- temps entre le rétablissement et le retour du LB visé dans les réponses : **%s**" % ("—" if c["dns_back_s"] is None else "%.0f s" % c["dns_back_s"]))
    md.append("")
    md.append("## Points de vue (hors gel | brut)")
    for vname in VANTAGES:
        if vname in per and per[vname]["raw"]:
            md.append("- **%s** — exclus E6 : %d échantillons dont %d échecs" % (vname, per[vname]["n_exclus"], per[vname]["echecs_exclus"]))
            for probe in sorted(per[vname]["raw"]):
                md.append("  - `%s` : %s | brut %s" % (probe, O.fmt_probe(per[vname]["hors"], probe), O.fmt_probe(per[vname]["raw"], probe)))
                runs = per[vname]["raw"][probe].get("failure_runs")
                if runs:
                    md.append("    - séries d'échecs (brut) : %s" % runs[:8])
    if c.get("lease_max"):
        md.append("")
        md.append("## Baux de nœuds (âge max du dernier renouvellement, s) — un nœud passe NotReady vers 40 s")
        md.append("- %s" % c["lease_max"])
    if mode == "isolate" and lb == "160":
        k153 = c["notready"].get("k8s-master")
        md.append("")
        md.append("## Décision C4 (kubelet .153 : garder `api.helios.orange.tn` ou revenir à l'IP locale)")
        if not k153:
            md.append("- **.153 est resté Ready** pendant l'isolation de .160 (bail max %s s) -> **GARDER** `api.helios.orange.tn`." % (c.get("lease_max") or {}).get("k8s-master"))
        else:
            md.append("- **.153 est passé NotReady** (%s) -> kubelet à revisiter : revenir à l'IP locale OU raccourcir HTTP2_READ_IDLE_TIMEOUT_SECONDS / "
                      "HTTP2_PING_TIMEOUT_SECONDS ; décision à prendre avec l'utilisateur (non appliquée automatiquement)." % k153)
    return rows, "\n".join(md)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", required=True, choices=["stop", "isolate"])
    ap.add_argument("--lb", required=True, choices=["160", "162"])
    ap.add_argument("--label", required=True)
    ap.add_argument("--duration", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-cached-probe", action="store_true")
    a = ap.parse_args()
    lb = a.lb
    other = "162" if lb == "160" else "160"
    duration = a.duration or (300 if a.mode == "stop" else 240)
    run = O.Run("p2b-%s%s-%s" % (a.mode, lb, a.label))
    say = run.say
    chk, client = preflight(a, lb, other, duration)
    run.save("preflight.json", chk)
    for c in chk:
        say("PREFLIGHT %-70s %s  %s" % (c["check"][:70], "OK " if c["ok"] else "KO ", str(c["detail"])[:110]))
    if not all(c["ok"] for c in chk):
        say("STOP: préconditions non satisfaites — aucune action effectuée.")
        return 3
    if a.dry_run:
        say("dry-run: préconditions OK, aucune action (mode=%s lb=.%s durée=%ds)." % (a.mode, lb, duration))
        return 0

    dns_mode = O.dns_state(lb).get("mode")
    unit = "ha01-%s-%s-%s" % ("start-haproxy" if a.mode == "stop" else "unisolate", lb, a.label)
    cmd = "/bin/systemctl start haproxy" if a.mode == "stop" else O.UNISOLATE_PATH
    if a.mode == "isolate" and not O.install_unisolate(lb):
        say("STOP: installation du script de rétablissement impossible sur .%s — aucune action." % lb)
        return 4

    total = duration + 840          # référence 40 s + alignement Bridge (<= 305 s) + panne + récupération (<= 360 s) + marge
    cached_unit = "ha01-probe-161-cached-%s" % a.label
    cached_file = "%s/probe161-cached-%s.jsonl" % ("/opt/helios-backups/ha01", a.label)
    cached_ok = False if a.no_cached_probe else start_cached_probe(cached_unit, cached_file, total, say)
    say("sonde .161 avec cache DNS : %s" % ("démarrée" if cached_ok else "NON démarrée"))
    det = D.LiveDetector([O.NET + s for s in O.ETCD_ALL], period=5.0)
    det.start()
    dns = O.DnsPoller()
    dns.start()
    timeline = run.jsonl("timeline.jsonl")
    term_before, leader_before = O.etcd_term_leader()
    etcd_expected = 4 if a.mode == "isolate" else 5
    notready, mem_min, lease_max = {}, {"153": 9999, "155": 9999, "158": 9999}, {}
    host_min, etcd_min, etcd_bad_n = 99999, 99, 0
    abort, injected, restored, armed = None, False, False, False
    t_inject = t_restore = None
    try:
        say("mesure de référence 40 s (détecteur de gels, DNS, sonde avec cache)")
        time.sleep(40)
        if a.mode == "isolate":
            align_bridge(duration, say)
        # Minuterie de sécurité armée JUSTE avant l'injection (l'alignement peut durer ~5 min : armée plus tôt, elle échoirait trop tôt).
        armed = O.deadman_arm(lb, unit, duration + 150, cmd)
        if not armed:
            say("STOP: minuterie de sécurité NON armée sur .%s — aucune injection." % lb)
            abort = "filet de sécurité non armé"
        else:
            say("minuterie de sécurité armée sur .%s (%s) : rétablissement automatique dans %d s si ce script disparaît" % (lb, unit, duration + 150))
            t_inject = time.time()
            ok, out = inject(a.mode, lb, client, say)
            injected = True
            say("INJECTION %s sur .%s : %s" % (a.mode, lb, "effective" if ok else "NON CONFIRMÉE (%s)" % out[:100]))
            if not ok:
                abort = "injection non confirmée"
        t_end = (t_inject or time.time()) + duration
        k = 0
        while abort is None and time.time() < t_end:
            cyc = time.time()
            rec = {"ts": O.iso(), "t_plus_s": int(cyc - t_inject)}
            ns = O.nodes_state()
            rec["nodes"] = ns
            for n, s in ns.items():
                if s != "Ready":
                    notready.setdefault(n, [rec["ts"], rec["ts"]])[1] = rec["ts"]
            h = O.etcd_healthy(eps=tuple(s for s in O.ETCD_ALL if not (a.mode == "isolate" and s == lb)))   # le membre isolé est exclu (6 s de timeout par cycle sinon)
            etcd_min = min(etcd_min, h)
            etcd_bad_n = etcd_bad_n + 1 if h < 4 else 0     # abandon seulement sous 4 membres sains (quorum = 3), sur 2 cycles consécutifs
            rec["etcd"] = h
            hm = O.host_avail_mb()
            host_min = min(host_min, hm)
            rec["host_mb"] = hm
            if k % 3 == 0:
                for s in ("153", "155", "158"):
                    m = O.mem_avail(s)
                    if m is not None:
                        mem_min[s] = min(mem_min[s], m)
                rec["mem"] = dict(mem_min)
            la = lease_ages()
            for n, v in la.items():
                lease_max[n] = max(lease_max.get(n, 0), v)
            rec["lease_age_s"] = la
            runs = {p: O.tail_fail_run(O.PROBE_WIN, p) for p in O.BY_NAME}
            rec["fail_run_s"] = runs
            ages = {d: O.canary_ack_age(d) for d in ("ha01-canary-w1", "ha01-canary-w2")} if k % 2 == 0 else {}
            rec["canary_age_s"] = {d: (None if v is None else round(v, 1)) for d, v in ages.items()}
            rec["p99_fsync_ms"] = {i.split(".")[-1]: round(v * 1000) for i, v in det.current().items() if v is not None}
            timeline.write(json.dumps(rec, ensure_ascii=False) + "\n")
            timeline.flush()
            why = None
            if min(mem_min["155"], mem_min["158"]) < 250:
                why = "MemAvailable d'un worker < 250 Mo"
            elif mem_min["153"] < 250:
                why = "MemAvailable .153 < 250 Mo"
            elif etcd_bad_n >= 2:
                why = "etcd dégradé (%s membres sains) sur 2 cycles consécutifs" % h
            elif hm < 600:
                why = "RAM hôte %s Mo < 600" % hm
            elif max(runs.values()) > 60:
                why = "sonde par nom en échec depuis %ss (vue Windows)" % max(runs.values())
            else:
                for n, (t_first, t_last) in notready.items():
                    if ns.get(n) != "Ready" and O.epoch_of(t_last) - O.epoch_of(t_first) > 120:
                        why = "nœud %s NotReady depuis > 2 min" % n
                for d, v in ages.items():
                    if v is None or v > 120:
                        why = "canari %s muet depuis > 120 s" % d
            if why:
                abort = why
                say("ABANDON: %s" % why)
                break
            k += 1
            d = 5 - (time.time() - cyc)
            if d > 0:
                time.sleep(d)
    finally:
        if injected:
            restored = restore(a.mode, lb, say)
            t_restore = time.time()
            say("RÉTABLISSEMENT %s sur .%s : %s" % (a.mode, lb, "confirmé" if restored else "NON CONFIRMÉ — la minuterie de sécurité s'en chargera"))
        if armed and (restored or not injected):
            O.deadman_disarm(lb, unit)
    if not injected:
        det.stop()
        dns.stop()
        say("aucune injection effectuée (%s) : rien à évaluer." % abort)
        return 4
    # --- récupération
    recovered_s = dns_nominal_s = None
    streak = 0
    dns_back_t = None
    t0r = time.time()
    while time.time() - t0r < 360:
        ns = O.nodes_state()
        ok_lb, _ = O.lb_direct_ok(lb)
        up1, _ = O.haproxy_all_up(lb)
        up2, _ = O.haproxy_all_up(other)
        healthy = ok_lb and up1 and up2 and len(ns) == 3 and all(v == "Ready" for v in ns.values()) and O.etcd_healthy() == 5
        streak = streak + 1 if healthy else 0
        if streak >= 2 and recovered_s is None:
            recovered_s = time.time() - t0r
            say("RÉCUPÉRATION complète en %.0f s" % recovered_s)
        sts = {x: O.dns_state(x) for x in ("160", "162")}
        nominal = all({k: v.get("status") for k, v in (s.get("lbs") or {}).items()} == {"160": "UP", "162": "UP"} for s in sts.values())
        if dns_mode == "enforce":
            nominal = nominal and all(s.get("publish") == [O.NET + O.DNS_PRIMARY] for s in sts.values())
        if nominal and dns_nominal_s is None:
            dns_nominal_s = time.time() - t0r
            say("DNS revenu à l'état nominal en %.0f s" % dns_nominal_s)
        if recovered_s is not None and dns_nominal_s is not None:
            break
        time.sleep(5)
    time.sleep(30)
    t_stop = time.time()
    det.stop()
    dns.stop()
    # --- collecte
    t_a, t_b = t_inject, t_restore + 90
    stalls = list(det.windows())
    prom = D.windows(t_a - 60, t_b)
    stalls += prom or []
    vant = {"Windows": O.window_records_local(O.PROBE_WIN, t_a, t_b), ".161 sans cache": O.window_records_remote("161", O.P161_FILE, t_a, t_b)}
    vant[".161 avec cache"] = O.window_records_remote("161", cached_file, t_a, t_b, sudo=True) if cached_ok else []
    for nm, rs in vant.items():
        run.save("probes-%s.jsonl" % nm.replace(" ", "_").replace(".", ""), "\n".join(json.dumps(r, ensure_ascii=False) for r in rs) + "\n")
    canaries = {}
    logs = {}
    for dep in ("ha01-canary-w1", "ha01-canary-w2"):
        logs[dep] = O.ha01_canary_report.fetch_log(dep)
    ids = O.ha01_canary_report.fetch_ids()
    for dep, lg in logs.items():
        run.save("canary-%s.log" % dep, lg)
        canaries[dep] = O.ha01_canary_report.analyse(lg, ids, t_a, t_b, exclude=stalls, exclude_after_s=60)
    term_after, leader_after = O.etcd_term_leader()
    rc, oomj = O.kubectl("get pods -A -o json", timeout=90)
    n_oom = 0
    try:
        for p in json.loads(oomj)["items"]:
            for cs in p["status"].get("containerStatuses", []):
                lt = cs.get("lastState", {}).get("terminated", {})
                if lt.get("reason") == "OOMKilled" and lt.get("finishedAt", "") >= O.iso(O.datetime.fromtimestamp(t_inject, O.timezone.utc)):
                    n_oom += 1
    except Exception:  # noqa: BLE001
        n_oom = -1
    rc, ev = O.kubectl("get events -A --field-selector reason=Evicted --no-headers")
    mem_ev = [l for l in ev.splitlines() if "memory" in l.lower()]
    dns_switch = dns.first_without("162" if lb == "160" else "160", lb, t_inject)
    dns_back = dns.first_with("162" if lb == "160" else "160", lb, t_restore)
    ctx = {"duration": duration, "lb": lb, "other": other, "mode": a.mode, "stalls": stalls, "vantages": vant, "cached_expected": not a.no_cached_probe, "canaries": canaries,
           "notready": notready, "etcd_expected": etcd_expected, "etcd_min": etcd_min, "term_before": term_before, "term_after": term_after,
           "leader_before": leader_before, "leader_after": leader_after, "mem_min": mem_min, "oom": n_oom, "mem_evictions": mem_ev,
           "host_min": host_min, "recovered_s": recovered_s, "dns_nominal_s": dns_nominal_s, "dns_mode": dns_mode, "abort": abort,
           "dns_switch_s": None if dns_switch is None else dns_switch - t_inject, "dns_back_s": None if dns_back is None else dns_back - t_restore,
           "lease_max": lease_max}
    rows, extra = evaluate(ctx)
    run.save("dns.jsonl", "\n".join(json.dumps(r) for r in dns.records) + "\n")
    run.save("livedetector.json", {"points": [(t, n, {k: v for k, v in p.items()}) for t, n, p in det.points], "windows": det.windows(), "prometheus_windows": prom})
    run.save("bridge-journal.txt", O.bridge_journal(t_inject - 120, t_stop))
    if a.mode == "isolate" and lb == "160":
        rc, kl = O.ssh("153", "sudo -n journalctl -u kubelet --since '%s' --until '%s' --utc --no-pager 2>&1 | grep -iE 'lease|node status|timeout|refused|dial|no route|i/o|heartbeat' | tail -n 60"
                       % (O.datetime.fromtimestamp(t_inject - 30, O.timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                          O.datetime.fromtimestamp(t_restore + 90, O.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")), timeout=60)
        run.save("kubelet-153.txt", kl)
    ok_all = O.write_report(run, "Injection %s sur .%s (%s) — %s -> %s" % (a.mode, lb, a.label, O.iso(O.datetime.fromtimestamp(t_inject, O.timezone.utc)),
                                                                        O.iso(O.datetime.fromtimestamp(t_restore, O.timezone.utc))), rows, extra,
                            {"mode": a.mode, "lb": lb, "label": a.label, "duration_s": duration, "t_inject": t_inject, "t_restore": t_restore})
    say("RÉSULTAT: %d/%d critères satisfaits -> %s" % (sum(1 for _, ok, _ in rows if ok), len(rows), run.dir))
    for c, ok, v in rows:
        say("  %s %-95s %s" % ("OK" if ok else "KO", c[:95], str(v)[:120]))
    if abort:
        return 2
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
