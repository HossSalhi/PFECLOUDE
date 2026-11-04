#!/usr/bin/env python3
"""
HA-01 Phase 4 — assemble les MESURES des arrêts réels de VM dans results/2026-11-03/GATE-4-mesures.md (aucune transcription à la main).

  python gate4_report.py

Lit le summary.json de chaque dossier d'essai listé dans RUNS (ordre chronologique) et écrit : (1) un tableau de synthèse, (2) pour chaque essai la liste
COMPLÈTE de ses critères avec ✅/❌ et la mesure brute ET « hors gel » telle que publiée par p4_vmstop.py. Rien n'est recalculé ni arrondi ici.
"""
import json
import os
import re
import sys
import time

import ha01_ops as O

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

RUNS = [
    ("`.160`", "essai unique", "p4-vmstop160-stop160-20261104T093239Z"),
    ("`.155`", "essai 1", "p4-vmstop155-stop155-20261104T101559Z"),
    ("`.155`", "essai 2", "p4-vmstop155-retest155-20261104T123434Z"),
    ("`.155`", "essai 3", "p4-vmstop155-retest155c-20261104T142018Z"),
    ("`.158`", "essai (1re tentative abandonnée à T+14 s par un défaut d'outillage, voir GATE-4.md)", "p4-vmstop158-stop158c-20261104T151214Z"),
]


def load(d):
    p = os.path.join(O.RES, d, "summary.json")
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None


def pick(crit, pattern):
    for c in crit:
        if re.search(pattern, c["c"]):
            return c
    return None


def first_stat(c):
    """« 99.829 % / 1.0 s » pris dans la mesure hors gel (1re occurrence)."""
    if not c:
        return "—"
    m = re.search(r"([\d.]+) % / ([\d.]+) s", c["v"])
    return ("%s %% / %s s" % (m.group(1), m.group(2))) if m else str(c["v"])[:40]


def main():
    out = ["# GATE-4 — mesures des arrêts réels de VM (généré par `ops/gate4_report.py`, rien n'est saisi à la main)", "",
           "Chaque ligne = un `vmrun stop hard` réel d'UNE VM, 10 min d'observation, relance, 30 min de stabilisation. Les pourcentages sont « disponibilité / plus longue série d'échecs » "
           "mesurés HORS gel d'E/S de l'hôte (écart E6) ; le brut est publié dans le détail de chaque essai. La colonne « Gels d'E/S » indique les gels détectés pendant l'essai COMPLET "
           "(mesure + retour + stabilisation) : un gel survenu pendant la phase de retour (cas de `.158`) n'affecte pas les mesures de service de la fenêtre de 10 min, mais peut "
           "affecter la stabilisation.", "",
           "| VM | Essai | Arrêt (UTC) | Score | Web (Windows) | `api_app` (Windows) | API K8s (Windows) | Écritures perdues (w1/w2) | Trou d'écriture survivant | etcd 4/4 en | Retour : Ready / etcd 5/5 / CNPG 2/2 | Gels d'E/S |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    details = []
    for vm, label, d in RUNS:
        s = load(d)
        if not s:
            out.append("| %s | %s | — | **dossier absent ou essai non terminé** (%s) | | | | | | | |" % (vm, label, d))
            continue
        crit = s["criteria"]
        ok_n = sum(1 for c in crit if c["ok"])
        t = time.strftime("%H:%M:%S", time.gmtime(s["t_stop"]))
        w = pick(crit, r"^\[Windows\] web")
        a = pick(crit, r"^\[Windows\] api_app")
        k = pick(crit, r"^\[Windows\] kube_api")
        lost = [re.search(r"perdues=(\d+)", c["v"]).group(1) for c in crit if c["c"].startswith("canari") and "perdue" in c["c"] and re.search(r"perdues=(\d+)", c["v"])]
        stall = str(crit[0]["v"])[:60] if crit and "gels d'E/S" in crit[0]["c"] else "—"
        gap = pick(crit, r"survivant\) : (0 échec|1re écriture)")
        gaptxt = re.search(r"trou max hors gel=([\d.]+)s", gap["v"]).group(1) + " s" if gap and re.search(r"trou max hors gel=([\d.]+)s", gap["v"]) else "—"
        e = pick(crit, r"etcd : 4/4")
        r1 = pick(crit, r"retour : (nœud Ready|SSH)")
        r2 = pick(crit, r"retour : CNPG")
        rtxt = "%s ; CNPG %s" % (str(r1["v"]) if r1 else "—", str(r2["v"]) if r2 else "—")
        out.append("| %s | %s | %s | **%d/%d** %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            vm, label, t, ok_n, len(crit), "✅" if ok_n == len(crit) else "❌", first_stat(w), first_stat(a), first_stat(k), "/".join(lost) or "—", gaptxt,
            str(e["v"]) if e else "—", rtxt, stall))
        details.append("\n## %s — %s (`%s`)\n\n| Critère | Résultat | Mesure |\n|---|---|---|" % (vm, label, d))
        for c in crit:
            details.append("| %s | %s | %s |" % (c["c"], "✅" if c["ok"] else "❌", str(c["v"]).replace("|", "/")[:400]))
        if s.get("abort"):
            details.append("\n**Abandon : %s**" % s["abort"])
    out.append("")
    out += details
    p = os.path.join(O.RES, "GATE-4-mesures.md")
    open(p, "w", encoding="utf-8").write("\n".join(out) + "\n")
    print("écrit :", p)
    print("\n".join(out[:14]))


if __name__ == "__main__":
    main()
