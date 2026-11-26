#!/usr/bin/env bash
# Essais ISOLES du chien de garde Camunda (outils simules : curl, free, systemctl, logger ; aucune action reelle).
# Usage : ./test-watchdog.sh [chemin de helios-camunda-watchdog.sh]
set -u; S="${1:-$(dirname "$0")/helios-camunda-watchdog.sh}"; T=$(mktemp -d); mkdir -p "$T/bin" "$T/st"; ok=0; ko=0
t(){ if [ "$2" = "$3" ]; then echo "OK    $1"; ok=$((ok+1)); else echo "ECART $1 (attendu $2, obtenu $3)"; ko=$((ko+1)); fi; }
printf '#!/bin/bash\nm=$(cat %s/mode); [ "$m" = healthy ] && echo '"'"'{"brokers":[{"partitions":[{"role":"leader","health":"healthy"}]}]}'"'"'\nexit 0\n' "$T" > "$T/bin/curl"
printf '#!/bin/bash\necho "total used free shared cache available"; echo "Mem: 3868 500 100 1 200 $(cat %s/ram)"\n' "$T" > "$T/bin/free"
printf '#!/bin/bash\nif [ "$1" = is-active ]; then cat %s/unit; else echo "$@" >> %s/calls; fi\n' "$T" "$T" > "$T/bin/systemctl"
printf '#!/bin/bash\nexit 0\n' > "$T/bin/logger"; chmod +x "$T"/bin/*
run(){ PATH="$T/bin:$PATH" STATE_DIR="$T/st" NOW_EPOCH="$1" bash "$S" >/dev/null 2>&1; }; calls(){ [ -f "$T/calls" ] && wc -l < "$T/calls" || echo 0; }
echo healthy > "$T/mode"; echo 3000 > "$T/ram"; echo active > "$T/unit"; : > "$T/calls"
run 1000; t "sain : aucune action" 0 "$(calls)"
echo down > "$T/mode"; run 1060; run 1120; t "2 echecs consecutifs : pas de relance" 0 "$(calls)"
run 1180; t "3e echec : relance" 1 "$(calls)"
run 1240; run 1300; run 1360; t "delai de reprise : pas de seconde relance" 1 "$(calls)"
echo 800 > "$T/ram"; rm -f "$T/st/fails"; run 3000; run 3060; run 3120; t "RAM insuffisante : relance differee" 1 "$(calls)"
echo 3000 > "$T/ram"; run 3180; t "RAM retablie, delai ecoule : relance" 2 "$(calls)"
echo activating > "$T/unit"; rm -f "$T/st/fails"; run 5000; run 5060; run 5120; run 5180; t "unite en cours de demarrage : aucune relance" 2 "$(calls)"
echo active > "$T/unit"; for n in 7000 8000 9000; do rm -f "$T/st/fails"; run $n; run $((n+60)); run $((n+120)); done; t "3 relances espacees dans l'heure : executees" 5 "$(calls)"
rm -f "$T/st/fails"; run 9500; run 9560; run 9620; t "4e relance dans l'heure : BLOQUEE par le plafond (alerte)" 5 "$(calls)"
echo healthy > "$T/mode"; run 20000; t "retour au sain : compteur remis a 0" 0 "$(cat "$T/st/fails")"
echo "bilan : $ok OK, $ko ecart(s)"; rm -rf "$T"; [ $ko -eq 0 ]