#!/usr/bin/env python3
"""
Reprise .162 (4 nov. 2026) — ÉTAPE 6 du plan : SUPERVISEUR EXTERNE du préflight spécifique. Script jetable CONSERVÉ COMME PREUVE (hors ops/ ; aucun contrôle de p5 modifié).

  python supervision-p5.py                       # lance `python ops\\p5_162_preflight.py` sous supervision
  python supervision-p5.py --tag selftest --child-cmd "..."   # essai à blanc sur un enfant inoffensif (jamais p5)

Surveille TOUTE la durée de l'enfant (donc la charge 20 req/s x 30 s vers .160) toutes les 2 s :
  (1) MemAvailable de .160 (SSH)                                    -> ABANDON si < 300 Mo (ou mesure impossible 3 fois de suite)
  (2) RAM disponible de l'hôte (GlobalMemoryStatusEx, comme p5)      -> ABANDON si < 600 Mo
  (3) sondes : toute NOUVELLE ligne ok:false dans probe-baseline-p0.jsonl (Windows) et probe161-p0.jsonl (.161, lecture SSH toutes les 4 s) -> ABANDON
  (4) santé etcd : GET http://192.168.148.<ip>:2381/health sur les 5 membres -> ABANDON si l'un n'est pas "true" (confirmé par une re-lecture immédiate <= 1 s)
ABANDON = arrêt de TOUT l'arbre de processus de l'enfant (taskkill /T /F, générateur de charge compris), consignation (heure, cause, valeurs) dans le dossier de preuves,
AUCUNE relance automatique. Journal en création exclusive : supervision-<tag>-<UTC>.jsonl ; sortie de l'enfant : supervision-<tag>-<UTC>-sortie-enfant.txt.
Codes retour : le code de l'enfant s'il se termine seul ; 5 = ABANDON.
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request

OPS = r"C:\Users\houss\IdeaProjects\PFE-Orange\infra\ha01\ops"
sys.path.insert(0, OPS)
import ha01_ops as O  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
F161 = "/opt/helios-backups/ha01/probe161-p0.jsonl"


def mem160():
    rc, o = O.ssh("160", "awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo", timeout=10)
    return int(o) if rc == 0 and o.strip().isdigit() else None


def etcd_non_sains():
    bad = []
    for s in O.ETCD_ALL:
        try:
            ok = json.load(urllib.request.urlopen("http://%s%s:2381/health" % (O.NET, s), timeout=2)).get("health") == "true"
        except Exception:  # noqa: BLE001
            ok = False
        if not ok:
            bad.append(s)
    return bad


class SondeWindows:
    """Lit uniquement les lignes COMPLÈTES ajoutées à probe-baseline-p0.jsonl depuis le lancement."""

    def __init__(self):
        self.path = O.PROBE_WIN
        self.off = os.path.getsize(self.path)

    def nouvelles(self):
        with open(self.path, "rb") as fh:
            fh.seek(self.off)
            data = fh.read()
        fin = data.rfind(b"\n") + 1
        self.off += fin
        n, echecs = 0, []
        for l in data[:fin].decode("utf-8", "replace").splitlines():
            try:
                r = json.loads(l)
            except ValueError:
                continue
            if r.get("probe") == "_meta":
                continue
            n += 1
            if r.get("ok") is False:
                echecs.append({k: r.get(k) for k in ("ts", "probe", "status", "ms", "err")})
        return n, echecs


class Sonde161:
    """Relit les 90 dernières lignes de probe161-p0.jsonl (SSH) et ne traite chaque enregistrement qu'une fois (insensible aux lignes partielles)."""

    def __init__(self):
        self.vus = set()
        self.t0 = O.iso_ms(time.time())
        self.nouvelles()                                  # amorce : ce qui existe déjà au lancement n'est pas compté

    def nouvelles(self):
        rc, o = O.ssh("161", "tail -n 90 %s" % F161, timeout=15)
        n, echecs = 0, []
        for l in o.splitlines():
            try:
                r = json.loads(l)
            except ValueError:
                continue
            k = (r.get("ts"), r.get("probe"))
            if k in self.vus or r.get("probe") == "_meta":
                continue
            self.vus.add(k)
            if r.get("ts", "") >= self.t0:
                n += 1
                if r.get("ok") is False:
                    echecs.append({kk: r.get(kk) for kk in ("ts", "probe", "status", "ms", "err")})
        return n, echecs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="p5")
    ap.add_argument("--mem160-min", type=int, default=300)
    ap.add_argument("--ram-min", type=int, default=600)
    ap.add_argument("--period", type=float, default=2.0)
    ap.add_argument("--child-cmd", default=None, help="essai à blanc uniquement : commande de l'enfant (par défaut : p5_162_preflight.py)")
    a = ap.parse_args()
    cmd = a.child_cmd.split("|") if a.child_cmd else [sys.executable, os.path.join(OPS, "p5_162_preflight.py")]      # essai à blanc : arguments séparés par « | »
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    log_path = os.path.join(HERE, "supervision-%s-%s.jsonl" % (a.tag, stamp))
    out_path = os.path.join(HERE, "supervision-%s-%s-sortie-enfant.txt" % (a.tag, stamp))
    log = open(log_path, "x", encoding="utf-8")
    out = open(out_path, "x", encoding="utf-8")

    def ecrire(rec):
        log.write(json.dumps(rec, ensure_ascii=False) + "\n")
        log.flush()

    win, p161 = SondeWindows(), Sonde161()
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    ecrire({"ts": O.iso_ms(time.time()), "evenement": "DEBUT", "enfant": cmd, "seuils": {"mem160_min_mo": a.mem160_min, "ram_hote_min_mo": a.ram_min, "periode_s": a.period}})
    proc = subprocess.Popen(cmd, cwd=OPS, stdout=out, stderr=subprocess.STDOUT, env=env)
    mn = {"mem160": None, "ram_hote": None}
    tot = {"cycles": 0, "sondes_win": 0, "sondes_161": 0}
    echecs_ssh, cycle, abandon = 0, 0, None
    while proc.poll() is None:
        t_cycle = time.time()
        try:
            txt = open(out_path, encoding="utf-8", errors="replace").read()
        except OSError:
            txt = ""
        phase = "apres_charge" if "charge 20 req/s" in txt else ("charge" if "API Kubernetes AUTHENTIFIÉE" in txt else "avant_charge")
        m, ram = mem160(), O.host_avail_mb()
        n_w, f_w = win.nouvelles()
        n_1, f_1 = (0, [])
        if cycle % 2 == 0:
            n_1, f_1 = p161.nouvelles()
        bad = etcd_non_sains()
        if bad:
            time.sleep(1.0)
            bad = sorted(set(bad) & set(etcd_non_sains()))           # alerte CONFIRMÉE par une re-lecture immédiate
        rec = {"ts": O.iso_ms(t_cycle), "phase": phase, "mem160_mo": m, "ram_hote_mo": ram, "sondes_win_lues": n_w, "sondes_win_echecs": f_w,
               "sondes_161_lues": n_1, "sondes_161_echecs": f_1, "etcd_non_sains": bad}
        echecs_ssh = echecs_ssh + 1 if m is None else 0
        cause = None
        if m is not None and m < a.mem160_min:
            cause = "MemAvailable .160 = %d Mo < %d Mo" % (m, a.mem160_min)
        elif echecs_ssh >= 3:
            cause = "MemAvailable .160 illisible 3 fois de suite (SSH)"
        elif ram < a.ram_min:
            cause = "RAM disponible de l'hôte = %d Mo < %d Mo" % (ram, a.ram_min)
        elif f_w or f_1:
            cause = "sonde en échec : %s" % (f_w + f_1)[:3]
        elif bad:
            cause = "santé etcd dégradée (membres non sains confirmés) : %s" % bad
        if m is not None:
            mn["mem160"] = m if mn["mem160"] is None else min(mn["mem160"], m)
        mn["ram_hote"] = ram if mn["ram_hote"] is None else min(mn["ram_hote"], ram)
        tot["cycles"] += 1
        tot["sondes_win"] += n_w
        tot["sondes_161"] += n_1
        if cause:
            rec["ABANDON"] = cause
            ecrire(rec)
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
            abandon = {"ts": O.iso_ms(time.time()), "cause": cause, "phase": phase, "valeurs": rec}
            break
        ecrire(rec)
        cycle += 1
        time.sleep(max(0.0, a.period - (time.time() - t_cycle)))
    rc = proc.wait(timeout=30)
    resume = {"ts": O.iso_ms(time.time()), "evenement": "ABANDON" if abandon else "FIN", "code_enfant": rc, "min_mem160_mo": mn["mem160"], "min_ram_hote_mo": mn["ram_hote"],
              **tot, "journal": os.path.basename(log_path), "sortie_enfant": os.path.basename(out_path)}
    if abandon:
        resume["abandon"] = abandon
    ecrire(resume)
    log.close()
    out.close()
    print(json.dumps(resume, ensure_ascii=False, indent=1))
    if abandon:
        with open(os.path.join(HERE, "ABANDON-%s-%s.txt" % (a.tag, stamp)), "x", encoding="utf-8") as fh:
            fh.write("ABANDON du superviseur (%s) : %s\nphase : %s\nvaleurs : %s\nAucune relance automatique.\n" % (abandon["ts"], abandon["cause"], abandon["phase"], json.dumps(abandon["valeurs"], ensure_ascii=False)))
        return 5
    return rc


if __name__ == "__main__":
    sys.exit(main())
