#!/usr/bin/env bash
# Essais ISOLES du script de sauvegarde durci (PostgreSQL jetable requis ; jamais la base reelle).
# Usage : PGHOST=127.0.0.1 PGPORT=55434 PGUSER=postgres PGPASSWORD=... ./test-backup-script.sh <script.sh extrait du CronJob>
set -u; S="${1:?script a tester}"; W=$(mktemp -d); ok=0; ko=0
t(){ if [ "$2" = "$3" ]; then echo "OK  $1"; ok=$((ok+1)); else echo "ECART $1 (attendu $2, obtenu $3)"; ko=$((ko+1)); fi; }
mkdir -p "$W"/{ok,ko1,ko2,ko3,fake}
BK_DIR="$W/ok" bash "$S" >/dev/null 2>&1; t "succes : code 0" 0 $?; t "succes : dump + sha256" 2 "$(ls "$W/ok" | grep -c -E 'sql.gz(.sha256)?$')"; t "succes : .last-success" 1 "$(ls -A "$W/ok" | grep -c '^.last-success$')"
BK_DIR="$W/ko1" PGPORT=1 WAIT_MAX_SECONDS=6 WAIT_STEP_SECONDS=2 bash "$S" >/dev/null 2>&1; t "PostgreSQL injoignable : echec" 1 $?; t "PostgreSQL injoignable : aucun fichier" 0 "$(ls -A "$W/ko1" | wc -l)"
BK_DIR="$W/ko2" PGDB=base_inexistante bash "$S" >/dev/null 2>&1; t "pg_dump en echec : echec" 1 $?; t "pg_dump en echec : aucun fichier (partiel supprime)" 0 "$(ls -A "$W/ko2" | wc -l)"
printf '#!/bin/bash\nfor i in $(seq 1 4000); do echo "INSERT INTO t VALUES ($i, '"'"'remplissage pour depasser la taille minimale du dump'"'"');"; done\n' > "$W/fake/pg_dump"; chmod +x "$W/fake/pg_dump"
PATH="$W/fake:$PATH" BK_DIR="$W/ko3" bash "$S" >/dev/null 2>&1; t "dump tronque (gzip valide, taille plausible) : echec" 1 $?; t "dump tronque : aucun fichier" 0 "$(ls -A "$W/ko3" | wc -l)"
echo "bilan : $ok OK, $ko ecart(s)"; rm -rf "$W"; [ $ko -eq 0 ]