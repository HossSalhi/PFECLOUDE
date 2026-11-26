# -*- coding: utf-8 -*-
"""PROTECTION des sauvegardes PostgreSQL contre les suppressions automatiques non sûres. DRY-RUN PAR DÉFAUT.

Défaut constaté le 6 nov. 2026 : les deux CronJobs de sauvegarde (`postgres-backup` → PVC sur .158, `postgres-backup-b` → PVC sur .155) exécutaient, après
chaque dump, `ls -1t /backups/pfe_db-*.sql.gz | tail -n +8 | xargs -r rm -f` : chaque nouvelle sauvegarde SUPPRIMAIT le plus ancien dump dès qu'il y en avait 7,
sans condition d'espace, sans vérifier que les dumps conservés sont sains ni que la restauration de celui qu'on supprime n'a pas été validée. De plus le CronJob A
écrivait directement sous le nom définitif (un dump tronqué pouvait masquer un bon dump et en pousser un autre hors de la fenêtre de 7).

Nouvelle politique (appliquée aux deux CronJobs, même bloc de rotation) :
  1. Écriture en `.partial`, contrôle `gzip -t` + taille minimale, puis `mv` ; empreinte SHA-256 conservée à côté (`<dump>.sha256`) ; jamais de rotation si le dump du jour échoue.
  2. AUCUNE suppression tant que l'occupation du système de fichiers du volume est < 90 % (1 dump/jour ≈ 0,3 Mo : le stockage n'est pas un enjeu) ; alerte dans le journal à partir de 80 %.
  3. Au-delà de 90 %, on ne peut supprimer que les dumps situés AU-DELÀ des 30 plus récents, jamais ceux listés dans `/backups/.protected` (restaurations validées),
     et seulement si les 5 dumps les plus récents viennent de passer `gzip -t`. Sinon : rotation annulée et journalisée.
  4. Les fichiers `.sha256`, `.partial` et `.protected` ne sont jamais supprimés par la rotation.

  python protect_backups.py                     dry-run : diff exact des deux scripts, aucun accès en écriture
  python protect_backups.py --apply             MODIFIANTE : exporte les CronJobs (helios-state\\snapshots), remplace le script (args[0]) des deux CronJobs, relit et compare
  python protect_backups.py --rollback FICHIER  remet le script d'origine depuis l'export YAML/JSON d'un --apply précédent
  python protect_backups.py --verify            crée UN Job de test par CronJob, SÉQUENTIELLEMENT (ajoute 1 dump par volume, n'en supprime aucun)
Aucune suppression de dump, de PVC ou de PV n'est exécutée par cet outil.
"""
import argparse
import datetime as dt
import difflib
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

NS = "pfe"
SNAP_DIR = r"C:\Users\houss\helios-state\snapshots"
CRONJOBS = {"postgres-backup": "a", "postgres-backup-b": "b"}

ROTATION = r'''# ---- ROTATION SÛRE (protect_backups.py, 6 nov. 2026) : jamais de suppression « aveugle » ----
BK_DIR="${BK_DIR:-/backups}"; KEEP_MIN="${KEEP_MIN:-30}"; HIGH_PCT="${HIGH_PCT:-90}"; WARN_PCT="${WARN_PCT:-80}"; GOOD_NEWER_MIN="${GOOD_NEWER_MIN:-5}"
USED="${BK_USED_OVERRIDE:-$(df -P "$BK_DIR" | awk 'NR==2 {gsub("%","",$5); print $5}')}"
case "$USED" in ''|*[!0-9]*) USED=0 ;; esac
NFILES=$({ ls -1 "$BK_DIR"/pfe_db-*.sql.gz 2>/dev/null || true; } | wc -l)
if [ "$USED" -ge "$WARN_PCT" ]; then echo "[backup] ALERTE: systeme de fichiers du volume occupe a ${USED}%"; fi
if [ "$USED" -ge "$HIGH_PCT" ] && [ "$NFILES" -gt "$KEEP_MIN" ]; then
  GOOD=0
  for f in $(ls -1t "$BK_DIR"/pfe_db-*.sql.gz | head -n "$GOOD_NEWER_MIN"); do
    if gzip -t "$f" 2>/dev/null; then GOOD=$((GOOD + 1)); fi
  done
  if [ "$GOOD" -ge "$GOOD_NEWER_MIN" ]; then
    ls -1t "$BK_DIR"/pfe_db-*.sql.gz | tail -n +"$((KEEP_MIN + 1))" | while read -r f; do
      b=$(basename "$f")
      if grep -qxF "$b" "$BK_DIR/.protected" 2>/dev/null; then echo "[backup] protege (restauration validee), conserve: $b"; continue; fi
      echo "[backup] ROTATION (volume a ${USED}%): suppression de $b"; rm -f -- "$f" "$f.sha256"
    done
  else
    echo "[backup] rotation ANNULEE: seulement $GOOD/$GOOD_NEWER_MIN dumps recents verifies"
  fi
else
  echo "[backup] aucune suppression (volume a ${USED}%, $NFILES dumps ; suppression possible seulement si >= ${HIGH_PCT}% ET > ${KEEP_MIN} dumps)"
fi
# ---- fin de la rotation sûre ----'''


def script_a():
    return '''set -euo pipefail
TS=$(date +%Y%m%d-%H%M%S)
F=/backups/pfe_db-$TS.sql.gz
T=$F.partial
trap 'rm -f "$T"' ERR
echo "[backup] demarrage $TS"
pg_dump -h postgres-rw.pfe.svc.cluster.local -U "$PGUSER" -d pfe_db --no-owner --no-privileges | gzip > "$T"
gzip -t "$T"
[ "$(stat -c %s "$T")" -gt 10240 ] || { echo "[backup] dump anormalement petit"; exit 1; }
mv "$T" "$F"
( cd /backups && sha256sum "pfe_db-$TS.sql.gz" > "pfe_db-$TS.sql.gz.sha256" )
echo "[backup] OK: $(du -h "$F" | cut -f1)"
''' + ROTATION + '''
echo "[backup] fichiers:"; ls -lh /backups/pfe_db-*.sql.gz
'''


def script_b():
    return '''set -euo pipefail
TS=$(date +%Y%m%d-%H%M%S)
F=/backups/pfe_db-$TS.sql.gz
T=$F.partial
trap 'rm -f "$T"' ERR
echo "[backup-b] demarrage $TS"
pg_dump -h postgres-rw.pfe.svc.cluster.local -U "$PGUSER" -d pfe_db --no-owner --no-privileges | gzip > "$T"
gzip -t "$T"
[ "$(stat -c %s "$T")" -gt 10240 ] || { echo "[backup-b] dump anormalement petit"; exit 1; }
mv "$T" "$F"
( cd /backups && sha256sum "pfe_db-$TS.sql.gz" > "pfe_db-$TS.sql.gz.sha256" )
echo "[backup-b] OK: $(du -h "$F" | cut -f1)"
''' + ROTATION.replace("[backup]", "[backup-b]") + '''
echo "[backup-b] fichiers:"; ls -lh /backups/pfe_db-*.sql.gz
'''


def new_script(cron):
    return script_a() if CRONJOBS[cron] == "a" else script_b()


# ───────────────────────── accès cluster (remplaçables dans les tests) ─────────────────────────

def kubectl(*args, timeout=90, stdin=None):
    p = subprocess.run(["kubectl", *args], input=stdin, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    return p.returncode, p.stdout, p.stderr


def read_cron(cron, kc=None):
    kc = kc or kubectl
    rc, out, err = kc("get", "cronjob", cron, "-n", NS, "-o", "json")
    if rc != 0:
        raise SystemExit("lecture du CronJob %s impossible : %s" % (cron, err.strip()[:200]))
    return json.loads(out)


def get_script(obj):
    return obj["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0]["args"][0]


def patch_script(cron, script, kc=None):
    kc = kc or kubectl
    patch = [{"op": "test", "path": "/spec/jobTemplate/spec/template/spec/containers/0/command/0", "value": "/bin/bash"},
             {"op": "replace", "path": "/spec/jobTemplate/spec/template/spec/containers/0/args/0", "value": script}]
    rc, out, err = kc("patch", "cronjob", cron, "-n", NS, "--type", "json", "-p", json.dumps(patch))
    if rc != 0:
        raise SystemExit("patch de %s refusé : %s" % (cron, err.strip()[:300]))


def run_plan(say=print):
    for cron in CRONJOBS:
        old = get_script(read_cron(cron))
        new = new_script(cron)
        say("=== %s : %s ===" % (cron, "déjà à jour" if old == new else "script modifié"))
        for ln in difflib.unified_diff(old.splitlines(), new.splitlines(), "actuel", "nouveau", lineterm="", n=0):
            say("  " + ln)
    say("\nRETOUR ARRIÈRE : python protect_backups.py --rollback <fichier d'export affiché par --apply>")
    say("DRY-RUN terminé : rien n'a été écrit. Ajouter --apply pour appliquer.")


def run_apply(say=print, kc=None, now=None):
    now = now or dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    os.makedirs(SNAP_DIR, exist_ok=True)
    saved = {}
    for cron in CRONJOBS:
        obj = read_cron(cron, kc)
        path = os.path.join(SNAP_DIR, "cronjob-%s-AVANT-protection-%s.json" % (cron, now))
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False)
        saved[cron] = path
        say("export de %s : %s" % (cron, path))
    for cron in CRONJOBS:
        script = new_script(cron)
        patch_script(cron, script, kc)
        back = get_script(read_cron(cron, kc))
        if back != script:
            raise SystemExit("relecture de %s différente du script posé : ARRÊT (retour arrière : --rollback %s)" % (cron, saved[cron]))
        say("%s : script de rotation sûre posé et relu à l'identique" % cron)
    say("RETOUR ARRIÈRE : " + " ; ".join("python protect_backups.py --rollback %s" % p for p in saved.values()))
    return saved


def run_rollback(path, say=print, kc=None):
    with open(path, encoding="utf-8") as fh:
        obj = json.load(fh)
    cron = obj["metadata"]["name"]
    if cron not in CRONJOBS:
        raise SystemExit("CronJob inattendu : " + cron)
    patch_script(cron, get_script(obj), kc)
    say("%s : script d'origine remis (%s)" % (cron, path))


def wait_job(name, timeout=300, kc=None):
    kc = kc or kubectl
    end = time.time() + timeout
    while time.time() < end:
        rc, out, _ = kc("get", "job", name, "-n", NS, "-o", "jsonpath={.status.succeeded}/{.status.failed}")
        if rc == 0 and out.strip().startswith("1/"):
            return True
        if rc == 0 and re.match(r"^\d*/[1-9]", out.strip()):
            return False
        time.sleep(5)
    return False


def run_verify(say=print, kc=None):
    kc = kc or kubectl
    stamp = dt.datetime.now().strftime("%H%M%S")
    ok_all = True
    for cron in CRONJOBS:
        job = "bk-prot-%s-%s" % (CRONJOBS[cron], stamp)
        rc, out, err = kc("create", "job", job, "--from=cronjob/" + cron, "-n", NS)
        if rc != 0:
            raise SystemExit("création du Job %s impossible : %s" % (job, err.strip()[:200]))
        say("Job %s créé ; attente de la fin (un Job à la fois)…" % job)
        done = wait_job(job, 300, kc)
        rc, log, _ = kc("logs", "job/" + job, "-n", NS)
        listing = [l for l in log.splitlines() if "pfe_db-" in l and l.strip().startswith("-rw")]
        checks = {"Job terminé": done, "dump contrôlé (OK)": "OK:" in log, "aucune suppression": "aucune suppression" in log and "ROTATION (volume" not in log,
                  "au moins 8 dumps listés": len(listing) >= 8}
        for k, v in checks.items():
            say("   %-28s %s" % (k, "PASS" if v else "FAIL"))
        ok_all = ok_all and all(checks.values())
    say("VERDICT : " + ("PASS — une sauvegarde a été ajoutée à chaque volume, aucune n'a été supprimée" if ok_all else "FAIL — analyser les journaux des Jobs"))
    return 0 if ok_all else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--rollback", metavar="FICHIER")
    ap.add_argument("--verify", action="store_true")
    a = ap.parse_args(argv)
    if a.rollback:
        run_rollback(a.rollback)
    elif a.apply:
        run_apply()
    elif a.verify:
        return run_verify()
    else:
        run_plan()
    return 0


if __name__ == "__main__":
    sys.exit(main())
