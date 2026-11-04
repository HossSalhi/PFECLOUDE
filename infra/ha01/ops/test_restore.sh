#!/usr/bin/env bash
# HELIOS HA-01 — test de RESTAURATION d'un dump SQL gzip (fichier présent sur .161) dans une base jetable.
# Compare le nombre de lignes de chaque table de pfe_db (source) et de la copie restaurée. PASS/FAIL.
# Ne touche pas à pfe_db : crée puis supprime la base `ha01_verify` sur le primaire courant.
# Usage : ./test_restore.sh /opt/helios-backups/ha01/offsite/pg/pfe_db-<ts>.sql.gz
set -uo pipefail
DUMP="${1:?usage: $0 <fichier .sql.gz sur .161>}"
N=(ssh -o BatchMode=yes -o ConnectTimeout=8 kube@192.168.148.155)      # nœud de service (kubectl)
Q=(ssh -o BatchMode=yes -o ConnectTimeout=8 kube@192.168.148.161)
PRIM=$("${N[@]}" "kubectl -n pfe get pods -l cnpg.io/instanceRole=primary -o jsonpath='{.items[0].metadata.name}' --request-timeout=20s")
echo "primaire: $PRIM ; dump: $DUMP"
"${Q[@]}" "gzip -t $DUMP" || { echo "FAIL: gzip -t"; exit 1; }
"${N[@]}" "kubectl -n pfe exec $PRIM -c postgres --request-timeout=60s -- psql -U postgres -d postgres -v ON_ERROR_STOP=1 -q -c 'DROP DATABASE IF EXISTS ha01_verify' -c 'CREATE DATABASE ha01_verify'" || exit 1
"${Q[@]}" "zcat $DUMP" | "${N[@]}" "timeout 180 kubectl -n pfe exec -i $PRIM -c postgres -- psql -U postgres -d ha01_verify -v ON_ERROR_STOP=1 -q -o /dev/null"
RC=$?
COUNTS="select format('select %L as t, count(*) from public.%I', tablename, tablename) from pg_tables where schemaname='public' order by tablename \\gexec"
for DB in pfe_db ha01_verify; do
  echo "$COUNTS" | "${N[@]}" "timeout 60 kubectl -n pfe exec -i $PRIM -c postgres -- psql -U postgres -d $DB -At -F ' ' -f -" | sort > "${TMPDIR:-/tmp}/ha01_counts_$DB.txt"
done
"${N[@]}" "kubectl -n pfe exec $PRIM -c postgres --request-timeout=60s -- psql -U postgres -d postgres -q -c 'DROP DATABASE ha01_verify'" >/dev/null
A="${TMPDIR:-/tmp}/ha01_counts_pfe_db.txt"; B="${TMPDIR:-/tmp}/ha01_counts_ha01_verify.txt"
echo "tables: $(wc -l < "$A") | lignes source: $(awk '{s+=$2} END{print s}' "$A") | restaurées: $(awk '{s+=$2} END{print s}' "$B")"
if [ $RC -eq 0 ] && diff -q "$A" "$B" >/dev/null; then echo "PASS: comptages identiques"; else echo "FAIL (rc psql=$RC) :"; diff "$A" "$B" | head -10; exit 1; fi
