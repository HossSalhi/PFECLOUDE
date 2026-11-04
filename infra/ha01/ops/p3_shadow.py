#!/usr/bin/env python3
"""
HA-01 Phase 3 — Traefik (contrôleur maintenu) en SHADOW à côté d'ingress-nginx : déploiement, recette, retrait.

  python p3_shadow.py apply [--dry-run]    # préflight, validation serveur, déploiement, vérifications, observation 120 s
  python p3_shadow.py accept               # recette complète (p3_shadow_accept.py : 200/TLS/redirect/SSE/charge) -> p3-accept-<ts>.md
  python p3_shadow.py status               # état lecture seule
  python p3_shadow.py rollback             # retire TOUT (namespace traefik, ClusterRole/Binding, Role/RoleBinding dans pfe)

Le shadow n'écoute que sur les NodePorts 32080/32443 : HAProxy continue d'utiliser 31756/31411 (ingress-nginx) tant que la
bascule (p3_switch.py) n'a pas eu lieu. Coût mémoire : 2 pods (64 Mi demandés / 192 Mi max chacun) sur les workers.
Code retour : 0 OK · 1 échec (retour arrière automatique pour `apply`) · 3 préflight KO (rien fait).
"""
import json
import os
import subprocess
import sys
import time

import ha01_ops as O

MANIFEST = os.path.normpath(os.path.join(O.HERE, "..", "k8s", "traefik-shadow.yaml"))
ACCEPT = os.path.join(O.HERE, "p3_shadow_accept.py")


def rollback(say):
    cmds = ["delete namespace traefik --ignore-not-found --wait=true --timeout=120s",
            "delete clusterrolebinding ha01-traefik-ingressclass --ignore-not-found",
            "delete clusterrole ha01-traefik-ingressclass --ignore-not-found",
            "-n pfe delete rolebinding ha01-traefik-reader --ignore-not-found",
            "-n pfe delete role ha01-traefik-reader --ignore-not-found"]
    for c in cmds:
        rc, out = O.kubectl(c, timeout=150)
        say("kubectl %s -> %s %s" % (c.split(" --")[0], rc, out.replace("\n", " | ")[:100]))
    rc, out = O.kubectl("get ns traefik --no-headers")
    return "NotFound" in out or out.strip() == "" or rc != 0


def status(say):
    rc, out = O.kubectl("-n traefik get deploy,pods,svc,pdb -o wide --no-headers")
    say(out or "(namespace traefik absent)")
    return 0


def accept(say):
    run_file = os.path.join(O.RES, "p3-accept-%s.md" % O.now().strftime("%Y%m%dT%H%M%SZ"))
    r = subprocess.run([sys.executable, ACCEPT, "--sse-seconds", "120"], capture_output=True, text=True, timeout=900, encoding="utf-8", errors="replace")
    body = (r.stdout + r.stderr)
    verdict = "PASS" if r.returncode == 0 else "FAIL"
    with open(run_file, "w", encoding="utf-8") as fh:
        fh.write("# Recette du shadow Traefik — %s (%s)\n\n```\n%s\n```\n" % (O.iso(), verdict, body))
    say("recette %s -> %s" % (verdict, run_file))
    for l in body.strip().splitlines()[-14:]:
        say("   | " + l[:170])
    return r.returncode


def apply(a, say, run):
    chk = []

    def add(name, ok, detail):
        chk.append({"check": name, "ok": bool(ok), "detail": detail})
        say("PREFLIGHT %-70s %s  %s" % (name[:70], "OK " if ok else "KO ", str(detail)[:100]))
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
        rc, out = O.ssh(w, "sudo -n crictl images 2>/dev/null | grep -c 'traefik'", timeout=30)
        add("image traefik préchargée sur .%s" % w, out.strip().isdigit() and int(out.strip()) >= 1, out)
    rc, out = O.kubectl("get ns traefik --no-headers")
    add("namespace traefik absent (premier déploiement)", rc != 0 or out.strip() == "", out[:80])
    rc, out = O.kubectl("get svc -A -o jsonpath='{range .items[*]}{range .spec.ports[*]}{.nodePort}{\" \"}{end}{end}'")
    ports = out.replace("'", "").split()
    add("NodePorts 32080 et 32443 libres", "32080" not in ports and "32443" not in ports, "32080/32443 absents" if ("32080" not in ports and "32443" not in ports) else ports)
    rc, out = O.kubectl("-n pfe get ingress helios-ingress -o jsonpath='{.metadata.name}:{.spec.ingressClassName}'")
    add("Ingress pfe/helios-ingress présent", out.replace("'", "").startswith("helios-ingress"), out)
    rc, out = O.kubectl("-n ingress-nginx get pods --no-headers")
    add("ingress-nginx : 2 pods Ready (inchangés)", out.count(" Running ") == 2 and out.count("1/1") == 2, out.replace("\n", " | ")[:140])
    ws = O.ha01_hoststall.windows(time.time() - 300, time.time())
    add("aucun gel d'E/S de l'hôte dans les 5 dernières minutes", ws == [], O.ha01_hoststall.describe(ws))
    run.save("preflight.json", chk)
    if not all(c["ok"] for c in chk):
        say("STOP: préconditions non satisfaites — aucune action effectuée.")
        return 3
    manifest = open(MANIFEST, encoding="utf-8").read()
    rc, out = O.ssh("155", "kubectl --request-timeout=60s apply --dry-run=client -f -", timeout=120, stdin=manifest)
    say("validation client (schéma) -> rc=%s %s" % (rc, out.replace("\n", " | ")[:200]))
    if rc != 0:
        say("STOP: le manifeste est invalide — aucune action.")
        return 3
    if a.dry_run:
        say("dry-run: préconditions et validation client OK, rien n'est appliqué. (La validation SERVEUR complète n'est possible qu'après la "
            "création du namespace traefik, inoffensive : elle a lieu au lancement réel, avec retrait si elle échoue.)")
        return 0
    t0 = time.time()
    # le namespace d'abord (inoffensif), puis validation SERVEUR du manifeste complet (impossible avant : le namespace n'existe pas)
    ns_doc = manifest.split("\n---\n")[0]
    rc, out = O.ssh("155", "kubectl --request-timeout=60s apply -f -", timeout=120, stdin=ns_doc)
    say("namespace traefik -> rc=%s %s" % (rc, out.replace("\n", " | ")[:100]))
    rc, out = O.ssh("155", "kubectl --request-timeout=60s apply --dry-run=server -f -", timeout=120, stdin=manifest)
    say("validation serveur (dry-run) -> rc=%s %s" % (rc, out.replace("\n", " | ")[:240]))
    if rc != 0:
        say("STOP: le manifeste est refusé par le serveur -> retrait du namespace.")
        rollback(say)
        return 1
    rc, out = O.ssh("155", "kubectl --request-timeout=60s apply -f -", timeout=120, stdin=manifest)
    say("APPLY -> rc=%s" % rc)
    run.save("apply.txt", out)
    rc, out = O.kubectl("-n traefik rollout status deploy/traefik --timeout=180s", timeout=200)
    say("rollout -> %s" % out.replace("\n", " | ")[:140])
    bad = None
    rc, pods = O.kubectl("-n traefik get pods -o wide --no-headers")
    run.save("pods.txt", pods)
    nodes = sorted(l.split()[6] for l in pods.splitlines() if len(l.split()) > 6)
    if not (len(nodes) == 2 and nodes == ["k8s-worker1", "k8s-worker2"] and pods.count(" Running ") == 2 and pods.count("1/1") == 2):
        bad = "pods Traefik non conformes (2 Ready, un par worker): %s" % pods.replace("\n", " | ")[:200]
    rc, logs = O.kubectl("-n traefik logs deploy/traefik --tail=60")
    run.save("traefik.log", logs)
    errs = [l for l in logs.splitlines() if ("level=error" in l.lower() or "\"level\":\"error\"" in l.lower() or "forbidden" in l.lower())]
    if errs and not bad:
        bad = "erreurs dans les journaux Traefik: %s" % errs[0][:160]
    obs_end = time.time() + 120
    while not bad and time.time() < obs_end:
        runs = {p: O.tail_fail_run(O.PROBE_WIN, p) for p in O.BY_NAME}
        mw = [O.mem_avail(w) or 0 for w in ("155", "158")]
        hm = O.host_avail_mb()
        if max(runs.values()) > 15:
            bad = "sonde par nom en échec depuis %ss" % max(runs.values())
        elif min(mw) < 250:
            bad = "MemAvailable d'un worker < 250 Mo (%s)" % mw
        elif hm < 600:
            bad = "RAM hôte %s Mo < 600" % hm
        time.sleep(10)
    if bad:
        say("ÉCHEC: %s -> RETOUR ARRIÈRE" % bad)
        say("retrait complet : %s" % ("OK" if rollback(say) else "À VÉRIFIER"))
        return 1
    say("SHADOW OPÉRATIONNEL en %.0f s : 2 pods Traefik Ready (un par worker), NodePorts 32080/32443, ingress-nginx inchangé." % (time.time() - t0))
    say("suite : `p3_shadow.py accept` (recette complète) puis `p3_switch.py --lb 162 --to traefik`")
    return 0


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["apply", "status", "accept", "rollback"])
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    run = O.Run("p3-shadow-%s" % a.action)
    say = run.say
    if a.action == "apply":
        return apply(a, say, run)
    if a.action == "status":
        return status(say)
    if a.action == "accept":
        return accept(say)
    ok = rollback(say)
    say("rollback: %s" % ("OK" if ok else "à vérifier"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
