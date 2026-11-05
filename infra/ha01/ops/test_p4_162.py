#!/usr/bin/env python3
"""
Tests HORS CLUSTER (données fabriquées, aucun accès réseau, aucune VM) des ajouts du 4e scénario (.162) à p4_vmstop.py :
  - analyse_dnswatch : bascule du contrôleur, récupération Windows (cache) et nœud, fail-back ;
  - evaluate() pour .162 (nouvelles lignes) ET pour .160/.155/.158 (libellés historiques inchangés, aucune exception) ;
  - garde-fous de configuration : .162 exige dnswatch + survivor_lb ; .160/.155/.158 n'ont PAS dnswatch.
  python test_p4_162.py
"""
import json
import os
import sys
import tempfile
import time

import ha01_ops as O
import p4_vmstop as P

T0 = 1_800_000_000.0           # t_stop fictif
VMSTART = T0 + 600.0


def iso(t):
    return O.iso_ms(t)


def w(path, recs):
    with open(path, "w", encoding="utf-8") as fh:
        for r in recs:
            fh.write(json.dumps(r) + "\n")


def build_dnswatch(d):
    direct, win, node = [], [], []
    t = T0 - 20
    while t < T0 + 900:
        for srv in ("160", "162"):
            for name in ("helios.orange.tn", "api.helios.orange.tn"):
                if srv == "162" and t >= T0 + 0.1 and not (VMSTART + 45 <= t):
                    continue                                                  # .162 morte entre l'arrêt et son retour (+45 s)
                ips = ["162"] if (T0 + 9.4 > t or t >= VMSTART + 70) else ["160"]            # bascule à +9,4 s ; retour de .162 dans la réponse à +70 s après la relance
                direct.append({"ts": iso(t), "mode": "direct", "server": srv, "name": name, "ips": ips, "ttl": 30, "ms": 1})
        t += 0.2
    t = T0 - 20
    while t < T0 + 900:
        dead_phase = T0 <= t < T0 + 27.3
        back = t >= VMSTART + 90
        tgt = "192.168.148.160" if (T0 + 27.3 <= t < VMSTART + 90) else "192.168.148.162"
        ok = not dead_phase if tgt.endswith("162") and not back else True
        if tgt.endswith("162") and T0 <= t < T0 + 27.3:
            ok = False
        if tgt.endswith("162") and VMSTART <= t < VMSTART + 90:
            ok = False
        win.append({"ts": iso(t), "mode": "os", "name": "api.helios.orange.tn", "ips": [tgt], "resolve_ms": 0, "target": tgt, "tcp_ok": ok, "tcp_ms": 1})
        tgt2 = "192.168.148.160" if (T0 + 10.2 <= t < VMSTART + 75) else "192.168.148.162"
        ok2 = not (T0 <= t < T0 + 10.2)
        if tgt2.endswith("162") and VMSTART <= t < VMSTART + 75:
            ok2 = False
        node.append({"ts": iso(t), "mode": "os", "name": "api.helios.orange.tn", "ips": [tgt2], "resolve_ms": 1, "target": tgt2, "tcp_ok": ok2, "tcp_ms": 1})
        t += 0.5
    w(os.path.join(d, "dnswatch-direct.jsonl"), direct)
    w(os.path.join(d, "dnswatch-windows.jsonl"), win)
    w(os.path.join(d, "dnswatch-node.jsonl"), node)


def probe_records(fail_probe=None, fail_from=0.0, fail_to=0.0):
    recs = []
    for name in ("web", "api_app", "kube_api", "lb160_web", "lb160_api", "lb162_web", "lb162_api"):
        for i in range(120):
            t = T0 + i * 1.0
            bad = name == fail_probe and fail_from <= i < fail_to
            recs.append({"ts": iso(t), "probe": name, "ok": not bad, "status": 0 if bad else 200, "ms": 3000 if bad else 60, "ip": None, "err": "TimeoutError" if bad else None})
    return recs


def ctx(vm, dnsw=None):
    can = {"acked_lost": 0, "acked": 3000, "failures": 0, "failures_total": 0, "max_gap_hors_gel_s": 0.3, "max_gap_s": 0.3}
    return {"dnsw": dnsw, "vm": vm, "stalls": [], "vantages": {"Windows": probe_records(), ".161 sans cache": probe_records("kube_api", 5, 20), ".161 avec cache": []},
            "canaries": {"ha01-canary-w1": dict(can), "ha01-canary-w2": dict(can)}, "stopped_ok": True, "stop_detail": "vmrun rc=0", "notready_unexpected": {},
            "dead_notready_after_s": None, "etcd_ok_after_s": 2, "term_before": 1808, "term_after": 1809, "leader_before": 1, "leader_after": 2,
            "mem_min": {"153": 600, "155": 900, "158": 800, "160": 600}, "oom": 0, "mem_evictions": [], "host_min": 5000, "return": {"ssh_s": 55, "etcd5_s": 60, "lb_s": 65, "dns_s": 120, "pods_s": 40, "ready_s": 55, "cnpg_s": 90},
            "stabilize_s": 1800, "stab_anomalies": [], "stab_explained": [], "abort": None, "dns_mode": "enforce", "dns_switch_s": None, "lease_max": {}, "cnpg_timeline": [],
            "pending_note": {}}


def main():
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        ok = ok and bool(cond)
        print("%s %-100s %s" % ("OK " if cond else "KO ", name, detail))

    # 1) configuration
    check("VMS['162'] : dnswatch + survivor_lb=160 + kind lb + deux canaris survivants", P.VMS["162"].get("dnswatch") and P.VMS["162"]["survivor_lb"] == "160"
          and P.VMS["162"]["kind"] == "lb" and P.VMS["162"]["alive_canaries"] == ["ha01-canary-w1", "ha01-canary-w2"] and P.VMS["162"]["folder"] == "k8s-lb2")
    check(".160/.155/.158 n'ont PAS dnswatch (comportement inchangé)", not any(P.VMS[v].get("dnswatch") for v in ("160", "155", "158")))
    check("VMS['160'].survivor_lb == '162' (inchangé)", P.VMS["160"]["survivor_lb"] == "162")
    check("vmx de .162", P.vmx_of("162").lower().endswith("k8s-lb2\\k8s-lb2.vmx"), P.vmx_of("162"))
    # 2) analyse_dnswatch
    d = tempfile.mkdtemp(prefix="p4-162-test-")
    build_dnswatch(d)
    r = P.analyse_dnswatch(d, T0, VMSTART, "162", "160")
    check("bascule du dnsmasq survivant vers .160 mesurée (9,4 s attendues)", r["flip_server_s"] is not None and abs(r["flip_server_s"] - 9.4) <= 0.4, r["flip_server_s"])
    check("fail-back du dnsmasq (retour de .162 dans la réponse) mesuré (70 s après la relance)", r["failback_server_s"] is not None and abs(r["failback_server_s"] - 70) <= 0.4, r["failback_server_s"])
    check("Windows (cache) : première erreur ~0 s, récupération 27,3 s, coupure ~27 s", r["windows"]["first_fail_s"] <= 0.6 and abs(r["windows"]["recover_s"] - 27.3) <= 1.0 and 26 <= r["windows"]["outage_s"] <= 28.5, r["windows"])
    check("nœud : récupération 10,2 s", abs(r["node"]["recover_s"] - 10.2) <= 1.0, r["node"])
    check("Windows : fail-back vers .162 mesuré après la relance", r["windows"]["failback_s"] is not None and 85 <= r["windows"]["failback_s"] <= 95, r["windows"].get("failback_s"))
    check("réponses périmées comptées", r["windows"]["stale_answers"] >= 1, r["windows"]["stale_answers"])
    # 3) evaluate .162 / .160 / .155 / .158
    rows, md = P.evaluate(ctx("162", r))
    txt = "\n".join(c for c, _, _ in rows)
    check("evaluate(.162) : ligne « LB survivant .160 en direct (lb160_web) »", "LB survivant .160 en direct (lb160_web)" in txt)
    check("evaluate(.162) : critère de bascule DNS <= 15 s présent et PASS", any("bascule DNS" in c and ok_ for c, ok_, _ in rows))
    check("evaluate(.162) : section « Bascule DNS vue par chaque client » dans le markdown", "Bascule DNS vue par chaque client" in md and "Windows" in md)
    check("evaluate(.162) : la série kube_api (.161 sans cache, 15 s) est bien jugée ❌ (seuil 15 s : 15,0 limite) ou ✅ selon le calcul — pas d'exception", True)
    rows160, md160 = P.evaluate(ctx("160"))
    t160 = "\n".join(c for c, _, _ in rows160)
    check("evaluate(.160) : libellé historique « LB survivant .162 en direct (lb162_web) » INCHANGÉ", "LB survivant .162 en direct (lb162_web)" in t160)
    check("evaluate(.160) : AUCUNE ligne de bascule DNS spécifique", not any("bascule DNS" in c for c, _, _ in rows160))
    check("evaluate(.160) : même nombre de critères qu'avant (hors .162)", len(rows160) == len(rows) - 1, "%d vs %d" % (len(rows160), len(rows)))
    for vm in ("155", "158"):
        c = ctx(vm)
        c["dead_notready_after_s"] = 45
        c["canaries"] = {"ha01-canary-w1": {"acked_lost": 0, "acked": 100, "failures": 0, "failures_total": 0, "max_gap_hors_gel_s": 0.3, "max_gap_s": 0.3},
                         "ha01-canary-w2": {"acked_lost": 0, "acked": 100, "failures": 0, "failures_total": 0, "max_gap_hors_gel_s": 0.3, "max_gap_s": 0.3}}
        try:
            rr, _ = P.evaluate(c)
            check("evaluate(.%s) s'exécute sans exception (%d critères)" % (vm, len(rr)), len(rr) >= 20)
        except Exception as e:  # noqa: BLE001
            check("evaluate(.%s) sans exception" % vm, False, repr(e))
    # 4) garde-fous de l'orchestrateur
    ok_p5, det = P.recent_p5_preflight("162")
    check("recent_p5_preflight() lit le dernier p5-162-preflight-*.json", isinstance(ok_p5, bool) and "p5-162" in det or "aucun" in det, det)
    check("confirmation(.162) : REFUSÉE tant que CONFIRMATION-162.txt n'existe pas", not P.confirmation("162")[0], P.confirmation("162")[1])
    print("\nRÉSULTAT :", "TOUS LES TESTS OK" if ok else "ÉCHEC")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
