#!/usr/bin/env python3
"""
HA-01 (écart E16) — correctifs de la FENÊTRE « pod mort encore dans les endpoints » (~45 s après la perte brutale d'un worker), chemin web.
Chaque saut qui répartit sur les pods du nœud mort doit (1) ne pas réutiliser une connexion keep-alive vers lui, (2) échouer vite, (3) retenter ailleurs.

  python p4_webchange.py --what traefik-provider [--observe 90] [--dry-run]  # B : options DU FOURNISSEUR Ingress NGINX de Traefik (et non serversTransport,
                                                                            #     sans effet sur ses services, mesuré le 4 nov.) : proxyconnecttimeout=1 (défaut 60 s)
                                                                            #     + proxynextupstreamtries=4 (défaut 3) ; retire le drapeau sans effet maxIdleConnsPerHost=-1
  python p4_webchange.py --what frontend-nginx [--observe 90] [--dry-run]  # C : nginx du frontend -> Service headless `backend-headless` (IP des pods, plusieurs
                                                                            #     adresses) + proxy_connect_timeout 2s + proxy_next_upstream : une requête qui tombe
                                                                            #     sur le pod mort est rejouée sur le pod vivant (avant : IP de cluster unique, 10 s d'attente, aucune reprise)
  python p4_webchange.py --what traefik-provider|frontend-nginx --rollback  # retour arrière = `kubectl rollout undo` (rien d'autre n'est défait)

Garde-fous : préflight · sauvegarde YAML des objets · validation serveur (dry-run) · C : `nginx -t` dans un pod du frontend AVANT tout changement · déploiement
progressif (un pod reste servi) · observation avec abandon automatique -> RETOUR ARRIÈRE (rollout undo). Aucun commit.
Code retour : 0 OK · 1 échec (retour arrière fait) · 3 préflight KO (rien fait).
"""
import argparse
import json
import re
import sys
import time

import ha01_ops as O

# Écart E16 : le fournisseur « kubernetesingressnginx » de Traefik 3.7 fixe ses PROPRES délais (connexion 60 s par défaut, 3 tentatives) ; les options
# --serversTransport.* posées à l'écart E13 ne s'appliquent pas à ses services (mesuré : avec un pod frontend muet, la moitié des requêtes restait bloquée > 8 s).
TRAEFIK_ADD = ["--providers.kubernetesingressnginx.proxyconnecttimeout=1", "--providers.kubernetesingressnginx.proxynextupstreamtries=4"]
TRAEFIK_DEL = ["--serversTransport.maxIdleConnsPerHost=-1"]
NEW_CM = "frontend-nginx-conf-ha01"
NEW_CM2 = "frontend-nginx-conf-ha01-v2"
OLD_CM = "frontend-nginx-conf"
HEADLESS = {"apiVersion": "v1", "kind": "Service",
            "metadata": {"name": "backend-headless", "namespace": "pfe", "labels": {"app": "backend", "ha01": "e16"}},
            "spec": {"clusterIP": "None", "selector": {"app": "backend"}, "ports": [{"name": "http", "port": 8080, "targetPort": 8080}]}}


def kube_apply(obj, dry=False):
    rc, out = O.ssh("155", "kubectl --request-timeout=60s apply %s-f -" % ("--dry-run=server " if dry else ""), timeout=120, stdin=json.dumps(obj))
    if rc in (255, 124):
        rc, out = O.ssh("158", "kubectl --request-timeout=60s apply %s-f -" % ("--dry-run=server " if dry else ""), timeout=120, stdin=json.dumps(obj))
    return rc, out


def _sub(pat, rep, text, expect=1):
    new, n = re.subn(pat, rep, text)
    if n != expect:
        raise ValueError("motif %r trouvé %d fois (attendu %d)" % (pat, n, expect))
    return new


def transform_nginx_v2(conf):
    """v2 (mesures sous charge du 4 nov. 14:07Z : web 3,7 % / api_app 7,1 % d'échecs, médiane api_app 2 s) :
    - résolution DNS : valid=30s (comme d'origine) et resolver_timeout 1s. Avec valid=5s chaque worker interroge le DNS toutes les 5 s et une requête sur deux tombe sur le
      CoreDNS du nœud mort -> une requête perdue à chaque rafraîchissement ;
    - proxy_connect_timeout 1s (LAN : une connexion saine s'établit en quelques ms) : un essai sur le pod mort coûte 1 s au lieu de 2 s ;
    - keepalive_timeout 0 : nginx ferme chaque connexion après la réponse, donc Traefik n'entretient AUCUNE connexion persistante vers un pod frontend. Une requête écrite sur
      une connexion persistante d'un pair mort n'est jamais rejouée par le middleware retry (« request already written ») et reste bloquée jusqu'au délai du client."""
    c = conf.replace("\r\n", "\n")
    c = _sub(r"resolver 10\.96\.0\.10 valid=5s ipv6=off;\n    resolver_timeout 2s;", "resolver 10.96.0.10 valid=30s ipv6=off;\n    resolver_timeout 1s;", c)
    c = _sub(r"proxy_connect_timeout 2s;", "proxy_connect_timeout 1s;", c, expect=2)
    c = _sub(r"(    proxy_next_upstream_timeout 5s;\n)", r"\1    # E16 v2 : pas de connexion persistante vers Traefik (cf. transform_nginx_v2 dans p4_webchange.py).\n    keepalive_timeout 0;\n", c)
    return c


FRONT = {}      # rempli après la définition de transform_nginx : what -> (ConfigMap de base, nouveau ConfigMap, transformation)


def transform_nginx(conf):
    """Applique les changements à la conf vivante ; lève ValueError si la forme n'est pas celle attendue (aucune supposition)."""
    sub = _sub
    c = conf.replace("\r\n", "\n")
    c = sub(r"resolver 10\.96\.0\.10 valid=30s ipv6=off;", "resolver 10.96.0.10 valid=5s ipv6=off;\n    resolver_timeout 2s;", c)
    c = sub(r'set \$backend_upstream "backend\.pfe\.svc\.cluster\.local:8080";',
            'set $backend_upstream "backend-headless.pfe.svc.cluster.local:8080";\n'
            "    # E16 : le nom headless renvoie l'IP de CHAQUE pod backend prêt ; connexion vers un pod mort -> abandon après 2 s puis essai sur l'autre pod.\n"
            "    proxy_connect_timeout 2s;\n"
            "    proxy_next_upstream error timeout;\n"
            "    proxy_next_upstream_tries 2;\n"
            "    proxy_next_upstream_timeout 5s;", c)
    c = sub(r"proxy_connect_timeout 10s;", "proxy_connect_timeout 2s;", c)
    return c


FRONT.update({"frontend-nginx": (OLD_CM, NEW_CM, transform_nginx), "frontend-nginx-v2": (NEW_CM, NEW_CM2, transform_nginx_v2)})


def preflight(run, what, say):
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
    for w in ("155", "158"):
        m = O.mem_avail(w)
        add(".%s MemAvailable >= 600 Mo" % w, (m or 0) >= 600, m)
    for lb in ("160", "162"):
        ok, d = O.lb_direct_ok(lb)
        up, bad = O.haproxy_all_up(lb)
        add("LB .%s sert en direct, serveurs UP" % lb, ok and up, "%s %s" % (d, bad))
    for hst in ("160", "162", "158"):
        rc, out = O.ssh(hst, "sudo -n sh -c \"iptables -S OUTPUT; iptables -S FORWARD\" 2>/dev/null | grep -c ha01-pbh", timeout=25)
        add(".%s : aucune règle d'injection ha01-pbh" % hst, out.strip().splitlines()[-1] == "0", out)
    ws = O.ha01_hoststall.windows(time.time() - 300, time.time())
    add("aucun gel d'E/S de l'hôte dans les 5 dernières minutes", ws == [], O.ha01_hoststall.describe(ws))
    if what == "traefik-provider":
        rc, out = O.kubectl("-n traefik get pods --no-headers")
        add("Traefik : 2 pods Ready", out.count(" Running ") == 2 and out.count("1/1") == 2, out.replace("\n", " | ")[:120])
        rc, args = O.kubectl("-n traefik get deploy traefik -o jsonpath='{.spec.template.spec.containers[0].args}'")
        add("options du fournisseur pas encore présentes", not any(x in args for x in TRAEFIK_ADD), "")
    else:
        rc, out = O.kubectl("-n pfe get pods -l app=frontend --no-headers")
        add("frontend : 2 pods Ready", out.count(" Running ") == 2 and out.count("1/1") == 2, out.replace("\n", " | ")[:120])
        rc, out = O.kubectl("-n pfe get pods -l app=backend --no-headers")
        add("backend : 2 pods Ready", out.count(" Running ") == 2 and out.count("1/1") == 2, out.replace("\n", " | ")[:120])
        rc, vol = O.kubectl("-n pfe get deploy frontend -o jsonpath='{.spec.template.spec.volumes[0].configMap.name}'")
        add("frontend utilise le ConfigMap de base attendu (%s)" % FRONT[what][0], vol.replace("'", "") == FRONT[what][0], vol)
    return chk


def rollout_undo(ns, dep, say):
    rc, o = O.kubectl("-n %s rollout undo deploy/%s" % (ns, dep), timeout=90)
    say("rollout undo %s/%s -> rc=%s %s" % (ns, dep, rc, o.replace("\n", " | ")[:100]))
    rc, o = O.kubectl("-n %s rollout status deploy/%s --timeout=240s" % (ns, dep), timeout=260)
    say("rollout après retour arrière -> %s" % o.replace("\n", " | ")[-110:])
    return rc == 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--what", required=True, choices=["traefik-provider", "frontend-nginx", "frontend-nginx-v2"])
    ap.add_argument("--observe", type=int, default=90)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--rollback", action="store_true")
    a = ap.parse_args()
    what = a.what
    ns, dep = ("traefik", "traefik") if what == "traefik-provider" else ("pfe", "frontend")
    run = O.Run("p4-webchange-%s%s" % (what, "-rollback" if a.rollback else ""))
    say = run.say
    if a.rollback:
        ok = rollout_undo(ns, dep, say)
        say("retour arrière : %s" % ("OK" if ok else "à vérifier"))
        return 0 if ok else 1
    chk = preflight(run, what, say)
    run.save("preflight.json", chk)
    if not all(c["ok"] for c in chk):
        say("STOP: préconditions non satisfaites — aucune action.")
        return 3
    rc, bk = O.kubectl("-n %s get deploy,svc,cm -o yaml" % ns, timeout=90)
    run.save("sauvegarde-avant.yaml", bk)
    if what == "traefik-provider":
        rc, aj = O.kubectl("-n traefik get deploy traefik -o jsonpath='{.spec.template.spec.containers[0].args}'", timeout=60)
        cur = json.loads(aj.strip("'"))
        ops = [{"op": "remove", "path": "/spec/template/spec/containers/0/args/%d" % i} for i in sorted((cur.index(x) for x in TRAEFIK_DEL if x in cur), reverse=True)]
        ops += [{"op": "add", "path": "/spec/template/spec/containers/0/args/-", "value": x} for x in TRAEFIK_ADD if x not in cur]
        patch = json.dumps(ops, separators=(",", ":"))
        say("patch JSON : %s" % patch)
        rc, out = O.kubectl("-n traefik patch deploy traefik --type json -p '%s' --dry-run=server" % patch, timeout=60)
        say("validation serveur (dry-run) -> rc=%s %s" % (rc, out[:120]))
        if rc != 0:
            say("STOP: patch refusé — aucune action.")
            return 3
    else:
        base_cm, new_cm, transform = FRONT[what]
        rc, live = O.kubectl("-n pfe get cm %s -o jsonpath='{.data.default\\.conf}'" % base_cm, timeout=60)
        live = live[1:-1] if live.startswith("'") and live.endswith("'") else live
        try:
            new_conf = transform(live)
        except ValueError as e:
            say("STOP: configuration nginx vivante inattendue (%s) — aucune action." % e)
            return 3
        run.save("default.conf.avant", live)
        run.save("default.conf.apres", new_conf)
        pod = O.kubectl("-n pfe get pods -l app=frontend -o jsonpath='{.items[0].metadata.name}'")[1].replace("'", "")
        wrap = "events {}\nhttp { include /tmp/ha01-default.conf; }\n"
        test = ("cat > /tmp/ha01-default.conf && printf '%s' > /tmp/ha01-wrap.conf && "
                "(nginx -t -e /tmp/ha01-err.log -c /tmp/ha01-wrap.conf 2>&1 || nginx -t -c /tmp/ha01-wrap.conf 2>&1); rm -f /tmp/ha01-default.conf /tmp/ha01-wrap.conf /tmp/ha01-err.log"
                % wrap.replace("\n", "\\n"))
        rc, out = O.ssh("155", "kubectl --request-timeout=60s -n pfe exec -i %s -- sh -c \"%s\"" % (pod, test.replace('"', '\\"')), timeout=90, stdin=new_conf)
        say("nginx -t dans %s -> %s" % (pod, out.replace("\n", " | ")[:200]))
        if "successful" not in out:
            say("STOP: la nouvelle configuration nginx n'est pas valide — aucune action.")
            return 3
        cm = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": new_cm, "namespace": "pfe", "labels": {"app": "frontend", "ha01": "e16"}},
              "data": {"default.conf": new_conf}}
        for obj in (HEADLESS, cm):
            rc, out = kube_apply(obj, dry=True)
            say("validation serveur (dry-run) %s/%s -> rc=%s %s" % (obj["kind"], obj["metadata"]["name"], rc, out.replace("\n", " ")[:100]))
            if rc != 0:
                say("STOP: objet refusé — aucune action.")
                return 3
    if a.dry_run:
        say("dry-run: préconditions et validations OK, rien n'est appliqué.")
        return 0
    t0 = time.time()
    if what == "traefik-provider":
        rc, out = O.kubectl("-n traefik patch deploy traefik --type json -p '%s'" % patch, timeout=60)
        say("PATCH Traefik -> rc=%s %s" % (rc, out[:100]))
    else:
        for obj in (HEADLESS, cm):
            rc, out = kube_apply(obj)
            say("APPLY %s/%s -> rc=%s %s" % (obj["kind"], obj["metadata"]["name"], rc, out.replace("\n", " ")[:100]))
        # DNS : le nom headless doit renvoyer 2 adresses avant tout changement du déploiement
        pod = O.kubectl("-n pfe get pods -l app=frontend -o jsonpath='{.items[0].metadata.name}'")[1].replace("'", "")
        ok_dns = False
        for _ in range(6):
            # `getent hosts` n'affiche qu'UNE adresse ; `ahostsv4` les liste toutes (une ligne par type de socket) -> on compte les IP distinctes.
            rc, out = O.kubectl("-n pfe exec %s -- sh -c \"getent ahostsv4 backend-headless.pfe.svc.cluster.local | awk '{print \\$1}' | sort -u\"" % pod, timeout=40)
            if len([l for l in out.splitlines() if re.fullmatch(r"\d+\.\d+\.\d+\.\d+", l.strip())]) >= 2:
                ok_dns = True
                break
            time.sleep(3)
        say("résolution de backend-headless depuis %s : %s -> %s" % (pod, "2 adresses" if ok_dns else "KO", out.replace("\n", " | ")[:120]))
        if not ok_dns:
            say("ÉCHEC: le nom headless ne renvoie pas 2 adresses — rien n'a été changé côté déploiement (objets ajoutés laissés, inoffensifs).")
            return 1
        patch = '[{"op":"replace","path":"/spec/template/spec/volumes/0/configMap/name","value":"%s"}]' % new_cm
        rc, out = O.kubectl("-n pfe patch deploy frontend --type json -p '%s'" % patch, timeout=60)
        say("PATCH frontend (volume nginx-conf -> %s) -> rc=%s %s" % (new_cm, rc, out[:100]))
    rc, out = O.kubectl("-n %s rollout status deploy/%s --timeout=300s" % (ns, dep), timeout=320)
    say("rollout -> %s" % out.replace("\n", " | ")[-150:])
    bad = None if rc == 0 else "déploiement non terminé"
    if not bad:
        rc, pods = O.kubectl("-n %s get pods --no-headers" % ns)
        if pods.count(" Running ") < 2 or pods.count("1/1") < 2:
            bad = "pods non Ready après le déploiement: %s" % pods.replace("\n", " | ")[:150]
    if not bad and what == "traefik-provider":
        rc, args = O.kubectl("-n traefik get deploy traefik -o jsonpath='{.spec.template.spec.containers[0].args}'")
        if not all(x in args for x in TRAEFIK_ADD) or any(x in args for x in TRAEFIK_DEL):
            bad = "arguments Traefik inattendus après le déploiement"
        else:
            import p3_harden
            for ip in ("155", "158"):
                for port, tls in ((8000, False), (8443, True)):
                    st = p3_harden.http_status(ip, port, tls)
                    if st not in (200, 301, 302, 404):
                        bad = "Traefik ne répond pas sur .%s:%d (statut %s)" % (ip, port, st)
    if not bad and what.startswith("frontend-nginx"):
        rc, out = O.kubectl("-n pfe get pods -l app=frontend -o jsonpath='{.items[*].spec.volumes[0].configMap.name}'")
        if out.replace("'", "").split() != [new_cm, new_cm]:
            bad = "les 2 pods n'utilisent pas le nouveau ConfigMap: %s" % out
        else:
            st = 0
            for _ in range(5):
                ok1, st1, _i, _e = O.ha01_probe.one_request(dict(host="192.168.148.162", port=443, path="/api/test/health-readiness", sni="helios.orange.tn", hosthdr="helios.orange.tn"))
                st = st1
                if ok1:
                    break
                time.sleep(2)
            if st != 200:
                bad = "l'API applicative via le LB ne répond pas 200 après le changement (statut %s)" % st
    t_obs = time.time()
    while not bad and time.time() - t_obs < a.observe:
        runs = {p: O.tail_fail_run(O.PROBE_WIN, p) for p in O.BY_NAME}
        ups = [O.haproxy_all_up(lb) for lb in ("160", "162")]
        if max(runs.values()) > 15:
            bad = "sonde par nom en échec depuis %ss" % max(runs.values())
        elif not all(u for u, _ in ups):
            bad = "serveurs HAProxy non UP: %s" % [b for _, b in ups]
        elif O.host_avail_mb() < 600:
            bad = "RAM hôte < 600 Mo"
        time.sleep(4)
    if bad:
        say("ÉCHEC: %s -> RETOUR ARRIÈRE" % bad)
        say("retour arrière: %s" % ("OK" if rollout_undo(ns, dep, say) else "À VÉRIFIER"))
        return 1
    say("CHANGEMENT APPLIQUÉ en %.0f s (%s), observation %d s propre. Retour : p4_webchange.py --what %s --rollback" % (time.time() - t0, what, a.observe, what))
    return 0


if __name__ == "__main__":
    sys.exit(main())
