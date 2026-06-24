#!/usr/bin/env python3
"""Valide CHAQUE type en mode MANUEL (un seul a la fois, sequentiel) — comme un humain qui tape
une commande, attend la resolution, puis passe au suivant. Pour chaque type :
  (1) remet l'app cible saine, (2) injecte la panne, (3) attend RESOLVED/RESOLUTION_FAILED,
  (4) enregistre type + action + statut + commande kubectl tracee.
But : reveler les bugs de timing propres au mode manuel (1 incident isole), type par type.

Usage : python scripts/validate_manual_all.py [type1 type2 ...]   (defaut : les 15 sauf crashloop)
"""
import time, sys
import inject_one as I  # reuse heal/inject/wait_ready/TYPES/k

DEFAULT = [t for t in I.TYPES if t != "crashloop"]  # crashloop deja valide


def sqlc(q):
    return I.k("exec", "postgres-1", "-c", "postgres", "--", "psql", "-t", "-A", "-F", "|",
               "postgresql://appuser:postgres@localhost/pfe_db", "-c", q)


def main():
    types = sys.argv[1:] or DEFAULT
    bad = [t for t in types if t not in I.TYPES]
    if bad:
        print("Types inconnus:", bad); sys.exit(1)
    results = []
    for t in types:
        app = I.TYPES[t][0]
        print(f"\n===== {t} ({app}) =====", flush=True)
        maxid = sqlc("SELECT COALESCE(MAX(id),0) FROM incident;").strip() or "0"
        print("  heal..."); I.heal(t, app); I.wait_ready(app)
        print("  inject:", I.inject(t, app), flush=True)
        final = None
        for _ in range(28):  # ~7 min max
            time.sleep(15)
            row = sqlc(f"SELECT incident_type, status, COALESCE((SELECT a.status||':'||a.type FROM incident_action a WHERE a.incident_id=i.id ORDER BY a.created_at DESC LIMIT 1),'-') FROM incident i WHERE app='{app}' AND id>{maxid} ORDER BY id DESC LIMIT 1;").strip()
            if not row or '|' not in row:
                print("    ...pas encore detecte", flush=True); continue
            parts = row.split('|')
            itype, status, action = parts[0], parts[1], parts[2] if len(parts) > 2 else '-'
            print(f"    {itype} | {status} | {action}", flush=True)
            if status in ("RESOLVED", "RESOLUTION_FAILED", "CLOSED"):
                iid = sqlc(f"SELECT id FROM incident i WHERE app='{app}' AND id>{maxid} ORDER BY id DESC LIMIT 1;").strip()
                cmd = I.k("exec", "postgres-1", "-c", "postgres", "--", "psql", "-t", "-A",
                          "postgresql://appuser:postgres@localhost/pfe_db", "-c",
                          f"SELECT message FROM workflow_logs wl JOIN workflow_instance wi ON wl.workflow_id=wi.id WHERE wi.incident_id={iid} AND wl.message LIKE '$ kubectl%' ORDER BY wl.created_at DESC LIMIT 1;").strip()
                final = (t, itype, status, action, cmd or "(pas de commande kubectl)")
                break
        if not final:
            final = (t, "?", "TIMEOUT", "?", "-")
        results.append(final)
        print(f"  >> {final[0]}: {final[1]} | {final[2]} | {final[3]}", flush=True)

    print("\n\n========== RECAP MODE MANUEL ==========")
    okc = 0
    for t, itype, status, action, cmd in results:
        flag = "OK " if status == "RESOLVED" else "XX "
        if status == "RESOLVED":
            okc += 1
        print(f"{flag}{t:<12} -> {itype:<34} | {status:<18} | {action}")
        print(f"      {cmd}")
    print(f"\nRESOLVED {okc}/{len(results)}")


if __name__ == "__main__":
    main()
