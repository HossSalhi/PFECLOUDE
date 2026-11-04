#!/usr/bin/env python3
"""
HA-01 Phase 2b, étape 1 — passe UN LB du DNS statique au DNS à santé contrôlée (mode ENFORCE), avec vérifications et RETOUR
ARRIÈRE AUTOMATIQUE si un client par nom échoue.

  python p2b_enforce.py --lb 162 [--observe 180] [--dry-run]      # puis --lb 160
  python p2b_enforce.py --lb 162 --rollback                        # retour au DNS statique (host-record d'origine)

Ce que fait le passage (scripts existants dns/enforce-dns-health.sh et dns/rollback-dns-health.sh, inchangés) :
  1. écrit d'abord le fichier hostsdir avec les 2 IP (aucune coupure de résolution) ; 2. remplace les host-record statiques par
  hostsdir ; 3. valide la config, redémarre dnsmasq de CE LB ; 4. met le mode du contrôleur sur « enforce ».
Ensuite le contrôleur ne publie plus que .160 tant qu'elle est saine (sinon .162 ; jamais zéro enregistrement).

Vérifié ici : état avant (shadow, les 2 LB UP, dnsmasq publie les 2 IP) · après (mode enforce, fichier hostsdir = .160 seule,
dnsmasq du LB répond .160 seule, TTL 30, l'AUTRE LB inchangé) · observation (--observe s) : aucune sonde par nom en échec
(chiffres bruts ET hors gel d'E/S hôte, écart E6). Code retour : 0 OK · 1 retour arrière effectué · 3 préflight KO (rien fait).
"""
import argparse
import os
import shutil
import subprocess
import sys
import time

import ha01_ops as O

DNS_DIR = os.path.normpath(os.path.join(O.HERE, "..", "dns"))


def git_bash():
    """bash de Git for Windows (jamais celui de WSL : autre environnement, sans les clés SSH)."""
    for p in (r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files (x86)\Git\bin\bash.exe"):
        if os.path.exists(p):
            return p
    w = shutil.which("bash")
    return w if w and "system32" not in w.lower() else None


def run_script(name, lb, say):
    b = git_bash()
    if not b:
        say("bash de Git for Windows introuvable")
        return 127, ""
    r = subprocess.run([b, os.path.join(DNS_DIR, name), lb], capture_output=True, text=True, timeout=180, encoding="utf-8", errors="replace",
                       cwd=DNS_DIR)
    say("%s %s -> code %s" % (name, lb, r.returncode))
    for l in (r.stdout + r.stderr).strip().splitlines()[-8:]:
        say("   | " + l[:160])
    return r.returncode, r.stdout


def state_ok(lb, want_mode):
    s = O.dns_state(lb)
    return s.get("mode") == want_mode and {k: v.get("status") for k, v in (s.get("lbs") or {}).items()} == {"160": "UP", "162": "UP"}, s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lb", required=True, choices=["160", "162"])
    ap.add_argument("--observe", type=int, default=180)
    ap.add_argument("--rollback", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    lb, other = a.lb, ("162" if a.lb == "160" else "160")
    run = O.Run("p2b-%s-%s" % ("rollback" if a.rollback else "enforce", lb))
    say = run.say
    chk = []

    def add(name, ok, detail):
        chk.append({"check": name, "ok": bool(ok), "detail": detail})
        say("PREFLIGHT %-72s %s  %s" % (name[:72], "OK " if ok else "KO ", str(detail)[:100]))
    ns = O.nodes_state()
    add("3 nœuds Ready, etcd 5/5", len(ns) == 3 and all(v == "Ready" for v in ns.values()) and O.etcd_healthy() == 5, ns)
    add("harnais actif", O.file_age(O.PROBE_WIN) < 15 and O.file_age(O.SAMPLES) < 60, "%.0fs/%.0fs" % (O.file_age(O.PROBE_WIN), O.file_age(O.SAMPLES)))
    want_before = "enforce" if a.rollback else "shadow"
    ok, s = state_ok(lb, want_before)
    add("contrôleur DNS de .%s en mode %s, les 2 LB UP" % (lb, want_before), ok, {"mode": s.get("mode"), "publish": s.get("publish")})
    add("bash de Git for Windows disponible", bool(git_bash()), git_bash())
    ans = O.dns_answers(lb)
    add("dnsmasq .%s répond" % lb, all(v[0] for v in ans.values()), ans)
    ws = O.ha01_hoststall.windows(time.time() - 300, time.time())
    add("aucun gel d'E/S de l'hôte dans les 5 dernières minutes", ws == [], O.ha01_hoststall.describe(ws))
    run.save("preflight.json", chk)
    if not all(c["ok"] for c in chk):
        say("STOP: préconditions non satisfaites — aucune action effectuée.")
        return 3
    if a.dry_run:
        say("dry-run: préconditions OK, aucune action.")
        return 0

    other_before = O.dns_answers(other)          # l'AUTRE LB ne doit pas changer pendant ce passage
    t0 = time.time()
    script = "rollback-dns-health.sh" if a.rollback else "enforce-dns-health.sh"
    rc, _ = run_script(script, lb, say)
    verdict = []
    time.sleep(8)       # laisse le contrôleur relire le mode (cycle de 3 s) et réécrire son fichier
    ok, s = state_ok(lb, "shadow" if a.rollback else "enforce")
    verdict.append(("contrôleur de .%s en mode %s" % (lb, "shadow" if a.rollback else "enforce"), ok, s.get("mode")))
    ans = O.dns_answers(lb)
    if a.rollback:
        verdict.append(("dnsmasq .%s publie de nouveau les 2 IP (statique)" % lb, all(v[0] == ["192.168.148.160", "192.168.148.162"] for v in ans.values()), ans))
    else:
        want = s.get("publish") or []        # l'IP que le contrôleur publie seule (ordre configurable : HELIOS_DNS_ORDER)
        verdict.append(("dnsmasq .%s publie UNE seule IP (%s) pour les 2 noms, TTL 30" % (lb, want), len(want) == 1 and all(v[0] == want for v in ans.values()), ans))
        rc2, hosts = O.ssh(lb, "sudo -n cat /etc/dnsmasq-helios.d/helios.hosts", timeout=20)
        ip_lines = [l for l in hosts.splitlines() if l.strip() and not l.startswith("#")]
        verdict.append(("fichier hostsdir de .%s = cette IP seule" % lb, len(want) == 1 and len(ip_lines) == 1 and ip_lines[0].startswith(want[0]), hosts.replace("\n", " | ")))
    ans_o = O.dns_answers(other)
    verdict.append(("l'AUTRE LB (.%s) n'a pas changé de réponses DNS" % other, ans_o == other_before, {"avant": other_before, "après": ans_o}))
    verdict.append(("script %s terminé sans erreur" % script, rc == 0, rc))
    for c, ok, d in verdict:
        say("VÉRIF %-62s %s  %s" % (c[:62], "OK " if ok else "KO ", str(d)[:120]))
    # observation : aucun échec par nom
    t_obs = time.time()
    say("observation %d s : sondes par nom (3 points de vue : Windows, .161)" % a.observe)
    bad = None
    while time.time() - t_obs < a.observe:
        runs = {p: O.tail_fail_run(O.PROBE_WIN, p) for p in O.BY_NAME}
        if max(runs.values()) >= 15:
            bad = "sonde par nom en échec depuis %ss (vue Windows)" % max(runs.values())
            break
        time.sleep(5)
    t1 = time.time()
    stalls = list(O.ha01_hoststall.windows(t0 - 30, t1) or [])
    win = O.window_records_local(O.PROBE_WIN, t0, t1)
    a161 = O.window_records_remote("161", O.P161_FILE, t0, t1)
    for nm, recs in (("Windows", win), (".161", a161)):
        an = O.analyse_vantage(recs, stalls)
        for p in O.BY_NAME:
            v = an["hors"].get(p)
            ok = bool(v) and v["availability_pct"] >= 99.5 and v["longest_failure_s"] <= 10
            verdict.append(("[%s] %s >= 99,5 %% et série <= 10 s pendant le passage (hors gels)" % (nm, p), ok, "hors gel: %s | BRUT: %s" % (O.fmt_probe(an["hors"], p), O.fmt_probe(an["raw"], p))))
    all_ok = all(ok for _, ok, _ in verdict) and bad is None
    O.write_report(run, "%s DNS .%s" % ("Retour arrière" if a.rollback else "Passage en enforce", lb), verdict,
                   "Gels d'E/S hôte : %s\n" % O.ha01_hoststall.describe(stalls), {"lb": lb, "rollback": a.rollback, "abort": bad})
    if not all_ok and not a.rollback:
        say("ÉCHEC (%s) -> RETOUR ARRIÈRE AUTOMATIQUE de .%s" % (bad or "vérification KO", lb))
        run_script("rollback-dns-health.sh", lb, say)
        return 1
    say("TERMINÉ : %s" % ("OK" if all_ok else "à examiner"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
