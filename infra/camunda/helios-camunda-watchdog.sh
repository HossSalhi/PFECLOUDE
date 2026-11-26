#!/bin/bash
# HELIOS - chien de garde de Camunda 8 Run (.161). Lance chaque minute par helios-camunda-watchdog.timer.
# Pourquoi : c8run abandonne et ARRETE tout si Camunda n'est pas "sain" apres ~6 min de demarrage (hote sature), puis l'unite oneshot reste
# "active (exited)". Ce chien de garde detecte l'arret et relance de facon prudente et bornee.
# Garde-fous : 3 echecs consecutifs (~3 min) ; unite en cours d'activation = on attend ; RAM disponible >= MIN_FREE_MB sinon on attend (pas de
# relance sur un hote sature) ; delai de reprise 15 min entre deux relances ; au plus 3 relances par heure, puis ALERTE sans relance.
set -u
STATE_DIR="${STATE_DIR:-/var/lib/helios-camunda-watchdog}"; URL="${URL:-http://127.0.0.1:8080/v2/topology}"; UNIT="${UNIT:-camunda8.service}"
FAILS_NEEDED="${FAILS_NEEDED:-3}"; COOLDOWN_S="${COOLDOWN_S:-900}"; MAX_PER_HOUR="${MAX_PER_HOUR:-3}"; MIN_FREE_MB="${MIN_FREE_MB:-1200}"; DRY_RUN="${DRY_RUN:-0}"
NOW="${NOW_EPOCH:-$(date +%s)}"; mkdir -p "$STATE_DIR"; F="$STATE_DIR/fails"; R="$STATE_DIR/restarts"
log(){ logger -t helios-camunda-watchdog -- "$*" 2>/dev/null; echo "$(date -u +%FT%TZ) $*"; }
body=$(curl -s -m 5 "$URL" 2>/dev/null || true)
if printf '%s' "$body" | grep -q '"health":"healthy"' && printf '%s' "$body" | grep -q '"role":"leader"'; then
  [ -s "$F" ] && [ "$(cat "$F")" != "0" ] && log "Camunda de nouveau sain (apres $(cat "$F") echec(s))"; echo 0 > "$F"; exit 0
fi
state=$(systemctl is-active "$UNIT" 2>/dev/null || true)
if [ "$state" = "activating" ] || [ "$state" = "reloading" ]; then log "Camunda non sain mais $UNIT est en cours de demarrage ($state) : attente"; exit 0; fi
n=$(( $(cat "$F" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$F"; log "Camunda non sain ($n/$FAILS_NEEDED), unite=$state"
[ "$n" -lt "$FAILS_NEEDED" ] && exit 0
free_mb=$(free -m 2>/dev/null | awk 'NR==2{print $7}'); free_mb=${free_mb:-0}
if [ "$free_mb" -lt "$MIN_FREE_MB" ]; then log "RELANCE DIFFEREE : RAM disponible ${free_mb} Mo < ${MIN_FREE_MB} Mo (hote sature)"; exit 0; fi
touch "$R"; recent=$(awk -v now="$NOW" '$1>now-3600' "$R" | wc -l); last=$(tail -n 1 "$R" 2>/dev/null); last=${last:-0}
if [ "$recent" -ge "$MAX_PER_HOUR" ]; then log "ALERTE : $recent relances dans l'heure (max $MAX_PER_HOUR) : intervention humaine requise, plus de relance automatique"; exit 0; fi
if [ $(( NOW - last )) -lt "$COOLDOWN_S" ]; then log "RELANCE DIFFEREE : delai de reprise ($COOLDOWN_S s) non ecoule"; exit 0; fi
echo "$NOW" >> "$R"; echo 0 > "$F"
if [ "$DRY_RUN" = "1" ]; then log "DRY-RUN : relancerait $UNIT"; exit 0; fi
log "RELANCE de $UNIT (RAM disponible ${free_mb} Mo)"; systemctl restart "$UNIT"; log "systemctl restart $UNIT : code $?"