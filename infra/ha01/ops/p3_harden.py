#!/usr/bin/env python3
"""
HA-01 (écart E13) — durcissement de Traefik EN SERVICE contre la perte BRUTALE d'un nœud : Traefik écoute en hostNetwork (8000/8443 sur l'IP du
nœud, HAProxy le joint sans NodePort), nouvelles tentatives rapides (middleware `retry` + dialTimeout 1 s). Cause constatée lors de l'arrêt réel de
.155 (4 nov. 10:16Z) : le contrôle de santé de HAProxy sur le NodePort du nœud SURVIVANT traversait kube-proxy vers le pod Traefik de la VM morte ->
HAProxy déclarait AUSSI le survivant DOWN -> 44 s de coupure web. (`externalTrafficPolicy: Local` essayé 2 fois : instable, abandonné.)

  python p3_harden.py [--dry-run] [--observe 120]     # applique k8s/traefik-shadow.yaml (hostNetwork + retry) au Traefik en service
  python p3_harden.py --rollback                       # retour à la version précédente (rollout undo + PSS restricted + retrait de la ConfigMap)

Étape 1 seulement : HAProxy continue d'utiliser les NodePorts 32080/32443 (toujours valides). Étape 2 : p3_switch.py --to traefik-host (ports 8000/8443).
Sécurité : préflight · sauvegarde des objets · validation serveur · déploiement progressif (un pod reste servi) · vérifications (pods, hostNetwork, ports
directs 8000/8443, serveurs HAProxy UP et stables) · observation avec abandon automatique -> RETOUR ARRIÈRE AUTOMATIQUE.
Code retour : 0 OK · 1 échec (retour arrière effectué) · 3 préflight KO (rien fait).
"""
import argparse
import os
import sys
import time

import ha01_ops as O

MANIFEST = os.path.normpath(os.path.join(O.HERE, "..", "k8s", "traefik-shadow.yaml"))


def rollback(say):
    out = []
    for c in ("-n traefik rollout undo deploy/traefik",
              "label ns traefik pod-security.kubernetes.io/enforce=restricted --overwrite",
              "-n traefik delete cm traefik-dynamic --ignore-not-found"):
        rc, o = O.kubectl(c, timeout=90)
        say("kubectl %s -> rc=%s %s" % (c.split(" -p")[0], rc, o.replace("\n", " | ")[:100]))
        out.append(rc)
    rc, o = O.kubectl("-n traefik rollout status deploy/traefik --timeout=180s", timeout=200)
    say("rollout après retour arrière -> %s" % o.replace("\n", " | ")[:120])
    return all(r == 0 for r in out)


def http_status(ip, port, tls=False, host="helios.orange.tn"):
    import socket
    import ssl
    try:
        s = socket.create_connection((O.NET + ip, port), timeout=4)
        s.settimeout(4)
        if tls:
            c = ssl.create_default_context()
            c.check_hostname = False
            c.verify_mode = ssl.CERT_NONE
            s = c.wrap_socket(s, server_hostname=host)
        s.sendall(b"GET / HTTP/1.1\r\nHost: " + host.encode() + b"\r\nConnection: close\r\n\r\n")
        line = s.recv(64).decode("latin1", "replace").split(" ")
        s.close()
        return int(line[1]) if len(line) > 1 and line[1].isdigit() else 0
    except Exception:  # noqa: BLE001
        return 0


def state(say):
    rc, pods = O.kubectl("-n traefik get pods -o wide --no-headers")
    nodes = sorted(l.split()[6] for l in pods.splitlines() if len(l.split()) > 6 and " Running " in " " + l + " " and "1/1" in l)
    rc, pol = O.kubectl("-n traefik get svc traefik -o jsonpath='{.spec.externalTrafficPolicy}'")
    return nodes, pol.replace("'", "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--rollback", action="store_true")
    ap.add_argument("--observe", type=int, default=120)
    a = ap.parse_args()
    run = O.Run("p3-harden")
    say = run.say
    if a.rollback:
        ok = rollback(say)
        say("retour arrière : %s" % ("OK" if ok else "à vérifier"))
        return 0 if ok else 1
    chk = []

    def add(name, ok, detail):
        chk.append({"check": name, "ok": bool(ok), "detail": detail})
        say("PREFLIGHT %-72s %s  %s" % (name[:72], "OK " if ok else "KO ", str(detail)[:100]))
    ns = O.nodes_state()
    add("3 nœuds Ready, etcd 5/5", len(ns) == 3 and all(v == "Ready" for v in ns.values()) and O.etcd_healthy() == 5, ns)
    st = O.cnpg_state()
    add("CNPG sain 2/2, aucun pod non sain", st.startswith("Cluster in healthy state") and st.endswith("|2") and not O.unhealthy_pods(), st)
    nodes, pol = state(say)
    add("Traefik : 2 pods Ready, un par worker, Service en Cluster (état avant)", nodes == ["k8s-worker1", "k8s-worker2"], {"pods": nodes, "policy": pol})
    for lb in ("160", "162"):
        ok, d = O.lb_direct_ok(lb)
        up, bad = O.haproxy_all_up(lb)
        add("LB .%s sert en direct, serveurs UP" % lb, ok and up, "%s %s" % (d, bad))
    add("harnais actif", O.file_age(O.PROBE_WIN) < 15 and O.file_age(O.SAMPLES) < 60, "%.0fs/%.0fs" % (O.file_age(O.PROBE_WIN), O.file_age(O.SAMPLES)))
    h = O.host_avail_mb()
    add("RAM hôte >= 2000 Mo", h >= 2000, h)
    ws = O.ha01_hoststall.windows(time.time() - 300, time.time())
    add("aucun gel d'E/S de l'hôte dans les 5 dernières minutes", ws == [], O.ha01_hoststall.describe(ws))
    run.save("preflight.json", chk)
    if not all(c["ok"] for c in chk):
        say("STOP: préconditions non satisfaites — aucune action.")
        return 3
    manifest = open(MANIFEST, encoding="utf-8").read()
    rc, out = O.ssh("155", "kubectl --request-timeout=60s apply --dry-run=server -f -", timeout=120, stdin=manifest)
    say("validation serveur (dry-run) -> rc=%s %s" % (rc, out.replace("\n", " | ")[:260]))
    if rc != 0:
        say("STOP: manifeste refusé par le serveur — aucune action.")
        return 3
    if a.dry_run:
        say("dry-run: préconditions et validation serveur OK, rien n'est appliqué.")
        return 0
    rc, bk = O.kubectl("-n traefik get deploy,svc,cm -o yaml", timeout=90)
    run.save("sauvegarde-avant.yaml", bk)
    t0 = time.time()
    rc, out = O.ssh("155", "kubectl --request-timeout=60s apply -f -", timeout=120, stdin=manifest)
    say("APPLY -> rc=%s %s" % (rc, out.replace("\n", " | ")[:200]))
    run.save("apply.txt", out)
    rc, out = O.kubectl("-n traefik rollout status deploy/traefik --timeout=240s", timeout=260)
    say("rollout -> %s" % out.replace("\n", " | ")[-160:])
    bad = None
    nodes, pol = state(say)
    if nodes != ["k8s-worker1", "k8s-worker2"]:
        bad = "pods Traefik non conformes après le déploiement: %s" % nodes
    elif O.kubectl("-n traefik get deploy traefik -o jsonpath='{.spec.template.spec.hostNetwork}'")[1].replace("'", "") != "true":
        bad = "hostNetwork non actif sur le Deployment"
    else:
        rc, args = O.kubectl("-n traefik get deploy traefik -o jsonpath='{.spec.template.spec.containers[0].args}'")
        if "serversTransport.forwardingTimeouts.dialTimeout=1s" not in args or "ha01-retry@file" not in args:
            bad = "arguments Traefik absents"
        else:
            for ip in ("155", "158"):          # Traefik répond-il DIRECTEMENT sur l'IP du nœud (sans NodePort) ?
                for port, tls in ((8000, False), (8443, True)):
                    st = http_status(ip, port, tls)
                    if st not in (200, 301, 302, 404):
                        bad = "Traefik ne répond pas sur .%s:%d (statut %s)" % (ip, port, st)
    if not bad:
        # Le changement de politique du Service (Cluster -> Local) et le déploiement progressif provoquent une TRANSITION d'environ 50 s
        # (reprogrammation de kube-proxy, purge des connexions suivies) pendant laquelle HAProxy voit des serveurs passer DOWN/UP : constatée
        # au 1er essai (UP stables seulement vers +60 s). On attend donc que les serveurs des DEUX LB soient UP en continu pendant 25 s,
        # au plus 150 s ; seule une indisponibilité client prolongée (sonde par nom en échec > 90 s) déclenche le retour arrière pendant la transition.
        stable_since, t_wait = None, time.time()
        while time.time() - t_wait < 150:
            ups = [O.haproxy_all_up(lb) for lb in ("162", "160")]
            if all(u for u, _ in ups):
                stable_since = stable_since or time.time()
                if time.time() - stable_since >= 25:
                    break
            else:
                stable_since = None
            if max(O.tail_fail_run(O.PROBE_WIN, p) for p in O.BY_NAME) > 90:
                bad = "sonde par nom en échec depuis plus de 90 s pendant la transition"
                break
            time.sleep(3)
        else:
            bad = "serveurs HAProxy non stables 150 s après le déploiement: %s" % [b for _, b in ups]
        if not bad:
            say("transition terminée : serveurs UP en continu depuis 25 s, %.0f s après le déploiement" % (time.time() - t0))
    rc, logs = O.kubectl("-n traefik logs deploy/traefik --tail=80")
    run.save("traefik.log", logs)
    errs = [l for l in logs.splitlines() if "level=error" in l.lower() or "\"level\":\"error\"" in l.lower() or "unknown flag" in l.lower() or "cannot" in l.lower()]
    if errs and not bad:
        bad = "erreurs dans les journaux Traefik: %s" % errs[0][:160]
    t_obs = time.time()
    while not bad and time.time() - t_obs < a.observe:
        runs = {p: O.tail_fail_run(O.PROBE_WIN, p) for p in O.BY_NAME}
        if max(runs.values()) > 15:
            bad = "sonde par nom en échec depuis %ss" % max(runs.values())
        elif O.host_avail_mb() < 600:
            bad = "RAM hôte < 600 Mo"
        time.sleep(5)
    if bad:
        say("ÉCHEC: %s -> RETOUR ARRIÈRE" % bad)
        say("retour arrière: %s" % ("OK" if rollback(say) else "À VÉRIFIER"))
        return 1
    say("DURCISSEMENT APPLIQUÉ en %.0f s : Traefik en hostNetwork (8000/8443), retry + dialTimeout 1 s, 2 pods Ready, serveurs HAProxy UP, observation %d s propre." % (time.time() - t0, a.observe))
    return 0


if __name__ == "__main__":
    sys.exit(main())
