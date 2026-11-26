# -*- coding: utf-8 -*-
"""PREUVE DE RESTAURABILITÉ d'une sauvegarde `pg_dump` : on la RESTAURE vraiment dans un PostgreSQL jetable, puis on compte.

`gzip -t` prouve seulement que l'archive est lisible. Ici on prouve que le dump se RECHARGE sans erreur et redonne EXACTEMENT les
lignes qu'il contient :
  1. métadonnées et SHA-256 relevés sur le nœud (lecture seule via SSH), transfert vérifié par la même empreinte ;
  2. analyse locale du SQL : tables créées, lignes de chaque bloc COPY (le « attendu » vient du fichier lui-même), pied de page pg_dump ;
  3. restauration (`ON_ERROR_STOP`, une seule transaction) dans un serveur PostgreSQL JETABLE, sans écoute réseau, lancé dans un pod
     qui ne sert AUCUN trafic (par défaut `postgres-5`, l'ancien primaire bloqué) et UNIQUEMENT sous /run (emptyDir) — jamais sur son
     volume de données ni sur celui d'un pod prêt ;
  4. comptage de chaque table restaurée = lignes COPY du dump ; comparaison INFORMATIVE avec la base vivante ;
  5. nettoyage systématique (arrêt du serveur jetable, dossier supprimé).

  python restore_probe.py --source A            dernière sauvegarde de postgres-backup   (PVC postgres-backups, nœud .158)
  python restore_probe.py --source B --file pfe_db-20261106-113034.sql.gz
  python restore_probe.py --source A --analysis-only    LECTURE SEULE stricte : étapes 1 et 2 seulement (aucune écriture dans un pod) ;
                                                        ne prouve PAS la restaurabilité, le dit, et sort avec le code 3

Garde-fous : refuse un pod primaire ; refuse un pod prêt sauf --allow-ready-replica ; ne lit que sous /run ; ne supprime que le dossier
qu'il a lui-même créé. LECTURE SEULE sur le cluster, la base vivante et les nœuds ; il écrit uniquement dans l'emptyDir du pod bac à sable
et un rapport JSON local.
"""
import argparse
import datetime as dt
import gzip
import hashlib
import io
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
PVC = {"A": "postgres-backups", "B": "postgres-backups-b"}
REPORT_DIR = r"C:\Users\houss\helios-state\backups"
PORT = 55432
COPY_RE = re.compile(r"^COPY ([^ ]+) \(.*\) FROM stdin;$")


# ──────────────────────────── analyse locale (pure, testée) ────────────────────────────

def analyze_dump(text):
    """Analyse du SQL d'un pg_dump en texte : tables créées, lignes de chaque bloc COPY, pied de page."""
    created, copies = [], {}
    cur, rows = None, 0
    for line in text.splitlines():
        if cur is not None:
            if line == "\\.":
                copies[cur] = copies.get(cur, 0) + rows
                cur, rows = None, 0
            else:
                rows += 1
            continue
        m = COPY_RE.match(line)
        if m:
            cur, rows = m.group(1), 0
            continue
        m = re.match(r"^CREATE TABLE ([^ ]+) \(", line)
        if m:
            created.append(m.group(1))
    version = re.search(r"^-- Dumped by pg_dump version ([0-9.]+)", text, re.M)
    return {"tables_created": sorted(created), "copy_rows": copies, "unterminated_copy": cur is not None,
            "footer_ok": "PostgreSQL database dump complete" in text[-600:],
            "pg_dump_version": version.group(1) if version else None}


def normalise(name):
    """`public.incident` -> `incident` (les comptages restaurés sont par nom de table du schéma public)."""
    return name.split(".", 1)[1] if name.startswith("public.") else name


def compare(expected, restored):
    """Pure : écarts entre lignes COPY du dump (attendu) et comptages de la base restaurée. Une table sans COPY attend 0 ligne."""
    exp = {normalise(k): v for k, v in expected.items()}
    problems = []
    for t, n in sorted(restored.items()):
        if exp.get(t, 0) != n:
            problems.append(f"{t} : restaurée {n} ligne(s), le dump en contient {exp.get(t, 0)}")
    for t in sorted(set(exp) - set(restored)):
        problems.append(f"{t} : présente dans le dump (COPY) mais ABSENTE de la base restaurée")
    return problems


def parse_counts(output):
    res = {}
    for ln in output.splitlines():
        if "|" in ln:
            t, n = ln.rsplit("|", 1)
            if n.strip().isdigit():
                res[t.strip()] = int(n)
    return res


def verdict(facts):
    """Pure : liste des motifs d'échec (vide = restaurable)."""
    why = []
    if not facts.get("sha_ok"):
        why.append("somme de contrôle du transfert différente de celle du nœud")
    if not facts.get("gzip_ok"):
        why.append("archive gzip illisible ou tronquée")
    a = facts.get("analysis") or {}
    if not a.get("footer_ok"):
        why.append("pied de page pg_dump absent (dump incomplet)")
    if a.get("unterminated_copy"):
        why.append("bloc COPY non terminé")
    if not a.get("tables_created"):
        why.append("aucune table dans le dump")
    if facts.get("restore_rc") != 0:
        why.append(f"la restauration a échoué (code {facts.get('restore_rc')}) : {facts.get('restore_err', '')[:200]}")
    why += facts.get("count_problems", [])
    if facts.get("restore_rc") == 0 and not facts.get("restored_counts"):
        why.append("aucun comptage de table restaurée")
    if facts.get("restore_rc") == 0 and set(normalise(t) for t in (a.get("tables_created") or [])) != set(facts.get("restored_counts") or {}):
        why.append("l'ensemble des tables restaurées diffère de celui du dump")
    return why


# ──────────────────────────── accès cluster / nœuds ────────────────────────────

def run(args, timeout=120, stdin=None, binary=False):
    p = subprocess.run(args, input=stdin, capture_output=True, timeout=timeout)
    if binary:
        return p.returncode, p.stdout, p.stderr.decode("utf-8", "replace")
    return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")


def kubectl(*a, timeout=120, stdin=None):
    return run(["kubectl", *a], timeout=timeout, stdin=stdin)


def resolve(source):
    rc, pv, _ = kubectl("get", "pvc", PVC[source], "-n", NS, "-o", "jsonpath={.spec.volumeName}")
    if rc != 0 or not pv:
        raise SystemExit(f"PVC {PVC[source]} introuvable")
    rc, out, _ = kubectl("get", "pv", pv, "-o", "json")
    j = json.loads(out)
    path = (j["spec"].get("local") or j["spec"].get("hostPath") or {}).get("path")
    host = j["spec"]["nodeAffinity"]["required"]["nodeSelectorTerms"][0]["matchExpressions"][0]["values"][0]
    rc, ip, _ = kubectl("get", "node", host, "-o", "jsonpath={.status.addresses[?(@.type==\"InternalIP\")].address}")
    return {"pv": pv, "path": path, "node": host, "ip": ip.strip(), "reclaim": j["spec"].get("persistentVolumeReclaimPolicy")}


def ssh(ip, script, timeout=120, binary=False):
    return run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "kube@" + ip, "sudo -n sh -s"], timeout=timeout,
               stdin=script.encode("utf-8"), binary=binary)


def sandbox_exec(pod, script, timeout=180, stdin=None):
    return kubectl("exec", "-i", "-n", NS, pod, "-c", "postgres", "--", "sh", "-c", script, timeout=timeout, stdin=stdin)


def check_sandbox(pod, allow_ready):
    rc, out, _ = kubectl("get", "pod", pod, "-n", NS, "-o", "json")
    if rc != 0:
        raise SystemExit(f"pod bac à sable {pod} introuvable")
    j = json.loads(out)
    role = (j["metadata"].get("labels") or {}).get("cnpg.io/instanceRole")
    ready = any(c.get("type") == "Ready" and c.get("status") == "True" for c in j["status"].get("conditions", []))
    if role == "primary":
        raise SystemExit(f"REFUS : {pod} est le PRIMAIRE — jamais de bac à sable sur le primaire")
    if ready and not allow_ready:
        raise SystemExit(f"REFUS : {pod} est PRÊT (il sert du trafic en lecture) ; --allow-ready-replica pour passer outre")
    return {"role": role, "ready": ready}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", choices=("A", "B"), required=True)
    ap.add_argument("--file", help="nom du dump (défaut : le plus récent)")
    ap.add_argument("--sandbox-pod", default="postgres-5")
    ap.add_argument("--allow-ready-replica", action="store_true")
    ap.add_argument("--analysis-only", action="store_true", help="étapes 1-2 seulement : aucune restauration, aucune écriture dans un pod")
    ap.add_argument("--report-dir", default=REPORT_DIR)
    a = ap.parse_args(argv)
    t0 = time.time()
    facts = {"source": a.source}

    sb = None if a.analysis_only else check_sandbox(a.sandbox_pod, a.allow_ready_replica)
    src = resolve(a.source)
    print(f"=== PREUVE DE RESTAURATION — sauvegarde {a.source} ===")
    print(f"source  : PVC {PVC[a.source]} -> PV {src['pv']} (reclaim {src['reclaim']}) sur {src['node']} ({src['ip']}) : {src['path']}")
    if sb:
        print(f"bac à sable : pod {a.sandbox_pod} (rôle {sb['role']}, prêt={sb['ready']}), espace de travail /run uniquement")
    else:
        print("mode ANALYSE SEULE : aucune restauration, aucune écriture dans un pod")

    name = a.file
    if not name:
        rc, out, err = ssh(src["ip"], f"cd {src['path']} && ls -1t pfe_db-*.sql.gz | head -1")
        name = out.strip()
    if not re.fullmatch(r"pfe_db-[0-9]{8}-[0-9]{6}\.sql\.gz", name or ""):
        raise SystemExit(f"nom de dump invalide : {name!r}")
    rc, out, err = ssh(src["ip"], f"cd {src['path']} && stat -c '%s %Y' {name} && sha256sum {name}")
    lines = out.strip().splitlines()
    size, mtime = lines[0].split()
    sha_node = lines[1].split()[0]
    facts.update(file=name, size=int(size), mtime=dt.datetime.fromtimestamp(int(mtime), dt.timezone.utc).isoformat(timespec="seconds"), sha256=sha_node)
    print(f"dump    : {name} | {size} octets | écrit le {facts['mtime']} | sha256 {sha_node[:16]}…")

    rc, raw, err = ssh(src["ip"], f"cat {src['path']}/{name}", timeout=180, binary=True)
    facts["sha_ok"] = rc == 0 and hashlib.sha256(raw).hexdigest() == sha_node and len(raw) == int(size)
    try:
        sql = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()      # lit TOUT : la somme CRC32 finale est vérifiée
        facts["gzip_ok"] = True
    except Exception as ex:  # noqa: BLE001
        sql, facts["gzip_ok"] = b"", False
        facts["restore_err"] = f"gzip: {ex}"
    text = sql.decode("utf-8", "replace")
    facts["analysis"] = analyze_dump(text)
    an = facts["analysis"]
    print(f"analyse : pg_dump {an['pg_dump_version']} | {len(an['tables_created'])} tables | {sum(an['copy_rows'].values())} lignes en tout | "
          f"pied de page {'ok' if an['footer_ok'] else 'ABSENT'} | SQL {len(sql)} octets")

    if a.analysis_only:
        why = [w for w in verdict(dict(facts, restore_rc=0, restored_counts={normalise(t): n for t, n in an["copy_rows"].items()} | {normalise(t): 0 for t in an["tables_created"] if normalise(t) not in {normalise(k) for k in an["copy_rows"]}}))]
        print("\n  table                          lignes du dump (COPY)")
        for t, n in sorted({normalise(k): v for k, v in an["copy_rows"].items()}.items()):
            print(f"  {t:<30} {n:>9}")
        print(f"\nRÉSULTAT (analyse seule) : {'ARCHIVE COMPLÈTE ET COHÉRENTE' if not why else 'ÉCHEC'}" + ("" if not why else " — " + " ; ".join(why)))
        print("RESTAURATION NON EXÉCUTÉE : la restaurabilité n'est PAS prouvée par ce mode (relancer sans --analysis-only).")
        return 3 if not why else 1
    if facts["sha_ok"] and facts["gzip_ok"] and an["footer_ok"]:
        d = f"/run/restore-probe-{int(t0)}"
        try:
            print(f"restauration dans un serveur jetable ({d}, port {PORT}, socket Unix seulement) …")
            rc, out, err = sandbox_exec(a.sandbox_pod, (
                f"set -e; D={d}; mkdir -p $D; initdb -D $D/data --auth=trust -U postgres -E UTF8 --no-locale --no-sync >/dev/null; "
                f"pg_ctl -D $D/data -o \"-c listen_addresses='' -c unix_socket_directories=$D -c port={PORT} -c shared_buffers=16MB "
                f"-c max_connections=8 -c fsync=off -c synchronous_commit=off -c full_page_writes=off -c wal_level=minimal "
                f"-c max_wal_senders=0 -c max_wal_size=64MB -c logging_collector=off\" -w -t 60 -l $D/server.log start >/dev/null; "
                f"createdb -h $D -p {PORT} -U postgres restoretest; echo SERVEUR_PRET"))
            if "SERVEUR_PRET" not in out:
                facts["restore_rc"], facts["restore_err"] = 1, (err or out)[-300:]
            else:
                rc, out, err = sandbox_exec(a.sandbox_pod, f"psql -h {d} -p {PORT} -U postgres -d restoretest -X -q -v ON_ERROR_STOP=1 "
                                            f"--single-transaction -f - >/dev/null", timeout=300, stdin=sql)
                facts["restore_rc"], facts["restore_err"] = rc, err.strip()[-300:]
                if rc == 0:
                    q = ("select format('%s|%s', c.relname, (xpath('/row/c/text()', query_to_xml(format('select count(*) as c from %I.%I', "
                         "n.nspname, c.relname), false, true, '')))[1]::text) from pg_class c join pg_namespace n on n.oid=c.relnamespace "
                         "where c.relkind='r' and n.nspname='public' order by c.relname;")
                    rc2, out2, err2 = sandbox_exec(a.sandbox_pod, f"psql -h {d} -p {PORT} -U postgres -d restoretest -X -At -c \"{q}\"")
                    facts["restored_counts"] = parse_counts(out2)
                    facts["count_problems"] = compare(an["copy_rows"], facts["restored_counts"])
                    rc3, out3, _ = sandbox_exec(a.sandbox_pod, f"psql -h {d} -p {PORT} -U postgres -d restoretest -X -At -c "
                                                f"\"select 'index|'||count(*) from pg_indexes where schemaname='public' union all "
                                                f"select 'contraintes|'||count(*) from pg_constraint c join pg_namespace n on n.oid=c.connamespace where n.nspname='public'\"")
                    facts["objects"] = parse_counts(out3)
        finally:
            sandbox_exec(a.sandbox_pod, f"D={d}; case \"$D\" in /run/restore-probe-*) "
                         f"(pg_ctl -D $D/data -m immediate -w -t 30 stop >/dev/null 2>&1 || kill -9 $(head -1 $D/data/postmaster.pid 2>/dev/null) 2>/dev/null); "
                         f"rm -rf \"$D\";; esac; ls /run | grep -c restore-probe || true")
            rc, out, _ = sandbox_exec(a.sandbox_pod, "ls /run | grep -c restore-probe || true")
            facts["nettoyage_ok"] = out.strip() == "0"
    else:
        facts["restore_rc"] = None

    # comparaison INFORMATIVE avec la base vivante (SELECT sur le primaire courant)
    live = {}
    rc, prim, _ = kubectl("get", "pods", "-n", NS, "-l", "cnpg.io/instanceRole=primary", "-o", "jsonpath={.items[0].metadata.name}")
    if facts.get("restored_counts") and prim:
        q = ("select format('%s|%s', c.relname, (xpath('/row/c/text()', query_to_xml(format('select count(*) as c from %I.%I', "
             "n.nspname, c.relname), false, true, '')))[1]::text) from pg_class c join pg_namespace n on n.oid=c.relnamespace "
             "where c.relkind='r' and n.nspname='public' order by c.relname;")
        rc, out, _ = kubectl("exec", "-n", NS, prim, "-c", "postgres", "--", "psql", "-U", "postgres", "-d", "pfe_db", "-At", "-c", q)
        live = parse_counts(out)
    if facts.get("restored_counts"):
        print("\n  table                          dump(COPY)  restaurée  vivante(maintenant)")
        for t, n in sorted(facts["restored_counts"].items()):
            exp = {normalise(k): v for k, v in an["copy_rows"].items()}.get(t, 0)
            print(f"  {t:<30} {exp:>9}  {n:>9}  {live.get(t, '?'):>10}   {'=' if exp == n else '≠ ÉCART'}")
        print(f"  objets restaurés : {facts.get('objects')}")
    why = verdict(facts)
    facts["verdict"] = "RESTAURABLE" if not why else "ÉCHEC"
    facts["durée_s"] = round(time.time() - t0, 1)
    facts["nettoyage"] = facts.get("nettoyage_ok")
    print(f"\nnettoyage du bac à sable : {'ok (aucun dossier restore-probe restant)' if facts.get('nettoyage_ok') else 'NON VÉRIFIÉ / à contrôler'}")
    print(f"RÉSULTAT : {facts['verdict']} en {facts['durée_s']} s" + ("" if not why else " — " + " ; ".join(why)))
    os.makedirs(a.report_dir, exist_ok=True)
    rep = os.path.join(a.report_dir, f"restore-proof-{a.source}-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}.json")
    with open(rep, "w", encoding="utf-8") as fh:
        json.dump({k: v for k, v in facts.items() if k != "analysis"} | {"analyse": {k: v for k, v in an.items() if k != "tables_created"}}, fh,
                  ensure_ascii=False, indent=2, default=str)
    print("rapport :", rep)
    return 0 if not why and facts.get("nettoyage_ok") else 1


if __name__ == "__main__":
    sys.exit(main())
