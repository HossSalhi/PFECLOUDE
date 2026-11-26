# -*- coding: utf-8 -*-
"""SAUVEGARDES PostgreSQL SÉQUENTIELLES (A puis B, jamais ensemble) avec preuve de restaurabilité. DRY-RUN PAR DÉFAUT.

Pré-contrôles (toujours, lecture seule) : CronJobs présents et non suspendus, aucun Job de sauvegarde actif, PVC liés, espace libre
des deux nœuds, primaire joignable, hors de la fenêtre des sauvegardes planifiées (02:00 et 02:30 UTC).
Avec --apply, pour A puis, SEULEMENT si A est entièrement validée, pour B :
  1. création du Job depuis le CronJob (`kubectl create job --from=cronjob/…`) ;
  2. attente du statut `Complete` (échec / dépassement de 300 s ⇒ arrêt, la suivante n'est JAMAIS lancée) ;
  3. journal du Job contenant la ligne « OK » ;
  4. nouveau fichier sur le nœud : horodatage postérieur à la création du Job, taille plausible, SHA-256 ;
  5. PREUVE DE RESTAURATION (`restore_probe.py`) : le dump est rechargé dans un PostgreSQL jetable et chaque table doit retrouver
     exactement les lignes du dump — `gzip -t` seul n'est pas une preuve.

  python backup_sequence.py             dry-run : pré-contrôles et plan, rien n'est créé
  python backup_sequence.py --apply     MODIFIANTE : crée 2 Jobs (additif), un à la fois
Retour arrière : `kubectl -n pfe delete job bk-final-a-… bk-final-b-…` (les fichiers de sauvegarde sont conservés, rotation de 7).
"""
import argparse
import datetime as dt
import importlib.util
import json
import os
import re
import subprocess
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # pragma: no cover
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
NS = "pfe"
SEQUENCE = [("A", "postgres-backup"), ("B", "postgres-backup-b")]
MIN_FREE_GB = 2.0
MIN_DUMP_BYTES = 10240
JOB_TIMEOUT_S = 300
SCHEDULED_UTC = ((2, 0), (2, 30))
WINDOW_MIN = 10          # minutes de part et d'autre de chaque sauvegarde planifiée


def job_state(j):
    """Pure : 'complete' | 'failed' | 'running' d'après le JSON d'un Job."""
    for c in (j.get("status") or {}).get("conditions", []):
        if c.get("status") == "True" and c.get("type") == "Complete":
            return "complete"
        if c.get("status") == "True" and c.get("type") == "Failed":
            return "failed"
    return "running"


def size_plausible(new, prev):
    """Pure : > 10 Ko et, si une sauvegarde précédente existe, entre la moitié et le double de sa taille."""
    if new is None or new < MIN_DUMP_BYTES:
        return False
    return prev is None or prev / 2 <= new <= prev * 2


def in_scheduled_window(now_utc, window_min=WINDOW_MIN):
    """Pure : vrai si `now_utc` est à moins de `window_min` minutes d'une sauvegarde planifiée."""
    for h, m in SCHEDULED_UTC:
        t = now_utc.replace(hour=h, minute=m, second=0, microsecond=0)
        if abs((now_utc - t).total_seconds()) <= window_min * 60:
            return True
    return False


def has_ok_line(logs):
    return bool(re.search(r"\[backup(?:-b)?\] OK", logs or ""))


def run_sequence(env, apply, say=print):
    """Cœur testé : l'ordre des opérations et l'arrêt au premier échec ne dépendent QUE de l'objet `env`."""
    problems = env.precheck()
    if problems:
        for p in problems:
            say("REFUS (pré-contrôle) :", p)
        return 1
    say("pré-contrôles : tous satisfaits")
    if not apply:
        for x, cj in SEQUENCE:
            say(f"  [plan] {x}: créer un Job depuis cronjob/{cj}, attendre Complete (≤ {JOB_TIMEOUT_S} s), vérifier journal + fichier + SHA-256, "
                "prouver la restauration — puis seulement ensuite la suivante")
        say("DRY-RUN terminé : aucun Job créé. Ajouter --apply pour exécuter.")
        return 0
    results = []
    for x, cj in SEQUENCE:
        if env.active_backup_jobs():
            say(f"ARRÊT : un Job de sauvegarde est actif avant {x} — jamais deux sauvegardes en même temps.")
            return 1
        before = env.latest_dump(x)
        name = f"bk-final-{x.lower()}-{env.stamp()}"
        t0 = env.now()
        say(f"\n[{x}] création du Job {name} (depuis cronjob/{cj}) …")
        env.create_job(cj, name)
        while True:
            st = job_state(env.job_json(name))
            if st == "complete":
                break
            if st == "failed":
                say(f"ARRÊT : le Job {name} a ÉCHOUÉ — la suivante n'est PAS lancée. Journal : kubectl -n {NS} logs job/{name}")
                return 1
            if env.now() - t0 > JOB_TIMEOUT_S:
                say(f"ARRÊT : le Job {name} n'est pas Complete après {JOB_TIMEOUT_S} s — la suivante n'est PAS lancée.")
                return 1
            env.sleep(3)
        say(f"[{x}] Job {name} : Complete en {env.now() - t0:.0f} s")
        if not has_ok_line(env.job_logs(name)):
            say(f"ARRÊT : le journal du Job {name} ne contient pas la ligne « OK ».")
            return 1
        after = env.latest_dump(x)
        if not after or (before and after["name"] == before["name"]) or after["mtime"] < t0 - 5:
            say(f"ARRÊT : aucun NOUVEAU fichier de sauvegarde sur le nœud pour {x} (dernier : {after and after['name']}).")
            return 1
        if not size_plausible(after["size"], before["size"] if before else None):
            say(f"ARRÊT : taille anormale pour {after['name']} : {after['size']} octets (précédent : {before and before['size']}).")
            return 1
        say(f"[{x}] fichier {after['name']} | {after['size']} octets | sha256 {after['sha256'][:16]}… | écrit à {dt.datetime.fromtimestamp(after['mtime'], dt.timezone.utc):%H:%M:%S}Z")
        say(f"[{x}] preuve de restauration …")
        rc, report = env.probe(x, after["name"])
        say(report.strip()[-900:])
        if rc != 0:
            say(f"ARRÊT : la restauration de {after['name']} n'est PAS prouvée — la suivante n'est PAS lancée.")
            return 1
        results.append((x, name, after))
    say("\nRÉSULTAT : les deux sauvegardes sont Complete ET restaurables.")
    for x, name, f in results:
        say(f"  {x} : {f['name']} | {f['size']} octets | sha256 {f['sha256']}")
    return 0


class Real:
    """Accès réel (kubectl, ssh, restore_probe). Chaque appel de lecture est sans effet ; create_job est le seul appel modifiant."""

    def __init__(self, sandbox_pod="postgres-5"):
        spec = importlib.util.spec_from_file_location("restore_probe", os.path.join(HERE, "restore_probe.py"))
        self.rp = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.rp)
        self.sandbox_pod = sandbox_pod
        self._src = {}

    def kube(self, *a, timeout=120):
        return self.rp.kubectl(*a, timeout=timeout)

    def src(self, x):
        if x not in self._src:
            self._src[x] = self.rp.resolve(x)
        return self._src[x]

    def now(self):
        return time.time()

    def sleep(self, s):
        time.sleep(s)

    def stamp(self):
        return dt.datetime.now().strftime("%H%M%S")

    def precheck(self):
        problems = []
        if in_scheduled_window(dt.datetime.now(dt.timezone.utc)):
            problems.append("on est à moins de 10 min d'une sauvegarde planifiée (02:00 / 02:30 UTC) : risque de chevauchement")
        for x, cj in SEQUENCE:
            rc, out, _ = self.kube("get", "cronjob", cj, "-n", NS, "-o", "jsonpath={.spec.suspend}|{.spec.concurrencyPolicy}")
            if rc != 0:
                problems.append(f"CronJob {cj} introuvable")
            elif out.split("|")[0] == "true":
                problems.append(f"CronJob {cj} suspendu")
            rc, out, _ = self.kube("get", "pvc", self.rp.PVC[x], "-n", NS, "-o", "jsonpath={.status.phase}")
            if out.strip() != "Bound":
                problems.append(f"PVC {self.rp.PVC[x]} n'est pas Bound ({out.strip() or 'absent'})")
            else:
                s = self.src(x)
                rc, out, err = self.rp.ssh(s["ip"], f"df -BG --output=avail {s['path']} | tail -1")
                free = int(re.sub(r"\D", "", out) or 0)
                if free < MIN_FREE_GB:
                    problems.append(f"espace libre insuffisant sur {s['node']} : {free} Go (minimum {MIN_FREE_GB})")
        if self.active_backup_jobs():
            problems.append(f"Job(s) de sauvegarde déjà actif(s) : {self.active_backup_jobs()}")
        rc, out, _ = self.kube("get", "pods", "-n", NS, "-l", "cnpg.io/instanceRole=primary", "-o", "jsonpath={.items[0].metadata.name}")
        prim = out.strip()
        if not prim:
            problems.append("primaire PostgreSQL introuvable")
        else:
            rc, out, err = self.kube("exec", "-n", NS, prim, "-c", "postgres", "--", "psql", "-U", "postgres", "-d", "pfe_db", "-At", "-c", "select 1")
            if out.strip() != "1":
                problems.append("le primaire ne répond pas à SELECT 1")
        return problems

    def active_backup_jobs(self):
        rc, out, _ = self.kube("get", "jobs", "-n", NS, "-o", "json")
        if rc != 0:
            return ["(Jobs illisibles)"]
        return [j["metadata"]["name"] for j in json.loads(out)["items"]
                if re.match(r"(postgres-backup|bk-|n3-bk-)", j["metadata"]["name"]) and j["status"].get("active")]

    def create_job(self, cj, name):
        rc, out, err = self.kube("create", "job", f"--from=cronjob/{cj}", name, "-n", NS)
        if rc != 0:
            raise SystemExit(f"création du Job impossible : {err.strip()[:200]}")

    def job_json(self, name):
        rc, out, _ = self.kube("get", "job", name, "-n", NS, "-o", "json")
        return json.loads(out) if rc == 0 else {}

    def job_logs(self, name):
        rc, out, _ = self.kube("logs", f"job/{name}", "-n", NS)
        return out

    def latest_dump(self, x):
        s = self.src(x)
        rc, out, _ = self.rp.ssh(s["ip"], f"cd {s['path']} && F=$(ls -1t pfe_db-*.sql.gz | head -1) && stat -c '%n %s %Y' $F && sha256sum $F")
        lines = out.strip().splitlines()
        if rc != 0 or len(lines) < 2:
            return None
        n, size, mtime = lines[0].split()
        return {"name": n, "size": int(size), "mtime": int(mtime), "sha256": lines[1].split()[0]}

    def probe(self, x, name):
        cmd = [sys.executable, "-I", os.path.join(HERE, "restore_probe.py"), "--source", x, "--file", name, "--sandbox-pod", self.sandbox_pod]
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
        return p.returncode, p.stdout + p.stderr


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--sandbox-pod", default="postgres-5")
    a = ap.parse_args(argv)
    print(f"=== SAUVEGARDES SÉQUENTIELLES ({'MODIFIANTE' if a.apply else 'DRY-RUN : LECTURE SEULE'}) — cluster namespace {NS} ===")
    return run_sequence(Real(a.sandbox_pod), a.apply)


if __name__ == "__main__":
    sys.exit(main())
