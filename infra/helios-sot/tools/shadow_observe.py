# -*- coding: utf-8 -*-
"""Relevé d'OBSERVATION du MODE OMBRE du rallumage automatique (LECTURE SEULE).

Ce que fait cet outil : un relevé = une ligne JSON ajoutée à
``infra/helios-sot/observation/shadow-observations.jsonl`` + un résumé lisible.
Ce qu'il ne fait JAMAIS : écrire dans le cluster, la base, l'hôte ou une VM ; démarrer, arrêter ou redémarrer quoi que
ce soit ; afficher ou enregistrer un secret (le jeton du watchdog est lu DANS le pod du backend, il n'en sort pas).

Usage
  python shadow_observe.py                              un relevé
  python shadow_observe.py --loop 900 --until 2026-11-07T01:45
                                                        un relevé toutes les 15 min jusqu'à l'heure LOCALE donnée,
                                                        puis la synthèse
  python shadow_observe.py --report                     synthèse de tous les relevés déjà enregistrés

Ce qu'un relevé établit (tout est mesuré, rien n'est déduit)
  cadence      compteur Spring ``tasks_scheduled_execution_seconds_count{code_function="cycle", ...VmPowerWatch}`` de
               chaque pod ; période = écart de temps / écart de compteur entre deux relevés (pod leader : ~15,6-16,2 s)
  surveillance état d'alimentation des 5 VM tel que le watchdog le sert (``GET /vm/state``), modes AUTO / DETECT_ONLY
  décisions    actions RESTART_VM, lignes d'historique « MODE OMBRE » / « NON autorisé », incidents des 5 périmètres de VM
  anomalies    lignes ERROR et avertissements VmPowerWatch cumulés par pod, redémarrages de pods, bail du leader
  hôte         RAM disponible (garde du watchdog : 1 500 Mo), disque C:, écouteur 9099, tâches, règles de pare-feu,
               nombre de ``POST /restart`` dans le journal du watchdog (attendu : 0 tant que rien n'est activé)
  Bridge       empreinte de bridge.py, absence du jeton du watchdog, service actif

Verdict d'un relevé : FAIL (le mode ombre est violé : une demande de démarrage existe), WARN (à expliquer), OK.

MODE « ARMÉE » (observation de la configuration FINALE de production, sans arrêt de VM) : `--armed [--since "AAAA-MM-JJ HH:MM:SS"]`.
Le rallumage réel est alors ACTIF pour `.155 .158 .162` (ACTIVE_VMS exactement ces trois noms, DRYRUN=false) ; on observe pendant
24 h qu'AUCUNE action RESTART_VM ni aucun `vmrun start` ne se produit sans panne réelle (toute action depuis `--since` est un FAIL à
analyser : vraie panne ou faux positif), que `.160` et `.153` ne sont JAMAIS visées, que la configuration reste conforme, que le
watchdog écoute (9099) et que l'ancien agent (9999) n'est pas revenu. Les relevés vont dans `armed-observations.jsonl`.
  python shadow_observe.py --armed --since "2026-11-06 18:00:00" --loop 900 --until 2026-11-07T18:00
"""
import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import tempfile
import time

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # pragma: no cover
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
OUT_DIR = os.path.join(REPO, "infra", "helios-sot", "observation")
OUT_FILE = os.path.join(OUT_DIR, "shadow-observations.jsonl")
ARMED_OUT_FILE = os.path.join(OUT_DIR, "armed-observations.jsonl")
WATCHDOG_LOG = r"C:\Users\houss\helios-state\helios-watchdog.log"
ARMED_ACTIVE = frozenset({"k8s-worker1", "k8s-worker2", "k8s-lb2", "k8s-master"})   # consigne du 17 nov. 2026 : .155 .158 .162 .153 ; jamais .160

# Début du mode ombre = mise en service du backend 0.2.163-vm4auto (heure locale, comme les colonnes created_at).
SHADOW_START_LOCAL = "2026-11-06 01:44:00"
NS = "pfe"
BRIDGE_DIR = "/opt/helios-ssh-bridge"
# 17 nov. 2026 : 00435e8ff652 = infra/helios-sot/bridge/bridge.py du dépôt, DÉPLOYÉ sur .161 le 14 nov. (DISK-E2E-158-01 ; lu en direct
# par policy_reconcile.py : identique). L'ancienne valeur 85307e71e1f1 (Bridge du 6 nov., sans le scénario DISK) était périmée.
TARGET_BRIDGE_SHA12 = "00435e8ff652"
RAM_GUARD_MB = 1500

VM_SCOPE_SQL = (
    "((namespace='k8s-nodes' and app in ('k8s-worker1','k8s-worker2','k8s-master') and incident_type='NODE_NOT_READY') "
    "or (namespace='infra' and app in ('k8s-lb2','k8s-monitoring') and incident_type in ('VM_DOWN','VM_UNREACHABLE')))"
)

def build_sql(since):
    """Requêtes de relevé ; `since` = début de la fenêtre (heure LOCALE, comme les colonnes created_at)."""
    return f"""
select 'restart_vm_total', count(*) from incident_action where type='RESTART_VM';
select 'restart_vm_depuis_ombre', count(*) from incident_action where type='RESTART_VM' and created_at >= '{since}';
select 'restart_vm_interdits', count(*) from incident_action where type='RESTART_VM' and target_name = 'k8s-monitoring' and created_at >= '{since}';
select 'lignes_mode_ombre', count(*) from incident_history where comment ilike '%MODE OMBRE%';
select 'lignes_non_autorise', count(*) from incident_history where comment ilike '%NON autorisé pour l''instant%';
select 'incidents_vm_depuis_ombre', count(*) from incident where created_at >= '{since}' and {VM_SCOPE_SQL};
select 'incidents_vm_ouverts', count(*) from incident where status in ('NEW','OPEN','IN_PROGRESS','WAITING_FOR_APPROVAL','RESOLUTION_FAILED') and {VM_SCOPE_SQL};
select 'preuves_recuperation_vm', count(*) from incident_terminal_evidence where source like 'VM_RECOVERY:%';
select 'actions_vm_bloquees', count(*) from incident_action where type='RESTART_VM' and status in ('PENDING','IN_PROGRESS') and created_at >= '{since}';
select 'bail_leader', holder from leader_lease where lease_name='aiops-scheduler';
select 'bail_age_s', extract(epoch from (now() at time zone 'Africa/Tunis' - renewed_at))::int from leader_lease where lease_name='aiops-scheduler';
"""


SQL = build_sql(SHADOW_START_LOCAL)
OBS = {"armed": False, "since": SHADOW_START_LOCAL}   # réglé par main() : fenêtre et mode d'observation

PS_HOST = r"""
$ErrorActionPreference='SilentlyContinue'
$os = Get-CimInstance Win32_OperatingSystem
$c = Get-PSDrive C
$tasks = [ordered]@{}
foreach ($n in 'HELIOS-Watchdog','HELIOS-Watchdog-HTTP','HELIOS-NodeAutoheal') { $t = Get-ScheduledTask -TaskName $n; $tasks[$n] = [string]$t.State }
$fw = [ordered]@{}
foreach ($n in 'HELIOS Watchdog 9099 - backend .153/.155/.158','HELIOS Watchdog 9099 - BLOCK appelants non autorises','HELIOS Watchdog 9099 - Bridge .161 uniquement','HELIOS-BLOCK-9999-ALL') {
  $r = Get-NetFirewallRule -DisplayName $n
  if ($r) { $fw[$n] = ([string]$r.Enabled + '/' + [string]$r.Action) } else { $fw[$n] = 'absente' }
}
# netstat / Get-NetTCPConnection sont LACUNAIRES sur cet hôte (6-7 sockets en écoute vus au lieu de dizaines, et
# l'écouteur 9099 y apparaît par intermittence) : seul un vrai essai de connexion TCP est fiable.
function Test-Tcp($ip, $port) {
  try { $c = New-Object Net.Sockets.TcpClient; $iar = $c.BeginConnect($ip, $port, $null, $null)
        $ok = $iar.AsyncWaitHandle.WaitOne(2500, $false) -and $c.Connected; $c.Close(); return [bool]$ok } catch { return $false }
}
$l9099 = [int](Test-Tcp '192.168.148.1' 9099)
$l9999 = [int]((Test-Tcp '127.0.0.1' 9999) -or (Test-Tcp '192.168.148.1' 9999))
$log = 'C:\Users\houss\helios-state\helios-watchdog.log'
$post = 0; if (Test-Path $log) { $post = @(Select-String -Path $log -Pattern '/restart/' -SimpleMatch).Count }
$state = Test-Path 'C:\Users\houss\helios-state\vm-power-state.json'
$flags = @(Get-ChildItem 'C:\Users\houss\helios-maintenance' -Filter '*.flag').Count
[pscustomobject]@{
  ram_dispo_mo = [int]($os.FreePhysicalMemory/1024); ram_total_mo = [int]($os.TotalVisibleMemorySize/1024)
  disque_c_libre_go = [math]::Round($c.Free/1GB,1); disque_c_libre_pct = [math]::Round(100*$c.Free/($c.Used+$c.Free),1)
  dernier_boot = [string]$os.LastBootUpTime; taches = $tasks; pare_feu = $fw
  ecouteur_9099 = $l9099; ecouteur_9999 = $l9999; post_restart_dans_journal = $post
  fichier_etat_present = [bool]$state; drapeaux_maintenance = $flags
} | ConvertTo-Json -Compress -Depth 4
"""


def run(args, timeout=60, stdin=None):
    try:
        p = subprocess.run(args, input=stdin, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except Exception as ex:  # noqa: BLE001 - un relevé ne doit jamais s'arrêter sur une sonde
        return 255, "", f"{type(ex).__name__}: {ex}"


def kube(*a, timeout=60, stdin=None):
    return run(["kubectl", *a], timeout=timeout, stdin=stdin)


def collect_pods(errors):
    rc, out, err = kube("get", "pods", "-n", NS, "-o", "json")
    pods = []
    if rc != 0:
        errors.append("kubectl get pods: " + err.strip()[:120])
        return pods
    for it in json.loads(out).get("items", []):
        name = it["metadata"]["name"]
        if not name.startswith("backend-"):
            continue
        cs = (it["status"].get("containerStatuses") or [{}])[0]
        pods.append({"name": name, "node": it["spec"].get("nodeName"), "ready": cs.get("ready"),
                     "restarts": cs.get("restartCount"), "start": it["status"].get("startTime"),
                     "image": it["spec"]["containers"][0]["image"]})
    for p in pods:
        rc, out, _ = kube("exec", "-n", NS, p["name"], "--", "sh", "-c", "curl -s -m 8 localhost:8080/actuator/prometheus",
                          timeout=40)
        for line in out.splitlines():
            if line.startswith("tasks_scheduled_execution_seconds_") and "VmPowerWatch" in line and 'code_function="cycle"' in line:
                key = line.split("{", 1)[0].rsplit("_", 1)[1]
                p["cycle_" + key] = float(line.rsplit(" ", 1)[1])
        rc, out, _ = kube("logs", "-n", NS, p["name"], timeout=60)
        lines = out.splitlines()
        p["log_lignes"] = len(lines)
        p["log_error"] = sum(1 for x in lines if " ERROR " in x)
        p["log_vmpower_warn"] = sum(1 for x in lines if "VmPowerWatch" in x and (" WARN " in x or " ERROR " in x))
        p["log_vmpower_derniere"] = next((x[:200] for x in reversed(lines) if "VmPowerWatch" in x), None)
    return pods


def collect_config(errors):
    rc, out, err = kube("get", "cm", "backend-config", "-n", NS, "-o", "json")
    if rc != 0:
        errors.append("configmap: " + err.strip()[:120])
        return {}
    data = json.loads(out).get("data", {})
    return {k: data.get(k) for k in ("HELIOS_VMPOWER_ENABLED", "HELIOS_VMPOWER_DRYRUN", "HELIOS_VMPOWER_ACTIVE_VMS")}


def count_watchdog_starts(since_local, log_path=None):
    """Nombre de `vmrun start` EXÉCUTÉS par le watchdog depuis `since_local` (heure locale) : lignes d'évènement VMRUN_START du journal
    JSON (horodatage UTC). Journal illisible/absent => None (jamais 0 par défaut)."""
    try:
        since_utc = dt.datetime.strptime(since_local, "%Y-%m-%d %H:%M:%S").astimezone(dt.timezone.utc)
        n = 0
        with open(log_path or WATCHDOG_LOG, encoding="utf-8", errors="replace") as fh:
            for ln in fh:
                try:
                    d = json.loads(ln)
                except ValueError:
                    continue
                if d.get("event") == "VMRUN_START":
                    ts = dt.datetime.strptime(d["ts"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=dt.timezone.utc)
                    if ts >= since_utc:
                        n += 1
        return n
    except Exception:  # noqa: BLE001
        return None


def collect_db(errors):
    rc, out, _ = kube("get", "pods", "-n", NS, "-l", "cnpg.io/instanceRole=primary", "-o", "jsonpath={.items[0].metadata.name}")
    primary = out.strip()
    if rc != 0 or not primary:
        errors.append("primaire PostgreSQL introuvable")
        return {}
    rc, out, err = kube("exec", "-i", "-n", NS, primary, "-c", "postgres", "--", "psql", "-U", "postgres", "-d", "pfe_db",
                        "-At", "-F", "|", "-f", "-", timeout=60, stdin=build_sql(OBS["since"]))
    if rc != 0:
        errors.append("psql: " + err.strip()[:160])
    res = {"primaire": primary}
    for line in out.splitlines():
        if "|" in line:
            k, v = line.split("|", 1)
            res[k] = int(v) if re.fullmatch(r"-?\d+", v) else v
    return res


def collect_vm_state(leader, errors):
    if not leader:
        return {}
    cmd = ('curl -s -m 12 -H "X-Helios-Token: $REMEDIATION_MASTER_WATCHDOG_TOKEN" '
           '"$REMEDIATION_MASTER_WATCHDOG_URL/vm/state"')
    rc, out, err = kube("exec", "-n", NS, leader, "--", "sh", "-c", cmd, timeout=40)
    try:
        data = json.loads(out)
    except Exception:  # noqa: BLE001
        errors.append("watchdog /vm/state illisible")
        return {}
    vms = data.get("vms", data)
    res = {}
    for name, v in sorted(vms.items()):
        if not isinstance(v, dict):
            continue
        w, e = v.get("watchdog", {}), v.get("evidence", {})
        res[name] = {"mode": v.get("mode"), "etat": v.get("power_state"), "age_ms": e.get("age_ms"),
                     "refus": w.get("refusal_codes"), "ram_mo": w.get("host_ram_mb"),
                     "cooldown_s": w.get("cooldown_remaining_s"), "dernier_demarrage": w.get("last_start")}
    return res


def collect_host(errors):
    with tempfile.NamedTemporaryFile("w", suffix=".ps1", delete=False, encoding="utf-8-sig") as f:
        f.write(PS_HOST)
        path = f.name
    try:
        rc, out, err = run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", path], timeout=90)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    try:
        return json.loads(out.strip().splitlines()[-1])
    except Exception:  # noqa: BLE001
        errors.append("hôte: " + (err or out).strip()[:160])
        return {}


def collect_bridge(errors):
    cmd = (f"echo sha=$(sudo sha256sum {BRIDGE_DIR}/bridge.py | cut -c1-12); "
           "echo jeton=$(sudo grep -c HELIOS_WATCHDOG_TOKEN /etc/helios/bridge.env); "
           "echo actif=$(systemctl is-active helios-ssh-bridge); "
           "echo relances=$(systemctl show -p NRestarts --value helios-ssh-bridge)")
    rc, out, err = run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "helios161", cmd], timeout=40)
    res = {}
    for line in out.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            res[k.strip()] = v.strip()
    if not res:
        errors.append("bridge: " + err.strip()[:120])
    return res


def verdict_armed(rec):
    """Observation de la configuration FINALE (rallumage réel actif pour .155 .158 .162) SANS arrêt de VM provoqué."""
    fails, warns = [], []
    db, host, cfg = rec.get("db", {}), rec.get("hote", {}), rec.get("config", {})
    actives = {x.strip() for x in (cfg.get("HELIOS_VMPOWER_ACTIVE_VMS") or "").split(",") if x.strip()}
    if cfg and (cfg.get("HELIOS_VMPOWER_ENABLED") != "true" or cfg.get("HELIOS_VMPOWER_DRYRUN") != "false" or actives != ARMED_ACTIVE):
        fails.append("configuration d'armement NON conforme (attendu ENABLED=true, DRYRUN=false, ACTIVE_VMS = k8s-worker1,k8s-worker2,k8s-lb2,k8s-master) : "
                     + json.dumps(cfg))
    if db.get("restart_vm_interdits", 0):
        fails.append(f"{db['restart_vm_interdits']} action(s) RESTART_VM visant k8s-monitoring (INTERDIT : détection seule)")
    if db.get("restart_vm_depuis_ombre", 0):
        fails.append(f"{db['restart_vm_depuis_ombre']} action(s) RESTART_VM depuis le début de la fenêtre — aucun arrêt n'a été provoqué : "
                     "vraie panne ou faux positif, ANALYSE REQUISE")
    n = host.get("vmrun_start_depuis_fenetre")
    if n:
        fails.append(f"{n} `vmrun start` exécuté(s) par le watchdog depuis le début de la fenêtre — analyse requise")
    if n is None and host:
        warns.append("journal du watchdog illisible : démarrages non comptés")
    if db.get("actions_vm_bloquees", 0):
        fails.append("action RESTART_VM bloquée (PENDING/IN_PROGRESS)")
    if db.get("incidents_vm_depuis_ombre", 0):
        warns.append(f"{db['incidents_vm_depuis_ombre']} incident(s) de périmètre VM créé(s) depuis le début de la fenêtre (vraie panne ?)")
    if db.get("incidents_vm_ouverts", 0):
        warns.append(f"{db['incidents_vm_ouverts']} incident(s) de périmètre VM encore ouvert(s)")
    for p in rec.get("pods", []):
        if p.get("log_error"):
            warns.append(f"{p['name']}: {p['log_error']} ligne(s) ERROR cumulée(s)")
        if p.get("restarts"):
            warns.append(f"{p['name']}: {p['restarts']} redémarrage(s)")
    for name, v in (rec.get("vms") or {}).items():
        if v.get("etat") != "RUNNING":
            warns.append(f"{name} est {v.get('etat')} (attendu RUNNING hors panne réelle)")
        attendu = {"k8s-monitoring": "DETECT_ONLY"}.get(name, "AUTO")
        if v.get("mode") != attendu:
            (fails if name == "k8s-monitoring" else warns).append(
                f"{name} n'est pas {attendu} côté watchdog ({v.get('mode')})")
    if host:
        if host.get("ram_dispo_mo", 99999) < RAM_GUARD_MB + 300:
            warns.append(f"RAM hôte disponible {host['ram_dispo_mo']} Mo : marge faible sous la garde de {RAM_GUARD_MB} Mo")
        if host.get("disque_c_libre_pct", 100) < 5:
            warns.append(f"disque C: libre {host['disque_c_libre_pct']} %")
        if host.get("ecouteur_9099") != 1:
            fails.append("le watchdog n'écoute pas sur 192.168.148.1:9099")
        if host.get("ecouteur_9999"):
            fails.append("un écouteur :9999 (ancien agent) est de retour")
    br = rec.get("bridge", {})
    if br and (br.get("sha") != TARGET_BRIDGE_SHA12 or br.get("jeton") not in ("0",) or br.get("actif") != "active"):
        fails.append("Bridge non conforme : " + json.dumps(br))
    for e in rec.get("erreurs", []):
        warns.append("relevé incomplet : " + e)
    return ("FAIL" if fails else ("WARN" if warns else "OK")), fails, warns


def verdict(rec):
    if (rec.get("observation") or {}).get("mode") == "armee":
        return verdict_armed(rec)
    fails, warns = [], []
    db, host, cfg = rec.get("db", {}), rec.get("hote", {}), rec.get("config", {})
    if db.get("restart_vm_depuis_ombre", 0):
        fails.append(f"{db['restart_vm_depuis_ombre']} action(s) RESTART_VM créée(s) depuis le début du mode ombre")
    if host.get("post_restart_dans_journal", 0):
        fails.append(f"{host['post_restart_dans_journal']} POST /restart reçu(s) par le watchdog")
    if db.get("actions_vm_bloquees", 0):
        fails.append("action RESTART_VM bloquée (PENDING/IN_PROGRESS)")
    if db.get("incidents_vm_depuis_ombre", 0):
        warns.append(f"{db['incidents_vm_depuis_ombre']} incident(s) de périmètre VM créé(s) depuis le début (vérifier : vraie panne ?)")
    if db.get("incidents_vm_ouverts", 0):
        warns.append(f"{db['incidents_vm_ouverts']} incident(s) de périmètre VM encore ouvert(s)")
    if cfg and (cfg.get("HELIOS_VMPOWER_DRYRUN") != "true" or cfg.get("HELIOS_VMPOWER_ACTIVE_VMS")):
        warns.append("la configuration n'est PLUS en mode ombre pur : " + json.dumps(cfg))
    for p in rec.get("pods", []):
        if p.get("log_error"):
            warns.append(f"{p['name']}: {p['log_error']} ligne(s) ERROR cumulée(s)")
        if p.get("restarts"):
            warns.append(f"{p['name']}: {p['restarts']} redémarrage(s)")
    for name, v in (rec.get("vms") or {}).items():
        if v.get("etat") != "RUNNING":
            warns.append(f"{name} est {v.get('etat')} (attendu RUNNING hors essai autorisé)")
        attendu = "DETECT_ONLY" if name == "k8s-monitoring" else "AUTO"   # .160 : détection seule (consigne finale du 6 nov. 2026)
        if v.get("mode") != attendu:
            warns.append(f"{name} n'est pas {attendu} côté watchdog ({v.get('mode')})"
                         + (" — redéployer le watchdog de l'hôte (politique du dépôt : détection seule)" if name == "k8s-monitoring" else ""))
    if host:
        if host.get("ram_dispo_mo", 99999) < RAM_GUARD_MB + 300:
            warns.append(f"RAM hôte disponible {host['ram_dispo_mo']} Mo : marge faible sous la garde de {RAM_GUARD_MB} Mo")
        if host.get("disque_c_libre_pct", 100) < 5:
            warns.append(f"disque C: libre {host['disque_c_libre_pct']} %")
        if host.get("ecouteur_9099") != 1:
            fails.append("le watchdog n'écoute pas sur 192.168.148.1:9099")
        if host.get("ecouteur_9999"):
            fails.append("un écouteur :9999 (ancien agent) est de retour")
    br = rec.get("bridge", {})
    if br and (br.get("sha") != TARGET_BRIDGE_SHA12 or br.get("jeton") not in ("0",) or br.get("actif") != "active"):
        fails.append("Bridge non conforme : " + json.dumps(br))
    for e in rec.get("erreurs", []):
        warns.append("relevé incomplet : " + e)
    return ("FAIL" if fails else ("WARN" if warns else "OK")), fails, warns


def cadence(prev, cur):
    """Période (s) de VmPowerWatch.cycle par pod entre deux relevés."""
    out = {}
    if not prev:
        return out
    t0, t1 = dt.datetime.fromisoformat(prev["ts"]), dt.datetime.fromisoformat(cur["ts"])
    dts = (t1 - t0).total_seconds()
    pp = {p["name"]: p for p in prev.get("pods", [])}
    for p in cur.get("pods", []):
        q = pp.get(p["name"])
        if q and "cycle_count" in p and "cycle_count" in q and p["cycle_count"] > q["cycle_count"] and dts > 0:
            n = p["cycle_count"] - q["cycle_count"]
            work = (p.get("cycle_sum", 0) - q.get("cycle_sum", 0)) / n
            out[p["name"]] = {"cycles": int(n), "periode_s": round(dts / n, 2), "duree_moy_s": round(work, 2)}
    return out


def snapshot(prev):
    errors = []
    rec = {"ts": dt.datetime.now().astimezone().isoformat(timespec="seconds")}
    rec["pods"] = collect_pods(errors)
    rec["config"] = collect_config(errors)
    rec["db"] = collect_db(errors)
    leader = next((p["name"] for p in rec["pods"] if rec["db"].get("bail_leader", "").startswith(p["name"])), None)
    rec["leader"] = leader
    rec["vms"] = collect_vm_state(leader or (rec["pods"][0]["name"] if rec["pods"] else None), errors)
    rec["hote"] = collect_host(errors)
    rec["bridge"] = collect_bridge(errors)
    if OBS["armed"]:
        rec["observation"] = {"mode": "armee", "depuis": OBS["since"]}
        rec["hote"]["vmrun_start_depuis_fenetre"] = count_watchdog_starts(OBS["since"])
    rec["erreurs"] = errors
    rec["cadence"] = cadence(prev, rec)
    rec["verdict"], rec["fails"], rec["warns"] = verdict(rec)
    return rec


def safe_snapshot(prev):
    """Un relevé ne doit jamais interrompre l'observation : toute exception devient un relevé « incomplet »."""
    try:
        return snapshot(prev)
    except Exception as ex:  # noqa: BLE001
        msg = f"{type(ex).__name__}: {ex}"
        return {"ts": dt.datetime.now().astimezone().isoformat(timespec="seconds"), "pods": [], "config": {}, "db": {},
                "vms": {}, "hote": {}, "bridge": {}, "erreurs": [msg], "cadence": {}, "verdict": "WARN", "fails": [],
                "warns": ["relevé incomplet : " + msg], "invalide": "relevé interrompu par une exception : " + msg}


def events(prev, cur):
    """Événements qui méritent qu'un humain (ou l'assistant) regarde tout de suite — l'observation s'arrête alors."""
    evs = []
    if cur.get("invalide"):
        return evs
    if cur.get("verdict") == "FAIL":
        evs += ["FAIL : " + f for f in cur.get("fails", [])]
    for n, v in (cur.get("vms") or {}).items():
        if v.get("etat") != "RUNNING":
            evs.append(f"{n} n'est plus RUNNING ({v.get('etat')}) — "
                       + ("panne réelle : la chaîne automatique doit agir (ou, pour .160/.153, ne rien faire)" if (cur.get("observation") or {}).get("mode") == "armee"
                          else "premier vrai test des décisions du mode ombre"))
    if prev and not prev.get("invalide"):
        pd, cd = prev.get("db", {}), cur.get("db", {})
        for k, label in (("lignes_mode_ombre", "nouvelle(s) ligne(s) « MODE OMBRE »"),
                         ("incidents_vm_depuis_ombre", "nouvel incident de périmètre VM"),
                         ("lignes_non_autorise", "nouvelle(s) ligne(s) « NON autorisé »")):
            if cd.get(k, 0) > pd.get(k, 0):
                evs.append(f"{cd[k] - pd.get(k, 0)} {label}")
        if prev.get("leader") and cur.get("leader") and prev["leader"] != cur["leader"]:
            evs.append(f"changement de leader {prev['leader']} → {cur['leader']}")
        pr = {p["name"]: p.get("restarts", 0) for p in prev.get("pods", [])}
        for p in cur.get("pods", []):
            if p.get("restarts", 0) > pr.get(p["name"], p.get("restarts", 0)):
                evs.append(f"{p['name']} a redémarré")
    return evs


def load_all():
    if not os.path.isfile(OUT_FILE):
        return []
    with open(OUT_FILE, encoding="utf-8") as f:
        return [json.loads(x) for x in f if x.strip()]


def save(rec):
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def show(rec):
    print(f"\n=== Relevé {rec['ts']}  →  {rec['verdict']}")
    print("  config   :", json.dumps(rec.get("config", {})))
    print("  leader   :", rec.get("leader"), "| bail âgé de", rec.get("db", {}).get("bail_age_s"), "s")
    for p in rec.get("pods", []):
        print(f"  pod {p['name']:<28} {p.get('node'):<12} redémarrages={p.get('restarts')} cycles={int(p.get('cycle_count', 0))} "
              f"ERROR={p.get('log_error')} WARN-VmPowerWatch={p.get('log_vmpower_warn')}")
    for n, c in (rec.get("cadence") or {}).items():
        print(f"  cadence  {n:<28} {c['cycles']} cycles, période {c['periode_s']} s (durée moyenne du travail {c['duree_moy_s']} s)")
    for n, v in (rec.get("vms") or {}).items():
        print(f"  VM {n:<15} {v['mode']:<13} {v['etat']:<11} preuve {v['age_ms']} ms  refus={v['refus']}")
    h = rec.get("hote", {})
    if h:
        print(f"  hôte     : RAM dispo {h.get('ram_dispo_mo')} Mo (garde {RAM_GUARD_MB}) | C: libre {h.get('disque_c_libre_pct')} % | "
              f"9099 écoute={h.get('ecouteur_9099')} 9999={h.get('ecouteur_9999')} | POST /restart={h.get('post_restart_dans_journal')}")
    d = rec.get("db", {})
    print(f"  base     : RESTART_VM depuis ombre={d.get('restart_vm_depuis_ombre')} (total {d.get('restart_vm_total')}) | "
          f"lignes MODE OMBRE={d.get('lignes_mode_ombre')} | incidents VM depuis ombre={d.get('incidents_vm_depuis_ombre')} "
          f"ouverts={d.get('incidents_vm_ouverts')} | preuves récupération={d.get('preuves_recuperation_vm')}")
    print("  bridge   :", json.dumps(rec.get("bridge", {})))
    for f in rec.get("fails", []):
        print("  ✗ FAIL :", f)
    for w in rec.get("warns", []):
        print("  ! WARN :", w)


def report():
    allrecs = load_all()
    recs = [r for r in allrecs if not r.get("invalide")]
    if not recs:
        print("aucun relevé valide enregistré")
        return
    first, last = recs[0], recs[-1]
    print(f"\n######## SYNTHÈSE DE L'OBSERVATION — {len(recs)} relevé(s) valide(s) du {first['ts']} au {last['ts']}")
    for r in allrecs:
        if r.get("invalide"):
            print(f"  (relevé {r['ts']} écarté : {r['invalide']})")
    counts = {k: sum(1 for r in recs if r["verdict"] == k) for k in ("OK", "WARN", "FAIL")}
    print("  verdicts :", counts)
    tot = {}  # pod -> [secondes cumulées, cycles cumulés, nb d'intervalles] ; la période se calcule sur le TOTAL (un
    # intervalle isolé est quantifié à ±1 cycle)
    for r in recs:
        for n, c in (r.get("cadence") or {}).items():
            if c["duree_moy_s"] > 0.3:  # pod leader (le travail réel) ; le non-leader ne fait qu'un contrôle de bail
                t = tot.setdefault(n, [0.0, 0, 0])
                t[0] += c["periode_s"] * c["cycles"]
                t[1] += c["cycles"]
                t[2] += 1
    for n, (secs, cyc, k) in tot.items():
        print(f"  cadence du leader {n} : {cyc} cycles en {secs/3600:.2f} h → une exécution toutes les {secs/cyc:.2f} s "
              f"(15 s de délai fixe + durée du travail) sur {k} intervalle(s)")
    rams = [r["hote"]["ram_dispo_mo"] for r in recs if r.get("hote")]
    if rams:
        print(f"  RAM hôte disponible : min {min(rams)} Mo / max {max(rams)} Mo (garde du watchdog : {RAM_GUARD_MB} Mo)")
    leaders = sorted({r.get("leader") for r in recs if r.get("leader")})
    print("  leaders vus :", leaders)
    for r in recs:
        for f in r.get("fails", []):
            print(f"  ✗ {r['ts']} FAIL : {f}")
    seen = set()
    for r in recs:
        for w in r.get("warns", []):
            if w not in seen:
                seen.add(w)
                print(f"  ! {r['ts']} WARN : {w}")
    ok = counts["FAIL"] == 0
    armed = (last.get("observation") or {}).get("mode") == "armee"
    if armed:
        print("  CONCLUSION :", "AUCUN démarrage ni action RESTART_VM sans panne, configuration d'armement conforme sur toute la fenêtre "
              f"(depuis {last['observation']['depuis']})" if ok else "au moins un FAIL pendant la fenêtre d'observation armée : voir ci-dessus")
    else:
        print("  CONCLUSION :", "aucun démarrage demandé, mode ombre respecté sur toute la période observée" if ok
              else "le mode ombre a été VIOLÉ : voir les FAIL ci-dessus")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--loop", type=int, metavar="SECONDES", help="intervalle entre deux relevés")
    ap.add_argument("--until", metavar="AAAA-MM-JJTHH:MM", help="heure LOCALE de fin de la boucle")
    ap.add_argument("--report", action="store_true", help="synthèse des relevés enregistrés, sans nouveau relevé")
    ap.add_argument("--armed", action="store_true",
                    help="observer la configuration FINALE armée (.155 .158 .162) sans arrêt de VM ; relevés dans armed-observations.jsonl")
    ap.add_argument("--since", metavar="AAAA-MM-JJ HH:MM:SS",
                    help="début de la fenêtre d'observation (heure LOCALE) ; défaut : maintenant avec --armed")
    ap.add_argument("--stop-on-event", action="store_true",
                    help="en mode --loop : s'arrêter dès qu'une VM n'est plus RUNNING, qu'une ligne « MODE OMBRE » ou un "
                         "incident de périmètre VM apparaît, que le leader change, qu'un pod redémarre ou qu'un FAIL survient")
    a = ap.parse_args(argv)
    global OUT_FILE
    if a.armed:
        OBS["armed"] = True
        OBS["since"] = a.since or dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        OUT_FILE = ARMED_OUT_FILE
    if a.report:
        report()
        return 0
    prev = (load_all() or [None])[-1]
    end = dt.datetime.fromisoformat(a.until).astimezone() if a.until else None
    stopped_on_event = False
    while True:
        rec = safe_snapshot(prev)
        save(rec)
        show(rec)
        evs = events(prev, rec)
        prev = rec
        if a.loop and a.stop_on_event and evs:
            print("\n>>> ÉVÉNEMENT — observation interrompue pour analyse :")
            for e in evs:
                print("   -", e)
            stopped_on_event = True
            break
        if not a.loop or (end and dt.datetime.now().astimezone() + dt.timedelta(seconds=a.loop) > end):
            break
        time.sleep(a.loop)
    if a.loop:
        report()
    if stopped_on_event:
        return 2
    return 1 if rec["verdict"] == "FAIL" else 0


if __name__ == "__main__":
    sys.exit(main())
