#!/usr/bin/env python3
"""
HA-01 (écart E14) — assouplit le CONTRÔLE D'ISOLEMENT du primaire CloudNativePG face à la perte brutale du nœud de la réplique.

Constat (arrêt réel de .155, 4 nov. 10:16Z) : 28 s après l'arrêt, la sonde de vivacité du primaire (`/healthz`, qui échoue quand le primaire n'atteint ni
l'API Kubernetes ni sa réplique) a renvoyé 500 trois fois de suite (failureThreshold 3 x 10 s) -> le kubelet a REDÉMARRÉ le primaire (arrêt « smart »
180 s puis relance : 9 s d'écritures en échec à T+205 s). Cause : pendant ~45 s l'apiserver mort reste dans les endpoints du Service `kubernetes`.
Correctif : spec.probes.liveness.failureThreshold 3 -> 9 (≈ 90 s). Le contrôle d'isolement RESTE activé (une vraie partition réseau est toujours isolée).

  python p3_cnpg_liveness.py [--dry-run]          # applique ; suit le déploiement progressif ; remet le primaire sur son nœud d'origine
  python p3_cnpg_liveness.py --rollback           # retour à la valeur d'origine (failureThreshold 3)

Le changement déclenche un déploiement progressif géré par l'opérateur (réplique d'abord, puis bascule contrôlée du primaire, puis redémarrage de l'ancien primaire).
Mesure : canaris des workers (écritures acquittées perdues = 0 exigé, trou max rapporté). Code retour : 0 OK · 1 échec · 3 préflight KO (rien fait).
"""
import argparse
import json
import sys
import time

import ha01_ops as O


def pods_threshold():
    rc, out = O.kubectl("-n pfe get pods -l cnpg.io/cluster=postgres -o json", timeout=60)
    res = {}
    try:
        for p in json.loads(out)["items"]:
            lp = p["spec"]["containers"][0].get("livenessProbe", {})
            res[p["metadata"]["name"]] = (lp.get("failureThreshold"), p["spec"]["nodeName"], p["metadata"]["labels"].get("cnpg.io/instanceRole"))
    except Exception:  # noqa: BLE001
        pass
    return res


def primary():
    rc, out = O.kubectl("-n pfe get pods -l cnpg.io/instanceRole=primary -o json", timeout=60)
    try:
        it = json.loads(out)["items"][0]
        return it["metadata"]["name"], it["spec"]["nodeName"]
    except Exception:  # noqa: BLE001
        return None, None


def promote(name, say, timeout=240):
    for ip in ("158", "155"):
        rc, o = O.ssh(ip, "test -x /home/kube/bin/kubectl-cnpg && /home/kube/bin/kubectl-cnpg promote postgres %s -n pfe" % name, timeout=60)
        if rc == 0:
            say("switchover vers %s demandé : %s" % (name, o.replace("\n", " | ")[:100]))
            break
    else:
        return False
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(4)
        st = O.cnpg_state()
        if st.startswith("Cluster in healthy state") and ("|%s|2" % name) in st:
            say("primaire = %s en %.0f s" % (name, time.time() - t0))
            return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--rollback", action="store_true")
    ap.add_argument("--threshold", type=int, default=9)
    a = ap.parse_args()
    run = O.Run("p3-cnpg-liveness%s" % ("-rollback" if a.rollback else ""))
    say = run.say
    target = 3 if a.rollback else a.threshold
    chk = []

    def add(name, ok, detail):
        chk.append({"check": name, "ok": bool(ok), "detail": detail})
        say("PREFLIGHT %-72s %s  %s" % (name[:72], "OK " if ok else "KO ", str(detail)[:100]))
    ns = O.nodes_state()
    add("3 nœuds Ready, etcd 5/5", len(ns) == 3 and all(v == "Ready" for v in ns.values()) and O.etcd_healthy() == 5, ns)
    st = O.cnpg_state()
    add("CNPG sain 2/2", st.startswith("Cluster in healthy state") and st.endswith("|2"), st)
    add("aucun pod non sain", not O.unhealthy_pods(), O.unhealthy_pods()[:3])
    for dep in ("ha01-canary-w1", "ha01-canary-w2"):
        age = O.canary_ack_age(dep)
        add("canari %s écrit depuis < 30 s" % dep, age is not None and age < 30, None if age is None else round(age, 1))
    add("harnais actif", O.file_age(O.PROBE_WIN) < 15 and O.file_age(O.SAMPLES) < 60, "%.0fs/%.0fs" % (O.file_age(O.PROBE_WIN), O.file_age(O.SAMPLES)))
    h = O.host_avail_mb()
    add("RAM hôte >= 2000 Mo", h >= 2000, h)
    for w in ("155", "158"):
        m = O.mem_avail(w)
        add(".%s MemAvailable >= 600 Mo" % w, (m or 0) >= 600, m)
    ws = O.ha01_hoststall.windows(time.time() - 300, time.time())
    add("aucun gel d'E/S de l'hôte dans les 5 dernières minutes", ws == [], O.ha01_hoststall.describe(ws))
    add("kubectl-cnpg disponible sur .158", O.ssh("158", "test -x /home/kube/bin/kubectl-cnpg && echo ok", timeout=20)[1] == "ok", "")
    p0 = primary()
    before = pods_threshold()
    add("état avant consigné (primaire, seuils)", bool(p0[0]) and bool(before), {"primaire": p0, "seuils": before})
    run.save("preflight.json", chk)
    if not all(c["ok"] for c in chk):
        say("STOP: préconditions non satisfaites — aucune action.")
        return 3
    patch = json_patch(target)
    rc, out = O.kubectl("-n pfe patch cluster.postgresql.cnpg.io postgres --type merge -p '%s' --dry-run=server" % patch, timeout=60)
    say("validation serveur (dry-run) -> rc=%s %s" % (rc, out[:120]))
    if rc != 0:
        say("STOP: patch refusé — aucune action.")
        return 3
    if a.dry_run:
        say("dry-run: préconditions et validation serveur OK, rien n'est appliqué.")
        return 0
    t0 = time.time()
    rc, out = O.kubectl("-n pfe patch cluster.postgresql.cnpg.io postgres --type merge -p '%s'" % patch, timeout=60)
    say("PATCH failureThreshold=%s -> rc=%s %s" % (target, rc, out[:100]))
    # suivi du déploiement progressif (≤ 15 min)
    ok = False
    timeline = run.jsonl("timeline.jsonl")
    while time.time() - t0 < 900:
        time.sleep(10)
        th = pods_threshold()
        st = O.cnpg_state()
        timeline.write(json.dumps({"t": round(time.time() - t0), "cnpg": st, "pods": th}, ensure_ascii=False) + "\n")
        timeline.flush()
        if th and all(v[0] == target for v in th.values()) and len(th) == 2 and st.startswith("Cluster in healthy state") and st.endswith("|2"):
            ok = True
            break
    say("déploiement progressif %s en %.0f s ; seuils: %s ; état: %s" % ("TERMINÉ" if ok else "NON TERMINÉ", time.time() - t0, pods_threshold(), O.cnpg_state()))
    if ok and p0[0] and primary()[0] != p0[0]:
        say("le primaire a changé (%s -> %s) : retour sur %s" % (p0[0], primary()[0], p0[0]))
        ok = promote(p0[0], say)
    t1 = time.time()
    time.sleep(20)
    # mesure : canaris des workers sur toute l'opération
    logs = {d: O.ha01_canary_report.fetch_log(d) for d in ("ha01-canary-w1", "ha01-canary-w2")}
    ids = O.ha01_canary_report.fetch_ids()
    stalls = list(O.ha01_hoststall.windows(t0 - 60, t1 + 30) or [])
    rows = [("seuil de vivacité = %s sur les 2 instances, cluster sain 2/2, primaire d'origine %s" % (target, p0[0]), ok and primary()[0] == p0[0], {"primaire": primary(), "seuils": pods_threshold()})]
    for d, lg in logs.items():
        ca = O.ha01_canary_report.analyse(lg, ids, t0 - 5, t1 + 20, exclude=stalls, exclude_after_s=60)
        rows.append(("canari %s : 0 écriture acquittée perdue pendant l'opération" % d, ca["acked_lost"] == 0, "perdues=%d acquittées=%d trou max hors gel=%.1fs (brut %.1fs)" % (ca["acked_lost"], ca["acked"], ca["max_gap_hors_gel_s"], ca["max_gap_s"])))
        rows.append(("canari %s : trou d'écriture <= 120 s" % d, ca["max_gap_hors_gel_s"] <= 120, "%.1f s" % ca["max_gap_hors_gel_s"]))
    O.write_report(run, "CNPG : liveness failureThreshold %s" % target, rows, "", {"target": target})
    for c, o, v in rows:
        say("  %s %-90s %s" % ("OK" if o else "KO", c[:90], str(v)[:110]))
    return 0 if all(o for _, o, _ in rows) else 1


def json_patch(n):
    return '{"spec":{"probes":{"liveness":{"failureThreshold":%d}}}}' % n


if __name__ == "__main__":
    sys.exit(main())
