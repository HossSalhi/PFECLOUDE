# -*- coding: utf-8 -*-
"""OBSERVATION PASSIVE de stabilité sur 24 h — complément de shadow_observe.py (LECTURE SEULE STRICTE).

Ce que fait cet outil : un relevé = une ligne JSON ajoutée à
``C:\\Users\\houss\\helios-state\\observation\\stabilite-24h.jsonl`` + un rapport Markdown glissant. Il lit : l'API Kubernetes (/readyz), les nœuds,
les pods de tous les espaces de noms (redémarrages, anomalies), les évènements Warning, la santé des 5 membres etcd (:2381), Camunda (topologie
et santé), le Bridge (/sante), CNPG (phase, réplication, retard, archiveur WAL), la conformité des politiques (policy_reconcile.py), les deux
HAProxy (page de stats :8404), les sondes HA-01 déjà en place (chemins HAProxy/Ingress/API : disponibilité, séries d'échecs, latences), la
résolution DNS, les incidents/actions/escalades/doublons depuis le début de la fenêtre.
Ce qu'il ne fait JAMAIS : arrêter/démarrer une VM, créer un incident, injecter une charge, lancer une remédiation, modifier une politique ou une
ressource du cluster, écrire en base, envoyer une requête POST/PUT/DELETE. Preuve par analyse statique : scripts/helios-ops/test_observe_passive.py.

Usage
  python passive_observe.py --since "2026-11-06 22:00:00" --until "2026-11-07 22:00:00"      un relevé (appelé toutes les 15 min par la tâche planifiée)
  python passive_observe.py --since ... --until ... --loop 900                              boucle (alternative sans tâche planifiée)
  python passive_observe.py --since ... --until ... --report                                rapport sans nouveau relevé
  python passive_observe.py --annotate "texte"                                              consigne une intervention humaine/assistant dans la chronologie

Le rapport indique « EN COURS » tant que l'heure de fin n'est pas atteinte : les 24 heures ne sont JAMAIS annoncées terminées avant leur expiration.
"""
import argparse
import csv
import ctypes
import datetime as dt
import glob
import io
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # pragma: no cover
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
STATE_DIR = r"C:\Users\houss\helios-state\observation"
OUT_FILE = os.path.join(STATE_DIR, "stabilite-24h.jsonl")
ANNOT_FILE = os.path.join(STATE_DIR, "annotations.jsonl")
REPORT_FILE = os.path.join(STATE_DIR, "stabilite-24h-rapport.md")
ARMED_FILE = os.path.join(REPO, "infra", "helios-sot", "observation", "armed-observations.jsonl")
NS = "pfe"
ETCD_IPS = ["192.168.148.153", "192.168.148.155", "192.168.148.158", "192.168.148.160", "192.168.148.162"]
HAPROXY = {"160": "192.168.148.160", "162": "192.168.148.162"}
CAMUNDA_REST = "http://192.168.148.161:8080"
CAMUNDA_HEALTH = "http://192.168.148.161:9600"
NODE_NAMES = ["k8s-master", "k8s-worker1", "k8s-worker2"]
DNS_NAMES = ["helios.orange.tn", "api.helios.orange.tn"]
WINDOW_S = 960            # fenêtre d'analyse des sondes HA-01 et des évènements : un relevé + marge
LAG_MAX_BYTES = 1048576   # 1 Mo
PG_WAL_WARN_BYTES = 2 * 1024 * 1024 * 1024   # 2 Go (volume de données de 5 Gi)
SERIE_MAX_S = 30.0        # une série d'échecs d'une sonde au-delà de 30 s = FAIL
EVENTS_WARN = 30
EXPECTED_EVERY_S = 900
VM_DIR = r"C:\Users\houss\Documents\Virtual Machines"
VM_NAMES = ("k8s-master", "k8s-worker1", "k8s-worker2", "k8s-monitoring", "k8s-aiCamunda", "k8s-lb2")
RAM_WARN_MB = 1500        # sous ce seuil de mémoire disponible l'hôte pagine les VM (11 nov. 2026 : 110-430 Mo et ~35 000 pages/s relues)
SCOPE_VM_SQL = ("((namespace='k8s-nodes' and app in ('k8s-worker1','k8s-worker2','k8s-master') and incident_type='NODE_NOT_READY') "
                "or (namespace='infra' and app in ('k8s-lb2','k8s-monitoring') and incident_type in ('VM_DOWN','VM_UNREACHABLE')))")


# ───────────────────────── accès (les seuls points d'entrée vers l'extérieur) ─────────────────────────

def run(args, timeout=60, stdin=None):
    try:
        p = subprocess.run(args, input=stdin, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except Exception as ex:  # noqa: BLE001 - un relevé ne doit jamais s'arrêter sur une sonde
        return 255, "", f"{type(ex).__name__}: {ex}"


def kube(*a, timeout=60, stdin=None):
    return run(["kubectl", *a], timeout=timeout, stdin=stdin)


def http_get(url, timeout=6):
    """GET simple (aucun corps, aucune méthode d'écriture). Rend (code HTTP ou None, texte, durée en ms)."""
    t0 = time.time()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            body = r.read(4 * 1024 * 1024).decode("utf-8", "replace")
            return r.status, body, int((time.time() - t0) * 1000)
    except Exception as ex:  # noqa: BLE001
        code = getattr(ex, "code", None)
        return code, "", int((time.time() - t0) * 1000)


def now_iso():
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def parse_iso(s):
    return dt.datetime.fromisoformat(s)


def safe(errors, name, fn, *a, **k):
    """Un collecteur ne doit jamais interrompre le relevé : toute exception devient une erreur consignée."""
    try:
        return fn(*a, **k)
    except Exception as ex:  # noqa: BLE001
        errors.append(f"{name}: {type(ex).__name__}: {ex}"[:200])
        return {}


# ───────────────────────── collecteurs ─────────────────────────

def collect_api(errors):
    t0 = time.time()
    rc, out, err = kube("get", "--raw", "/readyz?verbose", timeout=40)
    ms = int((time.time() - t0) * 1000)
    failed = re.findall(r"^\[-\](\S+)", out, re.M)
    ok = rc == 0 and "readyz check passed" in out and not failed
    if rc != 0:
        errors.append("api /readyz: " + err.strip()[:120])
    return {"ok": ok, "latence_ms": ms, "echecs": failed}


def collect_nodes(errors):
    rc, out, err = kube("get", "nodes", "-o", "json", timeout=40)
    if rc != 0:
        errors.append("nodes: " + err.strip()[:120])
        return {}
    res = {}
    for it in json.loads(out).get("items", []):
        conds = {c["type"]: c["status"] for c in it["status"].get("conditions", [])}
        res[it["metadata"]["name"]] = {
            "ready": conds.get("Ready") == "True",
            "pressions": [t for t in ("MemoryPressure", "DiskPressure", "PIDPressure", "NetworkUnavailable") if conds.get(t) == "True"],
            "cordon": bool(it["spec"].get("unschedulable")),
        }
    return res


def parse_metric(text, name):
    m = re.search(r"^%s(?:\{[^}]*\})?\s+([0-9.eE+-]+)" % re.escape(name), text, re.M)
    return float(m.group(1)) if m else None


def collect_etcd(errors):
    members, leader, changes = {}, None, None
    for ip in ETCD_IPS:
        code, body, ms = http_get(f"http://{ip}:2381/health", 5)
        members[ip.rsplit(".", 1)[1]] = bool(code == 200 and '"health":"true"' in body.replace(" ", ""))
        code, body, _ = http_get(f"http://{ip}:2381/metrics", 8)
        if code == 200:
            if parse_metric(body, "etcd_server_is_leader") == 1.0:
                leader = ip.rsplit(".", 1)[1]
            c = parse_metric(body, "etcd_server_leader_changes_seen_total")
            if c is not None:
                changes = max(changes or 0, int(c))
    return {"membres": members, "leader": leader, "changements_leader_cumules": changes}


def collect_camunda(errors):
    code, body, ms = http_get(CAMUNDA_REST + "/v2/topology", 8)
    parts, sains = 0, 0
    try:
        for b in json.loads(body).get("brokers", []):
            for p in b.get("partitions", []):
                parts += 1
                sains += 1 if p.get("health") == "healthy" else 0
    except ValueError:
        pass
    code2, body2, ms2 = http_get(CAMUNDA_HEALTH + "/actuator/health", 8)
    status = None
    try:
        status = json.loads(body2).get("status")
    except ValueError:
        pass
    return {"topologie_http": code, "partitions": parts, "partitions_saines": sains, "latence_ms": ms, "sante_http": code2, "sante": status}


def collect_bridge(errors):
    rc, out, err = run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "helios161",
                        'curl -s -m 5 -o /dev/null -w "%{http_code}" http://127.0.0.1:8099/sante'], timeout=30)
    code = out.strip()[-3:] if rc == 0 else None
    if rc != 0:
        errors.append("bridge: " + err.strip()[:120])
    return {"sante_http": int(code) if code and code.isdigit() else None}


def collect_cnpg(errors):
    rc, out, err = kube("get", "cluster.postgresql.cnpg.io", "postgres", "-n", NS, "-o", "json", timeout=40)
    if rc != 0:
        errors.append("cnpg: " + err.strip()[:120])
        return {}
    c = json.loads(out)
    st = c.get("status", {})
    res = {"phase": st.get("phase"), "prets": st.get("readyInstances"), "instances": c.get("spec", {}).get("instances"),
           "primaire": st.get("currentPrimary"), "replication": [], "archiveur": {}}
    primary = res["primaire"]
    if not primary:
        return res
    sql = ("select 'repl', application_name, state, sync_state, coalesce(pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn),-1)::bigint from pg_stat_replication;\n"
           "select 'arch', archived_count, failed_count, coalesce(extract(epoch from (now()-last_archived_time))::int,-1), coalesce(last_archived_wal,'-') from pg_stat_archiver;\n"
           "select 'cfg', current_setting('archive_mode'), current_setting('wal_level'), replace(current_setting('archive_command'), '|', '/'), current_setting('wal_keep_size');\n"
           "select 'taille', pg_database_size('pfe_db');\n"
           "select 'wal', count(*), coalesce(sum(size), 0)::bigint from pg_ls_waldir();\n")
    rc, out, err = kube("exec", "-i", "-n", NS, primary, "-c", "postgres", "--", "psql", "-U", "postgres", "-d", "pfe_db", "-At", "-F", "|", "-f", "-",
                        timeout=60, stdin=sql)
    if rc != 0:
        errors.append("cnpg psql: " + err.strip()[:120])
    for line in out.splitlines():
        p = line.split("|")
        if p[0] == "repl" and len(p) >= 5:
            res["replication"].append({"app": p[1], "etat": p[2], "sync": p[3], "retard_octets": int(p[4])})
        elif p[0] == "arch" and len(p) >= 5:
            res["archiveur"] = {"archives": int(p[1]), "echecs": int(p[2]), "age_dernier_s": int(p[3]), "dernier_wal": p[4]}
        elif p[0] == "cfg" and len(p) >= 5:
            res["config"] = {"archive_mode": p[1], "wal_level": p[2], "archive_command": p[3][:80], "wal_keep_size": p[4]}
        elif p[0] == "taille" and len(p) >= 2:
            res["taille_pfe_db_octets"] = int(p[1])
        elif p[0] == "wal" and len(p) >= 3:
            res["pg_wal"] = {"fichiers": int(p[1]), "octets": int(p[2])}
    return res


def collect_policies(errors):
    script = os.path.join(HERE, "policy_reconcile.py")
    rc, out, err = run([sys.executable, "-I", script], timeout=240)
    lines = [x for x in out.splitlines() if x.strip()]
    return {"coherent": rc == 0 and bool(lines) and lines[-1].startswith("COHÉRENT"),
            "code": rc, "ecarts": [x.strip()[:160] for x in lines if x.strip().startswith("-") or "ÉCART" in x][:8]}


def parse_haproxy_csv(text):
    """Rend {proxy: {"serveurs": n, "up": n, "down": [noms]}} (les lignes FRONTEND/BACKEND sont comptées à part)."""
    res = {}
    rows = csv.reader(io.StringIO(text.lstrip("# ")))
    header = next(rows, None)
    if not header:
        return res
    idx = {n: i for i, n in enumerate(header)}
    for r in rows:
        if len(r) <= idx.get("status", 99):
            continue
        px, sv, status = r[idx["pxname"]], r[idx["svname"]], r[idx["status"]]
        d = res.setdefault(px, {"serveurs": 0, "up": 0, "down": [], "frontend": None})
        if sv == "FRONTEND":
            d["frontend"] = status
        elif sv == "BACKEND":
            continue
        else:
            d["serveurs"] += 1
            if status.startswith("UP"):
                d["up"] += 1
            else:
                d["down"].append(sv + ":" + status)
    return res


def collect_haproxy(errors):
    res = {}
    for name, ip in HAPROXY.items():
        code, body, ms = http_get(f"http://{ip}:8404/;csv", 8)
        res[name] = {"http": code, "latence_ms": ms, "proxies": parse_haproxy_csv(body) if code == 200 else {}}
    return res


def find_probe_file():
    cands = glob.glob(os.path.join(REPO, "infra", "ha01", "results", "*", "probe-baseline-p0.jsonl"))
    return max(cands, key=os.path.getmtime) if cands else None


def parse_probe_ts(s):
    return dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=dt.timezone.utc).timestamp()


def analyse_probes(lines, now_ts, window_s, interval=1.0):
    by = {}
    cutoff = now_ts - window_s
    last_ts = None
    for ln in lines:
        try:
            r = json.loads(ln)
            t = parse_probe_ts(r["ts"])
        except (ValueError, KeyError):
            continue
        last_ts = t if last_ts is None or t > last_ts else last_ts
        if t >= cutoff:
            by.setdefault(r.get("probe", "?"), []).append((t, bool(r.get("ok")), r.get("ms") or 0))
    out = {}
    for name, rs in sorted(by.items()):
        rs.sort()
        n, ok = len(rs), sum(1 for x in rs if x[1])
        longest, start, last = 0.0, None, None
        for t, good, _ in rs:
            if not good:
                start = t if start is None else start
                last = t
            elif start is not None:
                longest = max(longest, last - start + interval)
                start = None
        if start is not None:
            longest = max(longest, last - start + interval)
        lat = sorted(x[2] for x in rs if x[1])
        p95 = lat[min(len(lat) - 1, int(round(0.95 * (len(lat) - 1))))] if lat else 0
        out[name] = {"n": n, "echecs": n - ok, "dispo_pct": round(100.0 * ok / n, 3) if n else None, "serie_max_s": round(longest, 1),
                     "p95_ms": p95, "max_ms": max(lat) if lat else 0}
    return out, last_ts


def collect_probes(errors):
    path = find_probe_file()
    if not path:
        errors.append("sondes HA-01: fichier introuvable")
        return {}
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        fh.seek(max(0, size - 6 * 1024 * 1024))
        raw = fh.read().decode("utf-8", "replace")
    lines = raw.splitlines()[1:]
    now_ts = time.time()
    probes, last_ts = analyse_probes(lines, now_ts, WINDOW_S)
    return {"fichier": os.path.relpath(path, REPO), "fenetre_s": WINDOW_S, "harnais_age_s": int(now_ts - last_ts) if last_ts else None, "sondes": probes}


def collect_pods(errors, prev):
    rc, out, err = kube("get", "pods", "-A", "-o", "json", timeout=90)
    if rc != 0:
        errors.append("pods: " + err.strip()[:120])
        return {}
    restarts, anormaux = {}, []
    for it in json.loads(out).get("items", []):
        key = it["metadata"]["namespace"] + "/" + it["metadata"]["name"]
        phase = it["status"].get("phase")
        cs = it["status"].get("containerStatuses") or []
        restarts[key] = sum(int(c.get("restartCount", 0)) for c in cs)
        if phase in ("Succeeded",):
            continue
        ready = all(c.get("ready") for c in cs) if cs else False
        if phase != "Running" or not ready:
            anormaux.append(f"{key}:{phase}:{'prêt' if ready else 'non prêt'}")
    old = ((prev or {}).get("pods") or {}).get("restarts_map") or {}
    delta = [{"pod": k, "delta": v - old[k]} for k, v in restarts.items() if k in old and v > old[k]]
    return {"total": len(restarts), "anormaux": anormaux[:15], "redemarrages_total": sum(restarts.values()), "restarts_map": restarts,
            "redemarrages_delta": delta[:20]}


def collect_events(errors):
    rc, out, err = kube("get", "events", "-A", "-o", "json", "--field-selector", "type=Warning", timeout=60)
    if rc != 0:
        errors.append("events: " + err.strip()[:120])
        return {}
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=WINDOW_S)
    top = {}
    n = 0
    for it in json.loads(out).get("items", []):
        ts = it.get("lastTimestamp") or it.get("eventTime") or (it.get("metadata") or {}).get("creationTimestamp")
        if not ts:
            continue
        try:
            t = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            continue
        if t >= cutoff:
            n += 1
            k = "%s/%s" % (it.get("reason"), (it.get("involvedObject") or {}).get("kind"))
            top[k] = top.get(k, 0) + 1
    return {"fenetre_s": WINDOW_S, "n": n, "top": sorted(top.items(), key=lambda kv: -kv[1])[:6]}


def collect_incidents(errors, since):
    rc, out, err = kube("get", "pods", "-n", NS, "-l", "cnpg.io/instanceRole=primary", "-o", "jsonpath={.items[0].metadata.name}")
    primary = out.strip()
    if rc != 0 or not primary:
        errors.append("incidents: primaire PostgreSQL introuvable")
        return {}
    sql = (f"select 'inc_statut', status, count(*) from incident where created_at >= '{since}' group by status;\n"
           f"select 'inc_type', incident_type, count(*) from incident where created_at >= '{since}' group by incident_type;\n"
           f"select 'nouveau', id||':'||incident_type||':'||namespace||'/'||app||':'||status from incident where created_at >= '{since}' order by id limit 30;\n"
           f"select 'echec', count(*) from incident where created_at >= '{since}' and status='RESOLUTION_FAILED';\n"
           f"select 'vm_scope', count(*) from incident where created_at >= '{since}' and {SCOPE_VM_SQL};\n"
           f"select 'action', type||':'||status, count(*) from incident_action where created_at >= '{since}' group by type, status;\n"
           f"select 'restart_vm', count(*) from incident_action where created_at >= '{since}' and type='RESTART_VM';\n"
           f"select 'doublon', incident_id||':'||type, count(*) from incident_action where created_at >= '{since}' group by incident_id, type having count(*) > 1;\n"
           "select 'ouvert', id||':'||incident_type||':'||namespace||'/'||app||':'||status from incident "
           "where status in ('NEW','OPEN','IN_PROGRESS','WAITING_FOR_APPROVAL','RESOLUTION_FAILED') order by id desc limit 20;\n")
    rc, out, err = kube("exec", "-i", "-n", NS, primary, "-c", "postgres", "--", "psql", "-U", "postgres", "-d", "pfe_db", "-At", "-F", "|", "-f", "-",
                        timeout=60, stdin=sql)
    if rc != 0:
        errors.append("incidents psql: " + err.strip()[:120])
    res = {"par_statut": {}, "par_type": {}, "nouveaux": [], "resolution_failed": 0, "perimetre_vm": 0, "actions": {}, "restart_vm": 0,
           "doublons": [], "ouverts": []}
    for line in out.splitlines():
        p = line.split("|")
        t = p[0]
        if t == "inc_statut":
            res["par_statut"][p[1]] = int(p[2])
        elif t == "inc_type":
            res["par_type"][p[1]] = int(p[2])
        elif t == "nouveau":
            res["nouveaux"].append(p[1])
        elif t == "echec":
            res["resolution_failed"] = int(p[1])
        elif t == "vm_scope":
            res["perimetre_vm"] = int(p[1])
        elif t == "action":
            res["actions"][p[1]] = int(p[2])
        elif t == "restart_vm":
            res["restart_vm"] = int(p[1])
        elif t == "doublon":
            res["doublons"].append(f"{p[1]} x{p[2]}")
        elif t == "ouvert":
            res["ouverts"].append(p[1])
    return res


def collect_dns(errors):
    res = {}
    for name in DNS_NAMES:
        t0 = time.time()
        try:
            res[name] = {"ip": socket.gethostbyname(name), "ms": int((time.time() - t0) * 1000)}
        except OSError as ex:
            res[name] = {"ip": None, "erreur": str(ex)[:80], "ms": int((time.time() - t0) * 1000)}
    return res


# ───────────────────────── verdict ─────────────────────────

_VM_TS = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d+))?Z")


def _vm_ts(line):
    m = _VM_TS.match(line)
    if not m:
        return None
    base = dt.datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=dt.timezone.utc).timestamp()
    return base + (float("0." + m.group(2)) if m.group(2) else 0.0)


def _read_vm_session(path):
    """Début, fin et nature de la fin d'une session VMware (journal vmware*.log) : lecture seule des 8 premiers Ko et des 64 derniers Ko.
    vix = la session s'est terminée par une demande VIX (ligne VMAutomationPowerOff puis VMX exit) : outil en ligne de commande vmrun ou interface VMware."""
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        head = fh.read(8192).decode("utf-8", "replace")
        fh.seek(max(0, size - 65536))
        tail = fh.read().decode("utf-8", "replace")
    start = next((t for t in map(_vm_ts, head.splitlines()) if t is not None), None)
    if start is None:
        return None
    tl = tail.splitlines()
    end = next((t for t in map(_vm_ts, reversed(tl)) if t is not None), None)
    vix = any("VMAutomationPowerOff" in x for x in tl) and any(("execState/val to poweredOff" in x) or ("VMX exit" in x) for x in tl)
    return {"start": start, "end": end, "vix": vix}


def collect_arrets_vix(errors, since):
    """Arrêts de VM demandés par VIX depuis le début de la fenêtre (une telle demande CONTAMINE une observation passive).
    Source : fin des journaux vmware*.log des 6 VM, en lecture seule ; aucune commande lancée."""
    t0 = dt.datetime.strptime(since, "%Y-%m-%d %H:%M:%S").astimezone().timestamp()
    stamp = lambda t: dt.datetime.fromtimestamp(t).astimezone().isoformat(timespec="seconds")  # noqa: E731
    events = []
    for vm in VM_NAMES:
        sessions = []
        for name in ("vmware.log", "vmware-0.log", "vmware-1.log", "vmware-2.log"):
            p = os.path.join(VM_DIR, vm, name)
            if os.path.isfile(p):
                s = _read_vm_session(p)
                if s:
                    sessions.append(s)
        sessions.sort(key=lambda s: s["start"])
        for cur, nxt in zip(sessions, sessions[1:] + [None]):
            if cur["vix"] and cur["end"] is not None and cur["end"] >= t0:
                events.append({"vm": vm, "arret": stamp(cur["end"]), "redemarrage": stamp(nxt["start"]) if nxt else None})
    events.sort(key=lambda e: e["arret"])
    return {"depuis": since, "n": len(events), "evenements": events}


def collect_hote(errors):
    """Mémoire physique de l'hôte (GlobalMemoryStatusEx : « disponible » = libre + cache réutilisable). Lecture seule, aucun sous-processus."""
    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong), ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]
    st = MEMORYSTATUSEX()
    st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
        errors.append("hote: GlobalMemoryStatusEx a échoué")
        return {}
    return {"ram_disponible_mo": int(st.ullAvailPhys // 1048576), "ram_total_mo": int(st.ullTotalPhys // 1048576), "charge_pct": int(st.dwMemoryLoad)}


def verdict(rec):
    fails, warns = [], []
    api = rec.get("api") or {}
    if api and not api.get("ok"):
        fails.append("API Kubernetes non prête : " + ",".join(api.get("echecs") or ["indisponible"]))
    for n, v in (rec.get("noeuds") or {}).items():
        if not v.get("ready"):
            fails.append(f"nœud {n} NotReady")
        if v.get("pressions"):
            warns.append(f"nœud {n} sous pression : {','.join(v['pressions'])}")
        if v.get("cordon"):
            warns.append(f"nœud {n} en cordon")
    et = rec.get("etcd") or {}
    if et:
        bad = [m for m, ok in (et.get("membres") or {}).items() if not ok]
        if bad:
            fails.append("etcd : membre(s) non sain(s) : " + ",".join(bad))
        if not et.get("leader"):
            fails.append("etcd : aucun leader identifié")
    cm = rec.get("camunda") or {}
    if cm:
        if cm.get("topologie_http") != 200 or not cm.get("partitions") or cm.get("partitions_saines") != cm.get("partitions"):
            fails.append("Camunda : partition(s) non saine(s) ou topologie indisponible")
        if cm.get("sante") != "UP":
            fails.append(f"Camunda : santé {cm.get('sante')}")
    if (rec.get("bridge") or {}).get("sante_http") not in (None, 200):
        fails.append("Bridge /sante : HTTP %s" % rec["bridge"]["sante_http"])
    pg = rec.get("cnpg") or {}
    if pg:
        if pg.get("phase") != "Cluster in healthy state" or pg.get("prets") != pg.get("instances"):
            fails.append(f"CNPG : phase « {pg.get('phase')} », {pg.get('prets')}/{pg.get('instances')} prêtes")
        rep = pg.get("replication") or []
        if not rep:
            fails.append("CNPG : aucune réplique connectée")
        for r in rep:
            if r["etat"] != "streaming":
                fails.append(f"CNPG : réplique {r['app']} en état {r['etat']}")
            if r["retard_octets"] > LAG_MAX_BYTES or r["retard_octets"] < 0:
                fails.append(f"CNPG : retard de {r['app']} = {r['retard_octets']} o")
        if (pg.get("pg_wal") or {}).get("octets", 0) > PG_WAL_WARN_BYTES:
            warns.append("CNPG : pg_wal du primaire = %d Mo (> %d Mo : archivage bloqué ou slot qui retient trop de WAL ?)" % (
                pg["pg_wal"]["octets"] // 1048576, PG_WAL_WARN_BYTES // 1048576))
        if (pg.get("archiveur") or {}).get("echecs", 0) > 0:
            warns.append("CNPG : %d échec(s) d'archivage WAL (pg_stat_archiver)" % pg["archiveur"]["echecs"])
    pol = rec.get("politiques") or {}
    if pol and not pol.get("coherent"):
        fails.append("politiques NON cohérentes : " + "; ".join(pol.get("ecarts") or ["voir policy_reconcile.py"]))
    for lb, v in (rec.get("haproxy") or {}).items():
        if v.get("http") != 200:
            fails.append(f"HAProxy .{lb} : page de stats HTTP {v.get('http')}")
            continue
        for px, d in (v.get("proxies") or {}).items():
            if d["down"]:
                fails.append(f"HAProxy .{lb}/{px} : serveur(s) non UP : " + ",".join(d["down"][:4]))
    pr = rec.get("sondes_ha01") or {}
    if pr:
        age = pr.get("harnais_age_s")
        if age is None or age > 120:
            warns.append(f"harnais de sondes HA-01 muet depuis {age} s")
        for n, s in (pr.get("sondes") or {}).items():
            if s["serie_max_s"] > SERIE_MAX_S:
                fails.append(f"sonde {n} : série d'échecs de {s['serie_max_s']} s")
            elif s["echecs"]:
                warns.append(f"sonde {n} : {s['echecs']} échec(s) sur {s['n']} (série max {s['serie_max_s']} s)")
    po = rec.get("pods") or {}
    if po.get("anormaux"):
        warns.append("pods anormaux : " + ", ".join(po["anormaux"][:5]))
    for d in po.get("redemarrages_delta") or []:
        warns.append(f"redémarrage inattendu : {d['pod']} (+{d['delta']})")
    ev = rec.get("evenements_warning") or {}
    if ev.get("n", 0) > EVENTS_WARN:
        warns.append(f"{ev['n']} évènements Warning dans les {ev['fenetre_s']} dernières secondes")
    inc = rec.get("incidents") or {}
    if inc.get("doublons"):
        fails.append("doublon(s) d'action : " + ", ".join(inc["doublons"]))
    if inc.get("restart_vm"):
        n_vix = (rec.get("arrets_vix") or {}).get("n", 0)
        if inc["restart_vm"] > n_vix:
            fails.append(f"{inc['restart_vm']} action(s) RESTART_VM depuis le début de la fenêtre pour {n_vix} arrêt(s) VIX constaté(s) (aucun arrêt provoqué ne l'explique : analyse requise)")
        else:
            warns.append(f"{inc['restart_vm']} action(s) RESTART_VM depuis le début de la fenêtre, chacune couverte par un arrêt VIX constaté (voir CONTAMINATION)")
    if inc.get("nouveaux"):
        warns.append("incident(s) créé(s) depuis le début de la fenêtre : " + ", ".join(inc["nouveaux"][:5]))
    if inc.get("resolution_failed"):
        warns.append(f"{inc['resolution_failed']} incident(s) RESOLUTION_FAILED depuis le début")
    for n, d in (rec.get("dns") or {}).items():
        if not d.get("ip"):
            warns.append(f"DNS : {n} ne se résout pas ({d.get('erreur')})")
    av = rec.get("arrets_vix") or {}
    if av.get("n"):
        warns.append("CONTAMINATION : %d arrêt(s) de VM par demande VIX depuis le début de la fenêtre (%s) : fenêtre non valable comme preuve de stabilité" % (
            av["n"], ", ".join("%s à %s" % (e["vm"], e["arret"][5:19]) for e in av["evenements"][:5])))
    ram = (rec.get("hote") or {}).get("ram_disponible_mo")
    if ram is not None and ram < RAM_WARN_MB:
        warns.append("RAM disponible de l'hôte : %d Mo (< %d Mo : risque de pagination des VM)" % (ram, RAM_WARN_MB))
    for al in rec.get("alertes", []):
        warns.append(al)
    for e in rec.get("erreurs", []):
        warns.append("relevé incomplet : " + e)
    return ("FAIL" if fails else ("WARN" if warns else "OK")), fails, warns


# ───────────────────────── relevé, journal, rapport ─────────────────────────

def snapshot(prev, since):
    errors = []
    rec = {"ts": now_iso(), "fenetre_depuis": since}
    rec["api"] = safe(errors, "api", collect_api, errors)
    rec["noeuds"] = safe(errors, "noeuds", collect_nodes, errors)
    rec["etcd"] = safe(errors, "etcd", collect_etcd, errors)
    rec["camunda"] = safe(errors, "camunda", collect_camunda, errors)
    rec["bridge"] = safe(errors, "bridge", collect_bridge, errors)
    rec["cnpg"] = safe(errors, "cnpg", collect_cnpg, errors)
    rec["politiques"] = safe(errors, "politiques", collect_policies, errors)
    rec["haproxy"] = safe(errors, "haproxy", collect_haproxy, errors)
    rec["sondes_ha01"] = safe(errors, "sondes", collect_probes, errors)
    rec["pods"] = safe(errors, "pods", collect_pods, errors, prev)
    rec["evenements_warning"] = safe(errors, "evenements", collect_events, errors)
    rec["incidents"] = safe(errors, "incidents", collect_incidents, errors, since)
    rec["dns"] = safe(errors, "dns", collect_dns, errors)
    rec["hote"] = safe(errors, "hote", collect_hote, errors)
    rec["arrets_vix"] = safe(errors, "arrets_vix", collect_arrets_vix, errors, since)
    rec["erreurs"] = errors
    et, prev_et = rec.get("etcd") or {}, (prev or {}).get("etcd") or {}
    if et.get("changements_leader_cumules") is not None and prev_et.get("changements_leader_cumules") is not None \
            and et["changements_leader_cumules"] > prev_et["changements_leader_cumules"]:
        rec.setdefault("alertes", []).append("etcd : %d changement(s) de leader depuis le relevé précédent" % (et["changements_leader_cumules"] - prev_et["changements_leader_cumules"]))
    rec["verdict"], rec["fails"], rec["warns"] = verdict(rec)
    return rec


def load_all(path=None):
    path = path or OUT_FILE
    if not os.path.isfile(path):
        return []
    out = []
    with open(path, encoding="utf-8") as fh:
        for ln in fh:
            if ln.strip():
                try:
                    out.append(json.loads(ln))
                except ValueError:
                    pass
    return out


def append_record(rec, path=None):
    path = path or OUT_FILE
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def annotate(text, path=None):
    path = path or ANNOT_FILE
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": now_iso(), "texte": text}, ensure_ascii=False) + "\n")


def fmt_h(seconds):
    seconds = int(max(0, seconds))
    return "%d h %02d min" % (seconds // 3600, (seconds % 3600) // 60)


def build_report(recs, since, until, now=None, armed=None, annotations=None):
    """Rapport Markdown. « TERMINÉE » seulement si l'heure de fin est atteinte ET qu'un relevé existe après elle."""
    now = now or dt.datetime.now().astimezone()
    s, u = dt.datetime.strptime(since, "%Y-%m-%d %H:%M:%S").astimezone(), dt.datetime.strptime(until, "%Y-%m-%d %H:%M:%S").astimezone()
    recs = [r for r in recs if "ts" in r]
    ok_recs = [r for r in recs if s <= parse_iso(r["ts"]) <= u + dt.timedelta(minutes=EXPECTED_EVERY_S // 60 + 5)]
    elapsed = (min(now, u) - s).total_seconds()
    expected = int(24 * 3600 / EXPECTED_EVERY_S) if u - s >= dt.timedelta(hours=24) else int((u - s).total_seconds() / EXPECTED_EVERY_S)
    finished = now >= u and any(parse_iso(r["ts"]) >= u - dt.timedelta(minutes=EXPECTED_EVERY_S // 60 + 2) for r in ok_recs)
    L = []
    L.append("# Observation passive de stabilité — rapport horodaté %s" % now.isoformat(timespec="seconds"))
    L.append("")
    if finished:
        L.append("**Statut : TERMINÉE** — fenêtre %s → %s (%s)." % (since, until, fmt_h((u - s).total_seconds())))
    else:
        L.append("**Statut : EN COURS** — %s écoulées sur %s (fenêtre %s → %s). **Les 24 heures ne sont PAS terminées : aucun verdict final.**" % (
            fmt_h(elapsed), fmt_h((u - s).total_seconds()), since, until))
    counts = {k: sum(1 for r in ok_recs if r.get("verdict") == k) for k in ("OK", "WARN", "FAIL")}
    L.append("")
    L.append("Relevés de l'outil de stabilité : **%d** (attendus à ce stade ≈ %d) — OK %d / WARN %d / FAIL %d." % (
        len(ok_recs), min(expected, int(elapsed // EXPECTED_EVERY_S) + 1), counts["OK"], counts["WARN"], counts["FAIL"]))
    # trous
    gaps = []
    ts = [parse_iso(r["ts"]) for r in ok_recs]
    for a, b in zip(ts, ts[1:]):
        if (b - a).total_seconds() > EXPECTED_EVERY_S * 1.5:
            gaps.append("%s → %s (%s)" % (a.strftime("%d %H:%M"), b.strftime("%d %H:%M"), fmt_h((b - a).total_seconds())))
    L.append("Trous de plus de %d min entre deux relevés : %s." % (int(EXPECTED_EVERY_S * 1.5 // 60), "; ".join(gaps) if gaps else "aucun"))
    # anomalies dédupliquées
    seen = {}
    for r in ok_recs:
        for kind, items in (("FAIL", r.get("fails", [])), ("WARN", r.get("warns", []))):
            for it in items:
                e = seen.setdefault((kind, it), {"first": r["ts"], "last": r["ts"], "n": 0})
                e["last"], e["n"] = r["ts"], e["n"] + 1
    L.append("")
    L.append("## Anomalies (dédupliquées)")
    if not seen:
        L.append("Aucune.")
    for (kind, it), e in sorted(seen.items(), key=lambda kv: (kv[0][0] != "FAIL", kv[1]["first"])):
        L.append("- **%s** — %s — première %s, dernière %s, %d relevé(s)" % (kind, it, e["first"][5:16], e["last"][5:16], e["n"]))
    # synthèses
    def col(path):
        out = []
        for r in ok_recs:
            cur = r
            for k in path:
                cur = (cur or {}).get(k) if isinstance(cur, dict) else None
            if cur is not None:
                out.append(cur)
        return out
    L.append("")
    L.append("## Synthèse par domaine")
    lat = sorted(col(["api", "latence_ms"]))
    if lat:
        L.append("- API Kubernetes /readyz : %d/%d relevés prêts ; latence médiane %d ms, max %d ms." % (
            sum(1 for r in ok_recs if (r.get("api") or {}).get("ok")), len(col(["api"])), lat[len(lat) // 2], lat[-1]))
    ch = col(["etcd", "changements_leader_cumules"])
    if ch:
        L.append("- etcd : membres sains à chaque relevé = %s ; changements de leader pendant la fenêtre : %d (compteur %s → %s)." % (
            "oui" if all(all((r.get("etcd") or {}).get("membres", {}).values()) for r in ok_recs if r.get("etcd")) else "NON", ch[-1] - ch[0], ch[0], ch[-1]))
    lag = [x["retard_octets"] for r in ok_recs for x in ((r.get("cnpg") or {}).get("replication") or [])]
    if lag:
        L.append("- CNPG : retard de réplication max %d o sur %d mesures ; échecs d'archivage WAL cumulés (dernier relevé) : %s." % (
            max(lag), len(lag), ((ok_recs[-1].get("cnpg") or {}).get("archiveur") or {}).get("echecs", "?")))
    probes = {}
    for r in ok_recs:
        for n, sd in ((r.get("sondes_ha01") or {}).get("sondes") or {}).items():
            a = probes.setdefault(n, {"n": 0, "echecs": 0, "serie": 0.0, "max_ms": 0})
            a["n"] += sd["n"]
            a["echecs"] += sd["echecs"]
            a["serie"] = max(a["serie"], sd["serie_max_s"])
            a["max_ms"] = max(a["max_ms"], sd["max_ms"])
    if probes:
        L.append("- Sondes HA-01 (chemins HAProxy/Ingress/API, fenêtres de relevé cumulées, recouvrement possible) :")
        for n, a in sorted(probes.items()):
            L.append("  - `%s` : %d échec(s)/%d, série max %.1f s, latence max %d ms" % (n, a["echecs"], a["n"], a["serie"], a["max_ms"]))
    red = {}
    for r in ok_recs:
        for d in (r.get("pods") or {}).get("redemarrages_delta") or []:
            red[d["pod"]] = red.get(d["pod"], 0) + d["delta"]
    L.append("- Redémarrages de pods observés entre relevés : %s." % (", ".join("%s +%d" % kv for kv in red.items()) if red else "aucun"))
    inc = (ok_recs[-1].get("incidents") if ok_recs else None) or {}
    if inc:
        L.append("- Incidents depuis le début : par statut %s ; par type %s ; actions %s ; RESTART_VM %s ; doublons %s." % (
            inc.get("par_statut") or "aucun", inc.get("par_type") or "aucun", inc.get("actions") or "aucune", inc.get("restart_vm"), inc.get("doublons") or "aucun"))
    pol = col(["politiques", "coherent"])
    if pol:
        L.append("- Politiques : COHÉRENT à %d/%d relevés." % (sum(1 for x in pol if x), len(pol)))
    ram = sorted(col(["hote", "ram_disponible_mo"]))
    if ram:
        L.append("- RAM disponible de l'hôte : min %d Mo, médiane %d Mo, max %d Mo sur %d relevés." % (ram[0], ram[len(ram) // 2], ram[-1], len(ram)))
    vix = {}
    for r in ok_recs:
        for e in (r.get("arrets_vix") or {}).get("evenements") or []:
            vix[(e["vm"], e["arret"])] = e
    L.append("- Arrêts de VM par demande VIX pendant la fenêtre : %s." % (
        "; ".join("%s à %s" % (k[0], k[1][5:19]) for k in sorted(vix, key=lambda k: k[1])) if vix else "aucun"))
    if armed:
        av = {k: sum(1 for r in armed if r.get("verdict") == k) for k in ("OK", "WARN", "FAIL")}
        last = armed[-1]
        L.append("")
        L.append("## Outil « armé » (shadow_observe.py --armed, fenêtre depuis %s)" % ((last.get("observation") or {}).get("depuis")))
        L.append("- %d relevé(s) : OK %d / WARN %d / FAIL %d ; dernier relevé %s ; actions RESTART_VM depuis T0 : %s ; `vmrun start` depuis T0 : %s ; lignes ERROR (cumul max par pod) : %s." % (
            len(armed), av["OK"], av["WARN"], av["FAIL"], last.get("ts"), (last.get("db") or {}).get("restart_vm_depuis_ombre"),
            (last.get("hote") or {}).get("vmrun_start_depuis_fenetre"), max([p.get("log_error", 0) for r in armed for p in r.get("pods", [])] or [0])))
    if annotations:
        L.append("")
        L.append("## Interventions consignées pendant la fenêtre")
        for a in annotations:
            L.append("- %s — %s" % (a["ts"][5:16], a["texte"]))
    L.append("")
    if finished:
        worst = "FAIL" if counts["FAIL"] else ("WARN" if counts["WARN"] else "OK")
        L.append("## Verdict final : %s (voir les anomalies ci-dessus ; tout FAIL est à analyser)" % worst)
        if vix:
            L.append("**Fenêtre CONTAMINÉE** par %d arrêt(s) de VM demandé(s) par VIX : non valable comme preuve de stabilité ; à refaire sans intervention." % len(vix))
    else:
        L.append("## Verdict final : NON RENDU — observation en cours")
    return "\n".join(L) + "\n"


def write_report(text, path=None):
    path = path or REPORT_FILE
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def show(rec):
    print(f"\n=== Relevé {rec['ts']}  →  {rec['verdict']}")
    print("  api      :", json.dumps(rec.get("api")))
    print("  nœuds    :", ", ".join(f"{n}={'Ready' if v.get('ready') else 'NotReady'}" for n, v in (rec.get("noeuds") or {}).items()))
    print("  etcd     :", json.dumps(rec.get("etcd")))
    print("  camunda  :", json.dumps(rec.get("camunda")), "| bridge :", json.dumps(rec.get("bridge")))
    pg = rec.get("cnpg") or {}
    print(f"  cnpg     : {pg.get('phase')} {pg.get('prets')}/{pg.get('instances')} primaire={pg.get('primaire')} réplication={pg.get('replication')} archiveur={pg.get('archiveur')}")
    print("  politiques:", json.dumps(rec.get("politiques")))
    for f in rec.get("fails", []):
        print("  ✗ FAIL :", f)
    for w in rec.get("warns", []):
        print("  ! WARN :", w)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", metavar="AAAA-MM-JJ HH:MM:SS", help="début de la fenêtre d'observation (heure locale)")
    ap.add_argument("--until", metavar="AAAA-MM-JJ HH:MM:SS", help="fin de la fenêtre (heure locale) ; défaut : since + 24 h")
    ap.add_argument("--loop", type=int, metavar="SECONDES", help="boucle (sinon un seul relevé)")
    ap.add_argument("--report", action="store_true", help="écrit et affiche le rapport sans nouveau relevé")
    ap.add_argument("--annotate", metavar="TEXTE", help="consigne une intervention dans la chronologie")
    a = ap.parse_args(argv)
    if a.annotate:
        annotate(a.annotate)
        print("annotation consignée")
        return 0
    if not a.since:
        ap.error("--since est requis")
    since = a.since
    until = a.until or (dt.datetime.strptime(since, "%Y-%m-%d %H:%M:%S") + dt.timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    if a.report:
        text = build_report(load_all(), since, until, armed=load_all(ARMED_FILE), annotations=load_all(ANNOT_FILE))
        write_report(text)
        print(text)
        return 0
    prev = (load_all() or [None])[-1]
    while True:
        rec = snapshot(prev, since)
        append_record(rec)
        show(rec)
        prev = rec
        write_report(build_report(load_all(), since, until, armed=load_all(ARMED_FILE), annotations=load_all(ANNOT_FILE)))
        if not a.loop or dt.datetime.now() + dt.timedelta(seconds=a.loop) > dt.datetime.strptime(until, "%Y-%m-%d %H:%M:%S") + dt.timedelta(minutes=20):
            break
        time.sleep(a.loop)
    return 1 if rec["verdict"] == "FAIL" else 0


if __name__ == "__main__":
    sys.exit(main())
