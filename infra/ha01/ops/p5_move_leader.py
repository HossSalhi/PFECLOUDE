#!/usr/bin/env python3
"""
HA-01 (4e scénario) — transfert du LEADER etcd vers un membre donné (pire cas : on éteint ensuite ce membre). OPÉRATION ÉTCD STANDARD (`etcdctl move-leader`), sans coupure.

  python p5_move_leader.py --to 162              # PAR DÉFAUT : vérifie et AFFICHE la commande exacte ; n'exécute RIEN
  python p5_move_leader.py --to 162 --execute    # exécute le transfert, puis vérifie (leader, 5/5 sains, indices)

Garde-fous : le membre cible doit être votant (pas learner), sain, synchrone (indice appliqué à <= 5 du leader), avec un fsync WAL p99 < 50 ms ; etcd doit être 5/5 sain ;
aucune exécution si l'une de ces conditions échoue. Si la cible est déjà leader : rien à faire. Aucun service n'est arrêté.
Code retour : 0 OK (ou rien à faire) · 3 conditions non remplies (rien fait) · 1 échec du transfert.
"""
import argparse
import json
import sys
import time

import ha01_ops as O

D = O.ha01_hoststall


def status():
    rc, out = O.ssh("158", O.etcdctl("endpoint status -w json") + " 2>/dev/null", timeout=30)
    res = {}
    for e in json.loads(out):
        s = e["Status"]
        res[e["Endpoint"].split("//")[1].split(":")[0].split(".")[-1]] = {"id": s["header"]["member_id"], "leader": s["leader"], "term": s["raftTerm"],
                                                                             "applied": s["raftAppliedIndex"], "learner": s.get("isLearner", False)}
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--to", required=True, choices=["153", "155", "158", "160", "162"])
    ap.add_argument("--execute", action="store_true")
    a = ap.parse_args()
    st = status()
    lead_id = next(v["leader"] for v in st.values())
    cur = [k for k, v in st.items() if v["id"] == lead_id][0]
    tgt = st[a.to]
    print("leader actuel : .%s (terme %s) ; cible : .%s (id %x)" % (cur, st[cur]["term"], a.to, tgt["id"]))
    if cur == a.to:
        print("la cible est déjà le leader : rien à faire")
        return 0
    spread = max(v["applied"] for v in st.values()) - tgt["applied"]
    det = D.LiveDetector([O.NET + x for x in O.ETCD_ALL], period=5.0)
    det.start()
    time.sleep(12)
    fs = {k.split(".")[-1]: v for k, v in det.current().items()}
    det.stop()
    conds = [("5/5 sains", O.etcd_healthy() == 5), ("cible votante (pas learner)", not tgt["learner"]), ("cible synchrone (écart d'index <= 5)", spread <= 5),
             ("fsync WAL p99 de la cible < 50 ms", fs.get(a.to) is not None and fs[a.to] < 0.05)]
    for lab, ok in conds:
        print("  %s %s" % ("OK " if ok else "KO ", lab))
    cmd = O.etcdctl("move-leader %x" % tgt["id"], eps=(cur,))
    print("commande exacte (exécutée sur .158 par ssh) :\n  " + cmd.replace("--cacert", "\\\n    --cacert"))
    if not all(ok for _, ok in conds):
        print("CONDITIONS NON REMPLIES : aucun transfert.")
        return 3
    if not a.execute:
        print("DRY-RUN : aucune exécution (ajouter --execute pour transférer).")
        return 0
    rc, out = O.ssh("158", cmd + " 2>&1", timeout=40)
    print("move-leader ->", out[:160])
    time.sleep(8)
    st2 = status()
    lead2 = [k for k, v in st2.items() if v["id"] == next(x["leader"] for x in st2.values())][0]
    ok = lead2 == a.to and O.etcd_healthy() == 5
    print("leader après : .%s (terme %s) ; etcd %s/5 sains -> %s" % (lead2, st2[lead2]["term"], O.etcd_healthy(), "OK" if ok else "ÉCHEC"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
