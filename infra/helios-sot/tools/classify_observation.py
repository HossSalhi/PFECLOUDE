#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HELIOS — classement des FAIL d'une observation passive de stabilité (LECTURE SEULE).

Pourquoi. L'observation de 24 h du 6-7 nov. 2026 s'est terminée en FAIL brut. Cet outil sépare, avec des preuves et sans
jamais cacher le chiffre brut, ce qui vient :
  A  d'un arrêt de VM PROVOQUÉ (fin de session VMware = appel VIX « PowerOff », puis démarrage du watchdog avec
     state_before = POWERED_OFF) : fenêtre d'impact = [arrêt - 60 s ; redémarrage + 300 s] ;
  B  de la PÉRIODE DE MANIPULATIONS qui entoure ces arrêts (du premier arrêt jusqu'à 2 h après le dernier redémarrage) :
     séquelle probable, NON PROUVÉE individuellement ;
  C  d'un GEL de l'hôte (fsync WAL ou RTT etcd dégradés sur >= 3 membres au même instant, Prometheus .160) ;
  D  de rien d'identifié : NON EXPLIQUÉ.
Un classement n'est PAS une absolution : une fenêtre qui contient un arrêt provoqué n'est jamais « validée ».

Garanties : lecture de fichiers locaux ; réseau = GET Prometheus (facultatif : --no-prometheus) ; AUCUN sous-processus ;
écriture uniquement vers --out (Markdown) et --json-out.

Usage :
  python -I classify_observation.py --since "2026-11-06 21:35:51" --until "2026-11-07 21:35:51" \
      [--incidents-json F] [--no-prometheus] [--out rapport.md] [--json-out resultat.json]
"""
import argparse
import datetime as dt
import glob
import json
import os
import re
import statistics
import sys
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
STATE = r"C:\Users\houss\helios-state"
STAB_FILE = os.path.join(STATE, "observation", "stabilite-24h.jsonl")
ARMED_FILE = os.path.join(REPO, "infra", "helios-sot", "observation", "armed-observations.jsonl")
WATCHDOG_LOG = os.path.join(STATE, "helios-watchdog.log")
VM_DIR = r"C:\Users\houss\Documents\Virtual Machines"
VMS = ("k8s-master", "k8s-worker1", "k8s-worker2", "k8s-monitoring", "k8s-aiCamunda", "k8s-lb2")
PROM = "http://192.168.148.160:9090"

BEFORE_S = 60.0          # marge avant un arrêt
AFTER_S = 300.0          # démarrage de la VM + reconvergence
MANIP_MARGIN_S = 7200.0  # convention : 2 h après le dernier redémarrage provoqué (séquelles : NotReady, boot storm)
UNKNOWN_STOP_S = 150.0   # début de panne présumé quand le journal VMware est roulé et que l'incident est inconnu
INCIDENT_LEAD_S = 90.0   # un incident VM_DOWN est créé >= 90 s après l'arrêt (2 observations espacées de >= 30 s)
RECORD_WINDOW_S = 960.0  # fenêtre d'analyse des sondes d'un relevé (WINDOW_S de passive_observe.py)
INSTANT_S = 120.0        # fenêtre d'un état instantané (nœud NotReady, serveur HAProxy DOWN, CNPG...)
STALL_MARGIN_S = 30.0

CAT_A, CAT_B, CAT_C, CAT_D = "A", "B", "C", "D"
RANK = {CAT_A: 1, CAT_B: 2, CAT_C: 3, CAT_D: 4}
CAT_LABEL = {
    CAT_A: "A — arrêt de VM provoqué (fenêtre d'impact)",
    CAT_B: "B — période de manipulations (séquelle probable, non prouvée)",
    CAT_C: "C — gel d'hôte mesuré (fsync WAL ou RTT etcd, >= 3 membres)",
    CAT_D: "D — NON EXPLIQUÉ",
}

FSYNC_P99 = "histogram_quantile(0.99, sum by (instance,le)(rate(etcd_disk_wal_fsync_duration_seconds_bucket[30s])))"
RTT_P99 = "histogram_quantile(0.99, sum by (instance,le)(rate(etcd_network_peer_round_trip_time_seconds_bucket[30s])))"
LOOKBACK_S = 30.0

_VM_TS = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d+))?Z")
_TS_BYTES = re.compile(rb'"ts":\s*"([^"]+)"')
_RE_SERIE = re.compile(r"sonde (\S+) : s.rie d'.checs de ([0-9.]+) s")
_RE_COUNT = re.compile(r"^(\d+) (?:action\(s\) RESTART_VM|`vmrun start`)")


# ───────────────────────── temps ─────────────────────────

def iso_epoch(s):
    """ISO 8601 (avec décalage ou suffixe Z) -> epoch UTC."""
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    d = dt.datetime.fromisoformat(s)
    if d.tzinfo is None:
        d = d.astimezone()
    return d.timestamp()


def local_epoch(s):
    """« AAAA-MM-JJ HH:MM:SS » (heure locale de l'hôte) -> epoch UTC."""
    return dt.datetime.strptime(s, "%Y-%m-%d %H:%M:%S").astimezone().timestamp()


def vm_ts(line):
    m = _VM_TS.match(line)
    if not m:
        return None
    base = dt.datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=dt.timezone.utc).timestamp()
    return base + (float("0." + m.group(2)) if m.group(2) else 0.0)


def fmt_utc(t):
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%m-%d %H:%M:%SZ") if t else "?"


def fmt_local(t):
    return dt.datetime.fromtimestamp(t).astimezone().strftime("%m-%d %H:%M:%S") if t else "?"


# ───────────────────────── intervalles ─────────────────────────

def merge(intervals):
    out = []
    for a, b in sorted(intervals):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def overlaps(intervals, a, b, before_s=0.0, after_s=0.0):
    return any(x - after_s <= b and y + before_s >= a for x, y in intervals)


# ───────────────────────── sessions VMware (arrêts VIX) ─────────────────────────

def read_session(path, vm, head_bytes=8192, tail_bytes=65536):
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        head = fh.read(head_bytes).decode("utf-8", "replace")
        fh.seek(max(0, size - tail_bytes))
        tail = fh.read().decode("utf-8", "replace")
    start = next((t for t in map(vm_ts, head.splitlines()) if t is not None), None)
    tail_lines = tail.splitlines()
    end = next((t for t in map(vm_ts, reversed(tail_lines)) if t is not None), None)
    vix = any("VMAutomationPowerOff" in ln for ln in tail_lines)
    off = any(("execState/val to poweredOff" in ln) or ("VMX exit" in ln) for ln in tail_lines)
    kind = "VIX_POWEROFF" if (vix and off) else ("ARRET_NON_VIX" if off else "EN_COURS_OU_INTERROMPU")
    return {"vm": vm, "file": os.path.basename(path), "start": start, "end": end, "end_kind": kind}


def vm_sessions(vm_dir=VM_DIR, vms=VMS):
    out = {}
    for vm in vms:
        ss = []
        for name in ("vmware.log", "vmware-0.log", "vmware-1.log", "vmware-2.log"):
            p = os.path.join(vm_dir, vm, name)
            if os.path.isfile(p):
                s = read_session(p, vm)
                if s["start"] is not None:
                    ss.append(s)
        ss.sort(key=lambda s: s["start"])
        out[vm] = ss
    return out


def stop_events(sessions_by_vm):
    ev = []
    for vm, ss in sessions_by_vm.items():
        for cur, nxt in zip(ss, ss[1:] + [None]):
            if cur["end_kind"] in ("VIX_POWEROFF", "ARRET_NON_VIX"):
                ev.append({"vm": vm, "stop": cur["end"], "restart": nxt["start"] if nxt else None,
                           "kind": cur["end_kind"], "source": cur["file"]})
    return ev


# ───────────────────────── journal du watchdog ─────────────────────────

def watchdog_starts(path=WATCHDOG_LOG):
    out = []
    if not os.path.isfile(path):
        return out
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                o = json.loads(line)
            except ValueError:
                continue
            if o.get("decision") == "STARTED" and o.get("vm") and o.get("ts"):
                out.append({"vm": o["vm"], "ts": iso_epoch(o["ts"]), "incident": o.get("incident"),
                            "state_before": o.get("state_before"), "caller": o.get("caller"),
                            "request_id": o.get("request_id"), "http": o.get("http")})
    return out


# ───────────────────────── arrêts provoqués ─────────────────────────

def provoked_events(stops, starts, incident_created=None, match_s=90.0):
    """Associe chaque démarrage du watchdog (state_before=POWERED_OFF) à l'arrêt VIX qui l'a précédé.
    Journal VMware roulé -> début estimé (incident connu : création - 90 s ; sinon démarrage - 150 s) et marqué comme tel."""
    incident_created = incident_created or {}
    used, events, non_poweroff = set(), [], []
    for s in sorted(starts, key=lambda x: x["ts"]):
        if s.get("state_before") != "POWERED_OFF":
            non_poweroff.append(s)           # un démarrage sans preuve d'arrêt serait superflu : signalé à part
            continue
        match = None
        for i, st in enumerate(stops):
            if i in used or st["vm"] != s["vm"] or st["restart"] is None or st["kind"] != "VIX_POWEROFF":
                continue
            if abs(st["restart"] - s["ts"]) <= match_s:
                match = i
                break
        if match is not None:
            used.add(match)
            st = stops[match]
            events.append({"vm": s["vm"], "stop": st["stop"], "stop_known": True, "start": s["ts"],
                           "incident": s.get("incident"), "caller": s.get("caller"),
                           "source": "journal VMware (%s) + watchdog" % st["source"]})
        else:
            created = incident_created.get(s.get("incident"))
            stop = (created - INCIDENT_LEAD_S) if created else (s["ts"] - UNKNOWN_STOP_S)
            events.append({"vm": s["vm"], "stop": min(stop, s["ts"] - 1.0), "stop_known": False, "start": s["ts"],
                           "incident": s.get("incident"), "caller": s.get("caller"),
                           "source": "watchdog (POWERED_OFF confirmé) ; journal VMware roulé, début estimé"})
    for i, st in enumerate(stops):
        if i in used or st["kind"] != "VIX_POWEROFF":
            continue
        events.append({"vm": st["vm"], "stop": st["stop"], "stop_known": True, "start": st["restart"], "incident": None,
                       "caller": None, "source": "journal VMware (%s) ; redémarrage hors watchdog" % st["source"]})
    events.sort(key=lambda e: e["stop"])
    return events, non_poweroff


def impact_windows(events, t0, t1, before_s=BEFORE_S, after_s=AFTER_S):
    wins = []
    for e in events:
        a, b = e["stop"] - before_s, (e["start"] if e["start"] else t1) + after_s
        if b >= t0 and a <= t1:
            wins.append((a, b))
    return merge(wins)


def manipulation_window(events, t0, t1, margin_s=MANIP_MARGIN_S, before_s=BEFORE_S):
    inside = [e for e in events if (e["start"] or t1) >= t0 and e["stop"] <= t1]
    if not inside:
        return None
    return (min(e["stop"] for e in inside) - before_s, max((e["start"] or t1) for e in inside) + margin_s)


# ───────────────────────── sondes HA-01 ─────────────────────────

def _line_ts(raw):
    m = _TS_BYTES.search(raw)
    if not m:
        return None
    try:
        return dt.datetime.strptime(m.group(1).decode(), "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=dt.timezone.utc).timestamp()
    except ValueError:
        return None


def _seek_start(fh, size, target):
    lo, hi = 0, size
    while hi - lo > 4096:
        mid = (lo + hi) // 2
        fh.seek(mid)
        fh.readline()
        ts = _line_ts(fh.readline())
        if ts is None:
            hi = mid
        elif ts < target:
            lo = mid
        else:
            hi = mid
    return lo


def probe_series(path, t0, t1, interval=1.0):
    """Séries d'échecs (échecs consécutifs d'une même sonde sans succès intercalé) entre t0 et t1."""
    series, open_ = {}, {}
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        off = _seek_start(fh, size, t0 - RECORD_WINDOW_S)
        fh.seek(off)
        if off:
            fh.readline()
        for raw in fh:
            t = _line_ts(raw)
            if t is None:
                continue
            if t > t1 + 60:
                break
            try:
                r = json.loads(raw)
            except ValueError:
                continue
            p = r.get("probe", "?")
            if r.get("ok"):
                cur = open_.pop(p, None)
                if cur:
                    series.setdefault(p, []).append((cur[0], cur[1], cur[2]))
            else:
                cur = open_.get(p)
                if cur:
                    cur[1], cur[2] = t, cur[2] + 1
                else:
                    open_[p] = [t, t, 1]
    for p, cur in open_.items():
        series.setdefault(p, []).append((cur[0], cur[1], cur[2]))
    return {p: [{"start": a, "end": b, "n": n, "dur_s": round(b - a + interval, 1)} for a, b, n in v] for p, v in series.items()}


def find_probe_file():
    c = glob.glob(os.path.join(REPO, "infra", "ha01", "results", "*", "probe-baseline-p0.jsonl"))
    return max(c, key=os.path.getmtime) if c else None


# ───────────────────────── gels d'hôte (Prometheus .160, GET) ─────────────────────────

def prom_range(query, a, b, step, prom=PROM):
    url = prom + "/api/v1/query_range?" + urllib.parse.urlencode({"query": query, "start": a, "end": b, "step": step})
    with urllib.request.urlopen(url, timeout=60) as r:
        data = json.load(r)
    if data.get("status") != "success":
        raise RuntimeError(str(data)[:200])
    return data["data"]["result"]


def stall_windows(fetch, expr, t0, t1, min_members=3, threshold_s=1.0, step=5, merge_gap_s=30.0):
    """Fenêtres où >= min_members membres etcd dépassent threshold_s (p99) au même instant. None si Prometheus est injoignable."""
    t1 = min(t1, dt.datetime.now().timestamp())
    pts, a = [], t0
    try:
        while a < t1:
            b = min(a + 12 * 3600, t1)
            res = fetch("count(%s > %s)" % (expr, threshold_s), a, b, step)
            if res:
                pts += [(float(x), int(float(y))) for x, y in res[0]["values"]]
            a = b + step
    except Exception:  # noqa: BLE001  (Prometheus indisponible, réseau, JSON)
        return None
    out, cur = [], None
    for ts, n in sorted(pts):
        if n < min_members:
            continue
        if cur and ts - cur["end"] <= merge_gap_s:
            cur["end"], cur["members"] = ts, max(cur["members"], n)
        else:
            if cur:
                out.append(cur)
            cur = {"start": ts - LOOKBACK_S, "end": ts, "members": n}
    if cur:
        out.append(cur)
    return out


def host_stalls(t0, t1, fetch=prom_range):
    f = stall_windows(fetch, FSYNC_P99, t0, t1)
    r = stall_windows(fetch, RTT_P99, t0, t1)
    if f is None and r is None:
        return None, None, None
    fl, rl = f or [], r or []
    allw = merge([(w["start"], w["end"]) for w in fl + rl])
    return allw, fl, rl


# ───────────────────────── classement ─────────────────────────

def item_interval(item, ts, series):
    """(a, b, type) de l'intervalle à classer pour un item de FAIL."""
    m = _RE_SERIE.search(item)
    if m:
        name, dur = m.group(1), float(m.group(2))
        lo, hi = ts - RECORD_WINDOW_S - 5.0, ts + 5.0
        cands = [s for s in series.get(name, []) if s["end"] >= lo and s["start"] <= hi]
        if cands:
            best = min(cands, key=lambda s: abs(s["dur_s"] - dur))
            if abs(best["dur_s"] - dur) <= 6.0:
                return best["start"] - 5.0, best["end"] + 5.0, "serie"
        return ts - RECORD_WINDOW_S, ts, "serie_non_retrouvee"
    if _RE_COUNT.match(item):
        return ts - INSTANT_S, ts, "compteur"
    return ts - INSTANT_S, ts, "instant"


def categorize(a, b, ctx):
    direct = overlaps(ctx["impact"], a, b)
    manip = ctx["manip"] is not None and a <= ctx["manip"][1] and b >= ctx["manip"][0]
    stall = ctx["stalls"] is not None and overlaps(ctx["stalls"], a, b, STALL_MARGIN_S, STALL_MARGIN_S)
    if direct:
        return CAT_A, stall
    if manip:
        return CAT_B, stall
    if stall:
        return CAT_C, stall
    return CAT_D, stall


def classify_record(rec, ctx):
    ts = iso_epoch(rec["ts"])
    items = []
    for it in rec.get("fails", []):
        a, b, kind = item_interval(it, ts, ctx["series"])
        if kind == "compteur":
            m = _RE_COUNT.match(it)
            n = int(m.group(1))
            known = sum(1 for e in ctx["events"] if ctx["t0"] <= e["start"] <= ts + 5.0)
            cat, stall = (CAT_A, False) if n <= known else (CAT_D, False)
        else:
            cat, stall = categorize(a, b, ctx)
        items.append({"item": it, "cat": cat, "stall": stall, "kind": kind})
    level = max((RANK[i["cat"]] for i in items), default=0)
    return {"ts": rec["ts"], "epoch": ts, "verdict": rec.get("verdict"), "items": items, "level": level}


def summarize(classified):
    fail = [c for c in classified if c["verdict"] == "FAIL"]
    out = {
        "relevés": len(classified),
        "FAIL brut": len(fail),
        "FAIL hors A": sum(1 for c in fail if c["level"] >= 2),
        "FAIL hors A+B": sum(1 for c in fail if c["level"] >= 3),
        "FAIL hors A+B+C (non expliqués)": sum(1 for c in fail if c["level"] >= 4),
    }
    by = {k: 0 for k in (CAT_A, CAT_B, CAT_C, CAT_D)}
    for c in fail:
        for it in c["items"]:
            by[it["cat"]] += 1
    out["items par catégorie"] = by
    return out


def ram_stats(armed_records):
    vals = [r["hote"]["ram_dispo_mo"] for r in armed_records if isinstance(r.get("hote"), dict) and isinstance(r["hote"].get("ram_dispo_mo"), (int, float))]
    if not vals:
        return None
    return {"n": len(vals), "min_mo": min(vals), "mediane_mo": statistics.median(vals), "max_mo": max(vals),
            "sous_1000": sum(1 for v in vals if v < 1000), "sous_2500": sum(1 for v in vals if v < 2500)}


def load_jsonl(path, t0, t1):
    out = []
    if not os.path.isfile(path):
        return out
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                t = iso_epoch(r["ts"])
            except (ValueError, KeyError):
                continue
            if t0 <= t <= t1 + 1800:
                out.append(r)
    return out


def verdict_text(summary, n_events, stalls_known):
    if n_events > 0:
        head = ("FENÊTRE CONTAMINÉE : %d arrêt(s) de VM provoqué(s) pendant la fenêtre. NON VALIDÉE comme preuve de stabilité de 24 h ; "
                "ré-observation requise sur une fenêtre sans intervention." % n_events)
    elif summary["FAIL hors A+B+C (non expliqués)"] > 0:
        head = "NON VALIDÉ : %d relevé(s) FAIL sans cause identifiée." % summary["FAIL hors A+B+C (non expliqués)"]
    else:
        head = "FAIL brut intégralement attribué à des gels d'hôte mesurés ; à re-vérifier sur une fenêtre propre (aucun verdict VALIDÉ n'est émis par cet outil)."
    if not stalls_known:
        head += " Gels d'hôte NON ÉVALUÉS (Prometheus .160 injoignable) : aucune exemption appliquée."
    return head


def run(args):
    t0, t1 = local_epoch(args.since), local_epoch(args.until)
    incidents = {}
    if args.incidents_json and os.path.isfile(args.incidents_json):
        with open(args.incidents_json, "r", encoding="utf-8") as fh:
            incidents = {int(k): float(v) for k, v in json.load(fh).get("incidents", {}).items()}
    sessions = vm_sessions(args.vm_dir)
    stops = stop_events(sessions)
    starts = [s for s in watchdog_starts(args.watchdog_log) if t0 - 60 <= s["ts"] <= t1 + 1800]
    events, non_poweroff = provoked_events(stops, starts, incidents)
    events_in = [e for e in events if (e["start"] or t1) >= t0 and e["stop"] <= t1]
    impact = impact_windows(events_in, t0, t1)
    manip = manipulation_window(events_in, t0, t1)
    probe_path = args.probes or find_probe_file()
    series = probe_series(probe_path, t0, t1) if probe_path and os.path.isfile(probe_path) else {}
    stalls, fs, rtt = (None, None, None) if args.no_prometheus else host_stalls(t0, t1)
    ctx = {"impact": impact, "manip": manip, "stalls": stalls, "series": series, "events": events_in, "t0": t0}
    stab = load_jsonl(args.stab, t0, t1)
    armed = load_jsonl(args.armed, t0, t1)
    cs = [classify_record(r, ctx) for r in stab]
    ca = [classify_record(r, ctx) for r in armed]
    clean = [c for c in cs if manip is None or c["epoch"] < manip[0]]
    return {
        "fenetre": {"depuis": args.since, "jusqu_a": args.until},
        "evenements_provoques": events_in, "demarrages_sans_arret_prouve": non_poweroff,
        "fenetre_impact": [(fmt_utc(a), fmt_utc(b)) for a, b in impact],
        "periode_manipulations": (fmt_utc(manip[0]), fmt_utc(manip[1])) if manip else None,
        "gels_hote": None if stalls is None else {
            "fenetres": len(stalls), "fsync": len(fs or []), "rtt": len(rtt or []),
            "duree_s": round(sum(b - a for a, b in stalls)),
            "pct_fenetre": round(100.0 * sum(b - a for a, b in stalls) / max(1.0, t1 - t0), 1)},
        "stabilite": {"synthese": summarize(cs), "periode_propre": summarize(clean), "classes": cs,
                      "periode_propre_detail": [c for c in clean if c["verdict"] == "FAIL"]},
        "arme": {"synthese": summarize(ca), "classes": ca},
        "ram_hote_mo": ram_stats(armed),
        "verdict": verdict_text(summarize(cs), len(events_in), stalls is not None),
        "_stalls": stalls, "_manip": manip, "_impact": impact,
    }


def md_table(rows, head):
    out = ["| " + " | ".join(head) + " |", "|" + "|".join("---" for _ in head) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def render(res):
    L = ["# Classement de l'observation passive (généré par classify_observation.py, LECTURE SEULE)", ""]
    L.append("Fenêtre : %s → %s (heure locale)." % (res["fenetre"]["depuis"], res["fenetre"]["jusqu_a"]))
    L.append("")
    L.append("**Verdict : %s**" % res["verdict"])
    L.append("")
    L.append("## Arrêts de VM provoqués (preuves croisées)")
    rows = [(e.get("incident") or "—", e["vm"], fmt_utc(e["stop"]) + ("" if e["stop_known"] else " (estimé)"), fmt_utc(e["start"]),
             e.get("caller") or "—", e["source"]) for e in res["evenements_provoques"]]
    L.append(md_table(rows, ["Incident", "VM", "Arrêt (UTC)", "Démarrage (UTC)", "Appelant", "Source"]) if rows else "Aucun.")
    L.append("")
    sp = res["demarrages_sans_arret_prouve"]
    L.append("Démarrages du watchdog SANS arrêt prouvé (superflus) : **%d**." % len(sp))
    L.append("")
    L.append("Fenêtres d'impact fusionnées (UTC) : " + ("; ".join("%s → %s" % w for w in res["fenetre_impact"]) or "aucune") + ".")
    pm = res["periode_manipulations"]
    L.append("Période de manipulations (UTC, marge de 2 h = convention) : " + ("%s → %s" % pm if pm else "aucune") + ".")
    g = res["gels_hote"]
    L.append("Gels d'hôte mesurés (Prometheus .160) : " + (
        "%d fenêtre(s) fusionnées (fsync %d, RTT %d) couvrant %d s, soit %.1f %% de la fenêtre : un FAIL classé C avait donc environ %.0f %% de chances de coïncider par hasard." % (
            g["fenetres"], g["fsync"], g["rtt"], g["duree_s"], g["pct_fenetre"], g["pct_fenetre"]) if g else "NON ÉVALUÉS."))
    L.append("")
    for key, titre in (("stabilite", "Outil de stabilité"), ("arme", "Outil « armé »")):
        s = res[key]["synthese"]
        L.append("## %s — chiffres bruts ET restants" % titre)
        L.append(md_table([(k, v) for k, v in s.items() if k != "items par catégorie"], ["Mesure", "Valeur"]))
        L.append("")
        L.append("Items de FAIL par catégorie : " + ", ".join("%s=%d" % (k, v) for k, v in s["items par catégorie"].items()) + ".")
        L.append("")
    pp = res["stabilite"]["periode_propre"]
    L.append("## Période propre (avant la première manipulation) — outil de stabilité")
    L.append(md_table([(k, v) for k, v in pp.items() if k != "items par catégorie"], ["Mesure", "Valeur"]))
    L.append("")
    det = res["stabilite"]["periode_propre_detail"]
    if det:
        L.append("Relevés FAIL de la période propre, item par item :")
        for c in det:
            L.append("- %s (local) :" % fmt_local(c["epoch"]))
            for i in c["items"]:
                L.append("  - [%s%s] %s" % (i["cat"], ", coïncide avec un gel" if i["stall"] else "", i["item"]))
        L.append("")
    r = res["ram_hote_mo"]
    if r:
        L.append("## RAM libre de l'hôte pendant la fenêtre (relevés de l'outil armé)")
        L.append("n=%d, min %d Mo, médiane %d Mo, max %d Mo ; relevés sous 1 000 Mo : %d ; sous 2 500 Mo : %d." % (
            r["n"], r["min_mo"], r["mediane_mo"], r["max_mo"], r["sous_1000"], r["sous_2500"]))
        L.append("")
    L.append("## Détail des relevés FAIL (outil de stabilité)")
    rows = []
    for c in res["stabilite"]["classes"]:
        if c["verdict"] != "FAIL":
            continue
        cats = sorted({i["cat"] for i in c["items"]})
        stall = "oui" if any(i["stall"] for i in c["items"]) else "non"
        rows.append((fmt_local(c["epoch"]), "/".join(cats), stall, len(c["items"])))
    L.append(md_table(rows, ["Relevé (local)", "Catégories", "Coïncide avec un gel", "Items"]) if rows else "Aucun.")
    L.append("")
    L.append("Légende : " + " ; ".join(CAT_LABEL[k] for k in (CAT_A, CAT_B, CAT_C, CAT_D)) + ".")
    return "\n".join(L) + "\n"


def write_text(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--since", required=True)
    ap.add_argument("--until", required=True)
    ap.add_argument("--stab", default=STAB_FILE)
    ap.add_argument("--armed", default=ARMED_FILE)
    ap.add_argument("--watchdog-log", default=WATCHDOG_LOG)
    ap.add_argument("--vm-dir", default=VM_DIR)
    ap.add_argument("--probes")
    ap.add_argument("--incidents-json")
    ap.add_argument("--no-prometheus", action="store_true")
    ap.add_argument("--out")
    ap.add_argument("--json-out")
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    res = run(args)
    text = render(res)
    if args.out:
        write_text(args.out, text)
    if args.json_out:
        write_text(args.json_out, json.dumps({k: v for k, v in res.items() if not k.startswith("_")}, ensure_ascii=False, indent=1, default=str))
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
