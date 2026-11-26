# -*- coding: utf-8 -*-
"""STATUT AUTO EFFECTIF des VM — LECTURE SEULE. Trois niveaux, jamais confondus :

  (hors niveaux) DÉTECTION SEULE : .160 (supervision) — la politique du dépôt la veut en DETECT_ONLY (pas d'auto-rallumage) ;
              le watchdog en direct doit le dire aussi (sinon : « à redéployer ») ; son nom ne doit PAS figurer dans ACTIVE_VMS
  CONFIGURÉE  la politique du dépôt ET le watchdog EN DIRECT déclarent la VM `AUTO` (route et notifications conformes)
  ARMÉE       le rallumage RÉEL est actif pour cette VM : ENABLED=true ∧ DRYRUN=false ∧ nom ∈ ACTIVE_VMS (ConfigMap vivant).
              Sinon c'est le MODE OMBRE : la décision est prise et journalisée mais aucun démarrage n'est demandé
  VALIDÉE     un arrêt RÉEL a été suivi d'un rallumage automatique : action RESTART_VM COMPLETED + incident RESOLVED +
              vérification PASS en base, depuis la mise en service de la chaîne actuelle (6 nov. 2026 09:56)

Ainsi « AUTO » (configuré) ne veut JAMAIS dire « armé » ni « validé ». Affiche aussi, par VM, les deux cooldowns :
  - POLITIQUE du backend  30 min : refuse de DEMANDER un nouveau démarrage si une action RESTART_VM (même en échec) vise la VM depuis < 30 min ;
                          lu en base, il survit aux redémarrages du backend
  - WATCHDOG              240 s  : refuse d'EXÉCUTER un second `vmrun start` < 240 s après un démarrage, quel que soit l'appelant ;
                          persisté dans vm-power-state.json ; s'ajoutent un backoff 10/20/40/60 s après un échec et l'idempotence par incident

  python infra/helios-sot/tools/auto_status.py
"""
import json
import os
import re
import sys
import importlib.util

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # pragma: no cover
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("n3_preflight", os.path.join(HERE, "n3_preflight.py"))
n3 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(n3)
so = n3.so

ORDER = ["k8s-worker1", "k8s-worker2", "k8s-lb2", "k8s-monitoring", "k8s-master"]
IP_LABEL = {"k8s-worker1": ".158", "k8s-worker2": ".155", "k8s-lb2": ".162", "k8s-monitoring": ".160", "k8s-master": ".153"}
NEW_CHAIN_SINCE = "2026-11-06 09:56:00"   # mise en service de la chaîne actuelle (activation de k8s-worker1) : on ignore l'historique antérieur
BACKEND_COOLDOWN_MIN = n3.restart_cooldown_minutes()   # VmPowerPolicy.RESTART_COOLDOWN = YAML thresholds.restart_cooldown_minutes (30 -> 5 le 18 nov. 2026)
WATCHDOG_COOLDOWN_S = 240                 # HELIOS_COOLDOWN_S (watchdog)


def active_set(cfg):
    return {x.strip() for x in (cfg.get("HELIOS_VMPOWER_ACTIVE_VMS") or "").split(",") if x.strip()}


def build(policy, states, cfg, validated, last_action_age_s):
    """Pure : une ligne de statut par VM. `validated` = {nom: (incident, heure)} ; `last_action_age_s` = {nom: âge en s de la dernière action}."""
    armed_global = cfg.get("HELIOS_VMPOWER_ENABLED") == "true" and cfg.get("HELIOS_VMPOWER_DRYRUN") == "false"
    actives = active_set(cfg)
    rows = []
    for name in ORDER:
        p, s = policy.get(name) or {}, states.get(name) or {}
        row = {"vm": name, "ip": IP_LABEL[name], "politique": p.get("mode"), "route": p.get("restart_route"),
               "notifications": p.get("notifications"), "watchdog": s.get("mode"), "etat": s.get("etat")}
        if p.get("mode") == "DENY_RESTART":
            # Mode CONSERVÉ mais plus utilisé par aucune VM depuis le 17 nov. 2026 (.153 est AUTO) : s'il réapparaît, il est affiché.
            row["statut"] = "INTERDITE (DENY_RESTART : politique du dépôt ET watchdog en direct ; les autres couches sont couvertes par les tests)" if s.get("mode") == "DENY_RESTART" \
                else f"ANOMALIE : DENY_RESTART voulu (politique du dépôt) mais le watchdog en direct déclare {s.get('mode')} : à redéployer"
            row["niveau"] = 0
        elif p.get("mode") == "DETECT_ONLY":
            if name in actives:
                row["statut"], row["niveau"] = ("ANOMALIE : DÉTECTION SEULE voulue (politique du dépôt) mais le nom figure encore dans ACTIVE_VMS "
                                                "— retirer ce nom de HELIOS_VMPOWER_ACTIVE_VMS"), -1
            elif s.get("mode") == "DETECT_ONLY":
                row["statut"], row["niveau"] = ("DÉTECTION SEULE (VM de supervision : pas d'auto-rallumage voulu) — politique du dépôt ET "
                                                "watchdog en direct d'accord ; rallumage par l'administrateur"), 0
            else:
                row["statut"], row["niveau"] = (f"DÉTECTION SEULE voulue (politique du dépôt) ; non armée, donc aucun rallumage demandé — "
                                                f"mais le watchdog en direct déclare encore {s.get('mode')} : à redéployer"), 0
        else:
            configuree = p.get("mode") == "AUTO" and s.get("mode") == "AUTO"
            armee = configuree and armed_global and name in actives
            if name in validated and armee:
                row["statut"], row["niveau"] = f"VALIDÉE par arrêt réel (incident #{validated[name][0]} à {validated[name][1]}) et ARMÉE", 3
            elif armee:
                row["statut"], row["niveau"] = "ARMÉE (rallumage réel actif) — non encore validée par un arrêt réel", 2
            elif configuree:
                row["statut"], row["niveau"] = "CONFIGURÉE AUTO — mode OMBRE (décision journalisée, aucun démarrage demandé)", 1
            else:
                row["statut"], row["niveau"] = "NON CONFIGURÉE AUTO (politique ou watchdog ne la déclare pas AUTO)", 0
        age = last_action_age_s.get(name)
        row["cooldown_backend_restant_min"] = None if age is None else max(0.0, BACKEND_COOLDOWN_MIN - age / 60.0)
        row["cooldown_watchdog_restant_s"] = (s.get("cooldown_s") or 0)
        rows.append(row)
    return rows


def _rows(primary, sql):
    rc, out, _ = so.kube("exec", "-i", "-n", so.NS, primary, "-c", "postgres", "--", "psql", "-U", "postgres", "-d", "pfe_db",
                         "-At", "-F", "|", "-f", "-", timeout=60, stdin=sql)
    return [tuple(ln.split("|")) for ln in out.splitlines() if "|" in ln]


def collect(errors):
    cfg = so.collect_config(errors)
    rc, out, _ = so.kube("get", "pods", "-n", so.NS, "-l", "cnpg.io/instanceRole=primary", "-o", "jsonpath={.items[0].metadata.name}")
    primary = out.strip()
    rc, out, _ = so.kube("get", "pods", "-n", so.NS, "-o", "jsonpath={range .items[*]}{.metadata.name}{\"\\n\"}{end}")
    backends = [p for p in out.split() if p.startswith("backend-")]
    validated, ages, leader = {}, {}, None
    if primary:
        names = ",".join("'%s'" % n for n in ORDER)
        sql = ("select 'lease', holder from leader_lease where lease_name='aiops-scheduler';\n"
               f"select 'age', target_name, extract(epoch from (now() at time zone 'Africa/Tunis' - max(created_at)))::int from incident_action where type='RESTART_VM' and target_name in ({names}) group by target_name;\n"
               "select 'ok', a.target_name, i.id, to_char(a.executed_at,'HH24:MI:SS') from incident_action a join incident i on i.id=a.incident_id "
               "where a.type='RESTART_VM' and a.status='COMPLETED' and i.status='RESOLVED' "
               f"and a.created_at >= '{NEW_CHAIN_SINCE}' and exists (select 1 from incident_verification v where v.incident_id=i.id and v.result='PASS') order by a.id;\n")
        for r in _rows(primary, sql):
            if r[0] == "lease":
                leader = next((b for b in backends if r[1].startswith(b)), None)
            elif r[0] == "age":
                ages[r[1]] = int(r[2])
            elif r[0] == "ok":
                validated[r[1]] = (r[2], r[3])
    else:
        errors.append("primaire PostgreSQL introuvable")
    states = so.collect_vm_state(leader or (backends[0] if backends else None), errors)
    return n3.policy_entries(), states, cfg, validated, ages


def main():
    errors = []
    policy, states, cfg, validated, ages = collect(errors)
    rows = build(policy, states, cfg, validated, ages)
    print("\n=== STATUT AUTO EFFECTIF DES VM (lecture seule) ===")
    print("configuration vivante :", json.dumps({k: cfg.get(k) for k in ("HELIOS_VMPOWER_ENABLED", "HELIOS_VMPOWER_DRYRUN", "HELIOS_VMPOWER_ACTIVE_VMS")}))
    for r in rows:
        print(f"\n{r['vm']} ({r['ip']}) — état d'alimentation : {r['etat']}")
        print(f"   politique du dépôt : {r['politique']} (route {r['route']}, notifications {r['notifications']}) | watchdog en direct : {r['watchdog']}")
        print(f"   >>> {r['statut']}")
        if r["politique"] not in ("DETECT_ONLY", "DENY_RESTART"):
            cb = r["cooldown_backend_restant_min"]
            print(f"   cooldown backend ({BACKEND_COOLDOWN_MIN} min) : " + ("aucune action récente" if cb is None else
                  ("expiré" if cb <= 0 else f"encore {cb:.0f} min") + " | " +
                  f"cooldown watchdog ({WATCHDOG_COOLDOWN_S} s) : " + ("expiré" if not r["cooldown_watchdog_restant_s"] else f"encore {r['cooldown_watchdog_restant_s']} s")))
    print(f"\nNB : « cooldown backend ({BACKEND_COOLDOWN_MIN} min) » = valeur du DÉPÔT (infra/helios-vm-policy.yaml). Celle que le backend DÉPLOYÉ applique "
          "se vérifie avec `python infra/helios-sot/tools/policy_reconcile.py` (révision de la politique lue dans le bytecode de l'image).")
    for e in errors:
        print(f"[ATTENTION] collecte incomplète : {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
