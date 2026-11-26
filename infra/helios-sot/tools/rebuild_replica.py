# -*- coding: utf-8 -*-
"""RECONSTRUCTION d'une réplique CNPG bloquée SANS supprimer ni PVC, ni PV, ni donnée. DRY-RUN PAR DÉFAUT.

Procédure (celle de `kubectl cnpg destroy --keep-pvc`, vérifiée dans le code source de l'opérateur 1.28.4, le plugin n'étant pas installé) :
  1. le PVC de l'instance est annoté `cnpg.io/pvcStatus=detached` : l'opérateur l'IGNORE désormais ;
  2. l'`ownerReference` vers le Cluster est retiré du PVC (le ramasse-miettes ne peut plus l'emporter avec le Cluster) ;
  3. le POD de l'instance est supprimé — le seul objet supprimé ; les données restent dans le PVC conservé ;
  4. l'opérateur crée une nouvelle instance (numéro suivant, nouveau PVC, `pg_basebackup` depuis le primaire).
Aucune suppression de PVC, de PV, de sauvegarde ni de donnée ; le PV reste en `Retain`. Retour arrière : ré-annoter `ready` et remettre l'ownerReference
(la commande exacte, avec l'uid du Cluster, est affichée et enregistrée dans `helios-state\\backups\\rebuild-replica-<horodatage>.json`).

Pré-conditions (TOUTES vérifiées en lecture seule, sinon REFUS et rien n'est écrit) :
  - le Cluster existe, `currentPrimary == targetPrimary` ≠ l'instance visée ; primaire prêt et répondant à SELECT 1 ; `spec.instances == 2` et 1 seule instance prête ;
  - l'instance visée est une RÉPLIQUE, non prête ; son PVC est `ready` et son PV est en `Retain` ;
  - au moins un nœud peut accueillir la nouvelle instance (ni taint NoSchedule non toléré, ni autre instance : anti-affinité) ;
  - espace libre ≥ 3 Go sur le nœud du PVC ;
  - l'ancien primaire n'a AUCUNE écriture après la fourche (`pg_waldump` : uniquement des enregistrements XLOG) ;
  - sauvegardes A et B de moins de `--backup-max-age-min` minutes ET preuve de restauration RESTAURABLE de chacune ;
  - aucun démarrage de VM en cours (aucun vmrun, aucun VMRUN_START récent sans START_DONE) ; seul incident VM ouvert toléré : celui du nœud de l'instance.

  python rebuild_replica.py --instance postgres-5            dry-run : pré-conditions + diff exact, rien n'est écrit
  python rebuild_replica.py --instance postgres-5 --apply    MODIFIANTE (métadonnées du PVC + suppression du Pod seul)
"""
import argparse
import collections
import datetime as dt
import glob
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
CLUSTER = "postgres"
BACKUP_DIR = r"C:\Users\houss\helios-state\backups"
MIN_FREE_GB = 3
INSTANCE_RE = re.compile(r"^postgres-\d+$")
HEALTHY = "Cluster in healthy state"
# Les fichiers .history et les segments WAL sont nommés avec la timeline en HEXADÉCIMAL majuscule sur 8 chiffres (timeline 118 -> 00000076.history).
# Erreur corrigée le 6 nov. 2026 : la version décimale (`to_char(..., 'FM00000000')` -> 00000118.history) ne trouvait aucun fichier.
HISTORY_SQL = ("select pg_read_file('pg_wal/' || upper(lpad(to_hex(timeline_id), 8, '0')) || '.history') "
               "from pg_control_checkpoint()")


# ───────────────────────── fonctions pures (testées) ─────────────────────────

def wal_segment_name(tli, lsn):
    """Nom du segment WAL (16 Mo) qui contient `lsn` ("57/86018340") sur la timeline `tli`."""
    hi, lo = (int(x, 16) for x in lsn.split("/"))
    return "%08X%08X%08X" % (tli, hi, lo >> 24)


def fork_info(history_text):
    """Dernière ligne utile d'un fichier .history : (timeline mère, LSN de fourche) ou None."""
    last = None
    for ln in history_text.splitlines():
        m = re.match(r"^(\d+)\s+([0-9A-Fa-f]+/[0-9A-Fa-f]+)\s", ln)
        if m:
            last = (int(m.group(1)), m.group(2).upper())
    return last


def rmgr_counts(waldump_output):
    c = collections.Counter()
    for ln in waldump_output.splitlines():
        m = re.match(r"^rmgr:\s+(\S+)", ln)
        if m:
            c[m.group(1)] += 1
    return c


def eligible_nodes(nodes, pods, target_pod, cluster=CLUSTER):
    """Nœuds où la nouvelle instance peut être planifiée : Ready, non cordonnés, sans taint NoSchedule/NoExecute (les pods CNPG ne
    tolèrent que not-ready/unreachable), et SANS autre instance du cluster (anti-affinité `required`). Le pod visé est ignoré
    (il va disparaître)."""
    busy = {p["spec"].get("nodeName") for p in pods
            if (p["metadata"].get("labels") or {}).get("cnpg.io/cluster") == cluster and p["metadata"]["name"] != target_pod}
    out = []
    for n in nodes:
        name = n["metadata"]["name"]
        ready = any(c["type"] == "Ready" and c["status"] == "True" for c in n["status"].get("conditions", []))
        taints = [t for t in n["spec"].get("taints", []) if t.get("effect") in ("NoSchedule", "NoExecute")]
        if ready and not n["spec"].get("unschedulable") and not taints and name not in busy:
            out.append(name)
    return out


def reports_prove_dumps(reports, dumps, now, max_age_min):
    """Pure : pour chaque source ("A","B"), il faut un dump de moins de max_age_min minutes ET un rapport RESTAURABLE de CE fichier."""
    problems = []
    for src in ("A", "B"):
        d = dumps.get(src)
        if not d:
            problems.append(f"sauvegarde {src} : aucun dump lisible")
            continue
        age = (now - d["mtime"]) / 60.0
        if age > max_age_min:
            problems.append(f"sauvegarde {src} : {d['name']} date de {age:.0f} min (maximum {max_age_min})")
        ok = any(r.get("source") == src and r.get("file") == d["name"] and r.get("sha256") == d["sha256"] and r.get("verdict") == "RESTAURABLE"
                 for r in reports)
        if not ok:
            problems.append(f"sauvegarde {src} : aucune preuve de restauration RESTAURABLE pour {d['name']}")
    return problems


def preconditions(f):
    """Pure : liste des motifs de REFUS (vide = on peut continuer). Tout fait absent est un refus (fail-closed)."""
    p = []
    inst = f["instance"]
    c = f.get("cluster") or {}
    if not c:
        return ["Cluster introuvable"]
    st, sp = c.get("status", {}), c.get("spec", {})
    if st.get("currentPrimary") in (None, inst):
        p.append(f"{inst} est (ou serait) le PRIMAIRE courant ({st.get('currentPrimary')}) : jamais")
    if st.get("targetPrimary") != st.get("currentPrimary"):
        p.append(f"bascule en cours (current={st.get('currentPrimary')} target={st.get('targetPrimary')}) : attendre")
    if sp.get("instances") != 2 or st.get("readyInstances") != 1:
        p.append(f"attendu : 2 instances voulues et 1 prête (obtenu {sp.get('instances')} voulues, {st.get('readyInstances')} prêtes)")
    if not f.get("primary_ready"):
        p.append("le primaire n'est pas prêt ou ne répond pas à SELECT 1")
    pod = f.get("pod")
    if not pod:
        p.append(f"pod {inst} introuvable")
    else:
        role = (pod["metadata"].get("labels") or {}).get("cnpg.io/instanceRole")
        ready = any(x.get("type") == "Ready" and x.get("status") == "True" for x in pod["status"].get("conditions", []))
        if role == "primary":
            p.append(f"{inst} porte le rôle primary")
        if ready:
            p.append(f"{inst} est prêt : il n'y a rien à reconstruire")
    pvc = f.get("pvc")
    if not pvc:
        p.append(f"PVC {inst} introuvable")
    else:
        if (pvc["metadata"].get("annotations") or {}).get("cnpg.io/pvcStatus") != "ready":
            p.append(f"PVC {inst} : cnpg.io/pvcStatus = {(pvc['metadata'].get('annotations') or {}).get('cnpg.io/pvcStatus')!r} (attendu 'ready')")
        if not any(o.get("kind") == "Cluster" and o.get("name") == CLUSTER for o in pvc["metadata"].get("ownerReferences", [])):
            p.append("PVC sans ownerReference vers le Cluster : déjà modifié ?")
    pv = f.get("pv")
    if not pv or pv["spec"].get("persistentVolumeReclaimPolicy") != "Retain":
        p.append(f"PV du PVC : reclaimPolicy = {pv and pv['spec'].get('persistentVolumeReclaimPolicy')!r} (attendu 'Retain')")
    if not f.get("eligible_nodes"):
        p.append("aucun nœud éligible pour la nouvelle instance (taint, anti-affinité) : elle resterait Pending")
    if f.get("free_gb") is None or f["free_gb"] < MIN_FREE_GB:
        p.append(f"espace libre insuffisant sur le nœud du PVC : {f.get('free_gb')} Go (minimum {MIN_FREE_GB})")
    nu = f.get("no_unique_writes")
    if nu is not True:
        p.append("absence d'écriture sur l'ancien primaire après la fourche NON prouvée : " + str(f.get("no_unique_writes_detail")))
    p += f.get("backup_problems", [])
    if f.get("vm_start_in_progress"):
        p.append("un démarrage de VM est en cours")
    other = [i for i in f.get("open_vm_incidents", []) if i not in f.get("tolerated_incidents", [])]
    if other:
        p.append(f"incident(s) de périmètre VM ouvert(s) hors instance visée : {other}")
    return p


def owner_ref_removal_patch(pvc, cluster=CLUSTER):
    """Correctif JSON qui retire UNIQUEMENT l'ownerReference du Cluster (avec `test` : il échoue si l'entrée a bougé)."""
    refs = pvc["metadata"].get("ownerReferences", [])
    idx = next(i for i, o in enumerate(refs) if o.get("kind") == "Cluster" and o.get("name") == cluster)
    return [{"op": "test", "path": f"/metadata/ownerReferences/{idx}/kind", "value": "Cluster"},
            {"op": "test", "path": f"/metadata/ownerReferences/{idx}/name", "value": cluster},
            {"op": "remove", "path": f"/metadata/ownerReferences/{idx}"}]


# ───────────────────────── cœur testé (ordre des écritures) ─────────────────────────

def run_rebuild(env, instance, apply, say=print):
    f = env.collect(instance)
    refus = preconditions(f)
    say(f"=== RECONSTRUCTION DE LA RÉPLIQUE {instance} ({'APPLICATION — MODIFIANTE' if apply else 'DRY-RUN — LECTURE SEULE'}) ===")
    say(f"cluster {CLUSTER} ({NS}) : primaire {f['cluster']['status'].get('currentPrimary')}, prêtes {f['cluster']['status'].get('readyInstances')}/{f['cluster']['spec'].get('instances')}")
    if f.get("no_unique_writes_detail"):
        say("ancien primaire :", f["no_unique_writes_detail"])
    for line in f.get("backup_lines", []):
        say("sauvegardes :", line)
    say("nœuds éligibles pour la nouvelle instance :", f.get("eligible_nodes"), "| espace libre sur le nœud du PVC :", f.get("free_gb"), "Go")
    if refus:
        for r in refus:
            say("REFUS :", r)
        return 1
    pvc = f["pvc"]
    ref = next(o for o in pvc["metadata"]["ownerReferences"] if o.get("kind") == "Cluster")
    patch = owner_ref_removal_patch(pvc)
    say("\nOBJETS MODIFIÉS (aucun PVC, aucun PV, aucune donnée supprimé) :")
    say(f"  1. pvc/{instance} : annotation cnpg.io/pvcStatus  ready -> detached")
    say(f"  2. pvc/{instance} : ownerReference Cluster/{CLUSTER} (uid {ref.get('uid')}) retirée")
    say(f"  3. pod/{instance} : SUPPRIMÉ (le seul objet supprimé ; PGDATA reste dans le PVC conservé)")
    say("  puis, par l'opérateur : nouvelle instance + nouveau PVC (pg_basebackup depuis le primaire), sur", f["eligible_nodes"])
    say("\nDIFF EXACT (métadonnées du PVC)")
    say(f"  --- pvc/{instance} actuel\n  +++ pvc/{instance} après\n  -    cnpg.io/pvcStatus: ready\n  +    cnpg.io/pvcStatus: detached\n"
        f"  -  ownerReferences: [{{kind: Cluster, name: {CLUSTER}, uid: {ref.get('uid')}}}]\n  +  ownerReferences: []")
    say("\nINTERRUPTION : la base reste servie par le primaire (aucune coupure) ; elle est à UNE instance jusqu'à la fin du clonage (≈ 1 à 3 min).")
    say("RETOUR ARRIÈRE (tant que le PVC existe, donc toujours) :")
    say(f"  kubectl -n {NS} annotate pvc {instance} cnpg.io/pvcStatus=ready --overwrite")
    say(f"  kubectl -n {NS} patch pvc {instance} --type json -p '[{{\"op\":\"add\",\"path\":\"/metadata/ownerReferences\",\"value\":[{json.dumps({k: ref[k] for k in ('apiVersion', 'kind', 'name', 'uid', 'controller', 'blockOwnerDeletion') if k in ref})}]}}]'")
    if not apply:
        say("\nDRY-RUN terminé : rien n'a été écrit. Ajouter --apply pour exécuter (autorisation spécifique requise).")
        return 0

    env.save_backup(instance, pvc)
    env.annotate_detached(instance)
    if env.pvc_status(instance) != "detached":
        say("ARRÊT : l'annotation n'est pas relue — le Pod n'est PAS supprimé.")
        return 1
    env.remove_owner_reference(instance, patch)
    if env.has_cluster_owner(instance):
        say("ARRÊT : l'ownerReference est toujours présente — le Pod n'est PAS supprimé.")
        return 1
    say(f"\nPVC {instance} détaché et relu (annotation detached, ownerReference retirée). Suppression du Pod {instance} …")
    env.delete_pod(instance)
    t0 = env.now()
    while True:
        st = env.cluster_status()
        if st.get("readyInstances") == 2 and st.get("phase") == HEALTHY:
            break
        if env.now() - t0 > 900:
            say(f"ARRÊT : après 900 s la base n'est pas à 2/2 (phase {st.get('phase')!r}, prêtes {st.get('readyInstances')}). "
                "Le PVC détaché est INTACT ; retour arrière ci-dessus.")
            return 1
        env.sleep(5)
    new = env.new_instances(instance)
    say(f"base à 2/2 en {env.now() - t0:.0f} s ; nouvelle(s) instance(s) : {new}")
    rep = env.replication()
    say("réplication :", rep)
    if not (rep and rep.get("state") == "streaming" and rep.get("lag_bytes", 10**12) < 1_000_000):
        say("ATTENTION : la réplication n'est pas en streaming avec un retard < 1 Mo — NE PAS poursuivre les essais réels.")
        return 1
    if env.pvc_status(instance) != "detached" or not env.pv_retained(instance):
        say("ANOMALIE : l'ancien PVC/PV n'est plus dans l'état attendu — vérifier immédiatement.")
        return 1
    say(f"\nRÉSULTAT : réplique reconstruite, ancien volume {instance} conservé (PVC detached, PV Retain). "
        "#4929 ne se ferme PAS à la main : VmPowerWatch le fait seul après deux passes de preuves.")
    return 0


# ───────────────────────── accès réel ─────────────────────────

class Real:
    def __init__(self, backup_max_age_min):
        spec = importlib.util.spec_from_file_location("restore_probe", os.path.join(HERE, "restore_probe.py"))
        self.rp = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.rp)
        self.max_age = backup_max_age_min

    def now(self):
        return time.time()

    def sleep(self, s):
        time.sleep(s)

    def kj(self, *a):
        rc, out, err = self.rp.kubectl(*a, timeout=120)
        return json.loads(out) if rc == 0 and out.strip() else None

    def collect(self, inst):
        rp = self.rp
        f = {"instance": inst, "cluster": self.kj("get", "cluster.postgresql.cnpg.io", CLUSTER, "-n", NS, "-o", "json")}
        f["pod"] = self.kj("get", "pod", inst, "-n", NS, "-o", "json")
        pvc = self.kj("get", "pvc", inst, "-n", NS, "-o", "json")
        f["pvc"] = pvc
        f["pv"] = self.kj("get", "pv", pvc["spec"]["volumeName"], "-o", "json") if pvc else None
        nodes = (self.kj("get", "nodes", "-o", "json") or {}).get("items", [])
        pods = (self.kj("get", "pods", "-n", NS, "-o", "json") or {}).get("items", [])
        f["eligible_nodes"] = eligible_nodes(nodes, pods, inst)
        prim = (f["cluster"] or {}).get("status", {}).get("currentPrimary")
        f["primary_ready"] = False
        if prim:
            rc, out, _ = rp.kubectl("exec", "-n", NS, prim, "-c", "postgres", "--", "psql", "-U", "postgres", "-d", "pfe_db", "-At", "-c", "select 1", timeout=60)
            f["primary_ready"] = out.strip() == "1"
        f["free_gb"] = None
        if f["pv"] and f["pod"]:
            node = (f["pv"]["spec"]["nodeAffinity"]["required"]["nodeSelectorTerms"][0]["matchExpressions"][0]["values"][0])
            rc, ip, _ = rp.kubectl("get", "node", node, "-o", "jsonpath={.status.addresses[?(@.type==\"InternalIP\")].address}")
            path = (f["pv"]["spec"].get("local") or f["pv"]["spec"].get("hostPath") or {}).get("path")
            rc, out, _ = rp.ssh(ip.strip(), f"df -BG --output=avail {path} | tail -1")
            f["free_gb"] = int(re.sub(r"\D", "", out) or 0) if rc == 0 else None
        f["no_unique_writes"], f["no_unique_writes_detail"] = self.no_unique_writes(prim, inst)
        dumps, lines = {}, []
        for src in ("A", "B"):
            s = rp.resolve(src)
            rc, out, _ = rp.ssh(s["ip"], f"cd {s['path']} && F=$(ls -1t pfe_db-*.sql.gz | head -1) && stat -c '%n %Y' $F && sha256sum $F")
            ls = out.strip().splitlines()
            if rc == 0 and len(ls) >= 2:
                n, m = ls[0].split()
                dumps[src] = {"name": n, "mtime": int(m), "sha256": ls[1].split()[0]}
                lines.append(f"{src}: {n} ({(self.now() - int(m)) / 60:.0f} min)")
        reports = []
        for path in glob.glob(os.path.join(BACKUP_DIR, "restore-proof-*.json")):
            try:
                with open(path, encoding="utf-8") as fh:
                    d = json.load(fh)
                reports.append({"source": d.get("source"), "file": d.get("file"), "sha256": d.get("sha256"), "verdict": d.get("verdict")})
            except (OSError, ValueError):
                pass
        f["backup_problems"] = reports_prove_dumps(reports, dumps, self.now(), self.max_age)
        f["backup_lines"] = lines
        f["vm_start_in_progress"] = self.vm_start_in_progress()
        inc = self.open_vm_incidents(prim)
        f["open_vm_incidents"] = inc
        node_app = (f["pod"] or {}).get("spec", {}).get("nodeName")
        f["tolerated_incidents"] = [i for i in inc if i.endswith("/" + (node_app or "?"))]
        return f

    def no_unique_writes(self, primary, inst):
        rp = self.rp
        if not primary:
            return None, "primaire inconnu"
        rc, out, err = rp.kubectl("exec", "-n", NS, primary, "-c", "postgres", "--", "psql", "-U", "postgres", "-d", "pfe_db", "-At", "-c",
                                  HISTORY_SQL, timeout=60)
        fi = fork_info(out) if rc == 0 else None
        if not fi:
            return None, "fichier .history du primaire illisible"
        tli, lsn = fi
        seg = wal_segment_name(tli, lsn)
        script = (f"cd /var/lib/postgresql/data/pgdata/pg_wal && ls {seg} >/dev/null 2>&1 || {{ echo SEGMENT_ABSENT; exit 0; }}; "
                  f"timeout 30 pg_waldump -p . -t {tli} -s {lsn} {seg} 2>&1 | head -50")
        rc, out, err = rp.kubectl("exec", "-i", "-n", NS, inst, "-c", "postgres", "--", "sh", "-c", script, timeout=90)
        if "SEGMENT_ABSENT" in out:
            return None, f"segment {seg} de la fourche absent : preuve impossible"
        counts = rmgr_counts(out)
        others = {k: v for k, v in counts.items() if k != "XLOG"}
        detail = f"fourche {tli}@{lsn} (segment {seg}) : enregistrements après la fourche = {dict(counts)}"
        return (not others and sum(counts.values()) > 0), detail

    def vm_start_in_progress(self):
        p = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", "(Get-Process -Name vmrun -ErrorAction SilentlyContinue | Measure-Object).Count"],
                           capture_output=True, text=True, timeout=30)
        if p.stdout.strip() not in ("0", ""):
            return True
        last = None
        try:
            with open(r"C:\Users\houss\helios-state\helios-watchdog.log", encoding="utf-8", errors="replace") as fh:
                for ln in fh:
                    try:
                        d = json.loads(ln)
                    except ValueError:
                        continue
                    if d.get("event") in ("VMRUN_START", "START_DONE"):
                        last = d
        except OSError:
            return False
        if last and last["event"] == "VMRUN_START":
            ts = dt.datetime.strptime(last["ts"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=dt.timezone.utc)
            return (dt.datetime.now(dt.timezone.utc) - ts).total_seconds() < 600
        return False

    def open_vm_incidents(self, primary):
        if not primary:
            return ["(primaire inconnu)"]
        sql = ("select namespace||'/'||app||'#'||id from incident where status in ('NEW','OPEN','IN_PROGRESS','WAITING_FOR_APPROVAL','RESOLUTION_FAILED') and "
               "((namespace='k8s-nodes' and app in ('k8s-worker1','k8s-worker2','k8s-master') and incident_type='NODE_NOT_READY') or "
               "(namespace='infra' and app in ('k8s-lb2','k8s-monitoring') and incident_type in ('VM_DOWN','VM_UNREACHABLE')))")
        rc, out, _ = self.rp.kubectl("exec", "-n", NS, primary, "-c", "postgres", "--", "psql", "-U", "postgres", "-d", "pfe_db", "-At", "-c", sql, timeout=60)
        return [re.sub(r"#\d+$", "", x) for x in out.split()] if rc == 0 else ["(illisible)"]

    def save_backup(self, inst, pvc):
        os.makedirs(BACKUP_DIR, exist_ok=True)
        path = os.path.join(BACKUP_DIR, f"rebuild-replica-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"pvc": inst, "annotations": pvc["metadata"].get("annotations"), "labels": pvc["metadata"].get("labels"),
                       "ownerReferences": pvc["metadata"].get("ownerReferences"), "pv": pvc["spec"].get("volumeName")}, fh, indent=2, ensure_ascii=False)
        print("sauvegarde des métadonnées du PVC :", path)

    def annotate_detached(self, inst):
        rc, out, err = self.rp.kubectl("annotate", "pvc", inst, "-n", NS, "cnpg.io/pvcStatus=detached", "--overwrite")
        if rc != 0:
            raise SystemExit("annotation impossible : " + err.strip()[:200])

    def pvc_status(self, inst):
        rc, out, _ = self.rp.kubectl("get", "pvc", inst, "-n", NS, "-o", "jsonpath={.metadata.annotations.cnpg\\.io/pvcStatus}")
        return out.strip()

    def remove_owner_reference(self, inst, patch):
        rc, out, err = self.rp.kubectl("patch", "pvc", inst, "-n", NS, "--type", "json", "-p", json.dumps(patch))
        if rc != 0:
            raise SystemExit("retrait de l'ownerReference impossible : " + err.strip()[:200])

    def has_cluster_owner(self, inst):
        pvc = self.kj("get", "pvc", inst, "-n", NS, "-o", "json")
        return any(o.get("kind") == "Cluster" for o in pvc["metadata"].get("ownerReferences", []))

    def delete_pod(self, inst):
        rc, out, err = self.rp.kubectl("delete", "pod", inst, "-n", NS)
        if rc != 0:
            raise SystemExit("suppression du Pod impossible : " + err.strip()[:200])

    def cluster_status(self):
        c = self.kj("get", "cluster.postgresql.cnpg.io", CLUSTER, "-n", NS, "-o", "json") or {}
        return c.get("status", {})

    def new_instances(self, old):
        pods = (self.kj("get", "pods", "-n", NS, "-l", f"cnpg.io/cluster={CLUSTER}", "-o", "json") or {}).get("items", [])
        return sorted(p["metadata"]["name"] + "@" + str(p["spec"].get("nodeName")) for p in pods if p["metadata"]["name"] != old)

    def replication(self):
        st = self.cluster_status()
        prim = st.get("currentPrimary")
        rc, out, _ = self.rp.kubectl("exec", "-n", NS, prim, "-c", "postgres", "--", "psql", "-U", "postgres", "-d", "pfe_db", "-At", "-F", "|", "-c",
                                     "select state||'|'||coalesce(pg_wal_lsn_diff(sent_lsn, replay_lsn),-1)::bigint from pg_stat_replication limit 1", timeout=60)
        if rc != 0 or "|" not in out:
            return None
        s, lag = out.strip().split("|")
        return {"state": s, "lag_bytes": int(lag)}

    def pv_retained(self, inst):
        pvc = self.kj("get", "pvc", inst, "-n", NS, "-o", "json")
        pv = self.kj("get", "pv", pvc["spec"]["volumeName"], "-o", "json") if pvc else None
        return bool(pv and pv["spec"].get("persistentVolumeReclaimPolicy") == "Retain")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--instance", required=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--backup-max-age-min", type=int, default=60)
    a = ap.parse_args(argv)
    if not INSTANCE_RE.match(a.instance):
        raise SystemExit("nom d'instance invalide (attendu : postgres-<n>)")
    return run_rebuild(Real(a.backup_max_age_min), a.instance, a.apply)


if __name__ == "__main__":
    sys.exit(main())
