#!/usr/bin/env python3
"""
HA-01 Phase 4 — preuve APPLICATIVE de la reprise de la base de données (lecture seule).

Les canaris (Python/psycopg) prouvent que la base accepte à nouveau des écritures ; ils ne prouvent PAS que le backend Java (pool Hikari, sans
`socketTimeout` JDBC) retrouve ses connexions. `/api/test/health-readiness` ne touche pas la base et `/api/test/db` exige SUPER_ADMIN : aucune sonde
publique ne mesure donc la reprise applicative. Cet outil lit APRÈS COUP, dans les journaux horodatés des pods `app=backend` encore joignables, les lignes
de connexion JDBC en échec (Hikari, PSQLException, base en démarrage/arrêt, transaction en lecture seule = écriture vers une réplique...) entre deux instants.

  python p4_appdb_evidence.py --since 2026-11-04T12:25:00Z --until 2026-11-04T12:30:00Z [--save dossier]

Sortie : par pod (nœud, état), nombre de lignes, première et dernière occurrence, 2 échantillons ; verdict « dernière erreur à T+x s de la 1re ».
Le pod d'un nœud éteint n'a pas de journal lisible (kubelet injoignable) : il est listé comme tel. Code retour 0 (information, pas un critère).
"""
import argparse
import json
import os
import re
import sys

import ha01_ops as O

PAT = re.compile(
    r"HikariPool-\d+ - (Failed to validate|Connection is not available|Exception during pool initialization|Thread starvation)"
    r"|SQLTransientConnectionException|PSQLException|org\.postgresql\.util|Connection to .{0,80} refused|the database system is (starting up|shutting down|in recovery)"
    r"|terminating connection|read-only transaction|Unable to acquire JDBC Connection|CannotCreateTransactionException|JDBCConnectionException"
    r"|connection (has been closed|reset)|Broken pipe", re.I)


def ts_of(line):
    """« 2026-11-04T07:50:44.478888157Z texte » -> '2026-11-04T07:50:44Z' ; None si la ligne n'a pas d'horodatage."""
    m = re.match(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)", line)
    return m.group(1) + "Z" if m else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", required=True)
    ap.add_argument("--until", required=True)
    ap.add_argument("--save", default=None, help="dossier où écrire appdb-evidence.json / .md")
    a = ap.parse_args()
    rc, out = O.kubectl("-n pfe get pods -l app=backend -o json", timeout=60)
    try:
        pods = [(p["metadata"]["name"], p["spec"].get("nodeName"), p["status"].get("phase")) for p in json.loads(out)["items"]]
    except Exception:  # noqa: BLE001
        print("liste des pods backend illisible: %s" % out[:200])
        return 0
    res = {}
    for name, node, phase in pods:
        info = {"node": node, "phase": phase, "lisible": False, "lignes_db": 0, "premiere": None, "derniere": None, "echantillons": []}
        res[name] = info
        if phase != "Running":
            continue
        rc, txt = O.kubectl("-n pfe logs %s --since-time=%s --timestamps" % (name, a.since), timeout=120)
        if rc != 0 and not txt:
            continue
        info["lisible"] = True
        hits = []
        for l in txt.splitlines():
            t = ts_of(l)
            if t and a.since <= t <= a.until and PAT.search(l):
                hits.append((t, l))
        info["lignes_db"] = len(hits)
        if hits:
            info["premiere"], info["derniere"] = hits[0][0], hits[-1][0]
            info["echantillons"] = [h[1][:240] for h in (hits[:2] + hits[-2:])]
    lines = ["# Reprise applicative de la base — journaux des pods backend, fenêtre %s -> %s" % (a.since, a.until), ""]
    for name, i in res.items():
        if not i["lisible"]:
            lines.append("- **%s** (%s, %s) : journal non lisible (nœud éteint ou injoignable)" % (name, i["node"], i["phase"]))
        elif not i["lignes_db"]:
            lines.append("- **%s** (%s) : **aucune** erreur de connexion JDBC dans la fenêtre" % (name, i["node"]))
        else:
            span = O.epoch_of(i["derniere"]) - O.epoch_of(i["premiere"])
            lines.append("- **%s** (%s) : %d lignes d'erreur JDBC, de %s à %s (dernière erreur %.0f s après la première)" % (name, i["node"], i["lignes_db"], i["premiere"], i["derniere"], span))
            for s in i["echantillons"]:
                lines.append("    - `%s`" % s.replace("`", "'"))
    text = "\n".join(lines)
    print(text)
    if a.save:
        os.makedirs(a.save, exist_ok=True)
        with open(os.path.join(a.save, "appdb-evidence.json"), "w", encoding="utf-8") as fh:
            json.dump({"since": a.since, "until": a.until, "pods": res}, fh, ensure_ascii=False, indent=1)
        with open(os.path.join(a.save, "appdb-evidence.md"), "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
