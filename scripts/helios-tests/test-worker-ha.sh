#!/usr/bin/env bash
# =============================================================================
# HELIOS — Test HA réel : arrêt worker-1 puis worker-2 (depuis VMware)
# =============================================================================
# Ce script NE peut PAS éteindre les VMs lui-même (c'est à toi de le faire dans
# VMware Workstation). Il PILOTE le test et VÉRIFIE automatiquement la HA via
# kubectl + l'API backend, à chaque étape, avec des résultats horodatés.
#
# À lancer depuis une machine qui a `kubectl` configuré sur le cluster
# (master 192.168.148.158 ou ton poste avec le kubeconfig).
#
# ⚠️ RÈGLES DE SÉCURITÉ (mémoire projet — catastrophe etcd du 6 juin) :
#   - UN SEUL worker éteint à la fois. JAMAIS les deux ensemble.
#   - Toujours rallumer + attendre Ready avant de passer au worker suivant.
#   - Ne JAMAIS re-gonfler la RAM de .161 (rester à 8GB).
#
# Usage:
#   ./test-worker-ha.sh
#   BACKEND_URL=http://192.168.148.153:30880 ./test-worker-ha.sh
# =============================================================================
set -uo pipefail

NS="${NS:-pfe}"
BACKEND_URL="${BACKEND_URL:-http://192.168.148.153:30880}"
WORKER1="${WORKER1:-worker-1}"
WORKER2="${WORKER2:-worker-2}"
READY_TIMEOUT="${READY_TIMEOUT:-300}"   # s à attendre pour NotReady/Ready
POLL="${POLL:-10}"

ts()   { date '+%Y-%m-%d %H:%M:%S'; }
log()  { echo "[$(ts)] $*"; }
hr()   { echo "------------------------------------------------------------------"; }
pause(){ read -rp ">>> $* — appuie sur [Entrée] quand c'est fait... " _; }

require() { command -v "$1" >/dev/null 2>&1 || { echo "ERREUR: '$1' introuvable"; exit 1; }; }
require kubectl
require curl

# --- état du cluster ---------------------------------------------------------
node_status() { kubectl get node "$1" -o jsonpath='{range .status.conditions[?(@.type=="Ready")]}{.status}{end}' 2>/dev/null; }
backend_ok()  { curl -fsS --max-time 5 "${BACKEND_URL}/actuator/health" >/dev/null 2>&1 && echo OK || echo DOWN; }
running_pods(){ kubectl get pods -n "$NS" --field-selector=status.phase=Running --no-headers 2>/dev/null | wc -l | tr -d ' '; }

wait_for_node() {  # $1=node  $2=expected(True|False|absent)
  local node="$1" want="$2" waited=0
  while (( waited < READY_TIMEOUT )); do
    local s; s="$(node_status "$node")"
    [[ -z "$s" ]] && s="absent"
    if [[ "$s" == "$want" || ( "$want" == "False" && "$s" != "True" ) ]]; then
      log "  node $node = $s (attendu $want) ✔"; return 0
    fi
    sleep "$POLL"; waited=$((waited+POLL))
    log "  ... node $node = ${s:-?} (attente ${waited}s/${READY_TIMEOUT}s)"
  done
  log "  ⚠ timeout: node $node n'a pas atteint l'état '$want' en ${READY_TIMEOUT}s"; return 1
}

check_helios_incident() {  # cherche un incident NODE_NOT_READY récent côté backend
  local body; body="$(curl -fsS --max-time 8 "${BACKEND_URL}/api/incidents?size=20" 2>/dev/null || true)"
  if [[ -z "$body" ]]; then log "  (API incidents injoignable — vérifie manuellement dans l'UI)"; return; fi
  if grep -qiE 'NODE_NOT_READY|node.*outage|MULTI_SIGNAL' <<<"$body"; then
    log "  ✔ HELIOS a créé un incident d'indisponibilité de nœud (détection autonome OK)"
  else
    log "  ⏳ pas encore d'incident NODE_NOT_READY visible (le monitor tourne toutes les ~120s — re-vérifie)"
  fi
}

baseline() {
  hr; log "BASELINE (avant test)"; hr
  kubectl get nodes -o wide
  log "Pods Running dans '$NS' : $(running_pods)"
  log "Backend ($BACKEND_URL) : $(backend_ok)"
}

test_one_worker() {  # $1 = nom du worker
  local w="$1"
  hr; log "TEST HA — arrêt de $w"; hr
  log "Pods actuellement sur $w :"
  kubectl get pods -n "$NS" -o wide --no-headers 2>/dev/null | awk -v n="$w" '$7==n{print "   "$1" ("$7")"}'

  pause "Dans VMware : POWER OFF de la VM '$w' (Power > Power Off)"
  log "Vérification : $w doit passer NotReady..."
  wait_for_node "$w" "False"

  log "Vérification HA pendant que $w est DOWN :"
  log "  - Backend reste joignable ? -> $(backend_ok)"
  log "  - Pods Running (doivent être reschedulés ailleurs) : $(running_pods)"
  kubectl get pods -n "$NS" -o wide 2>/dev/null | grep -vE 'Running|Completed' || log "  (tous les pods non-terminés sont Running ✔)"
  check_helios_incident

  pause "Dans VMware : POWER ON de la VM '$w' et attends qu'elle boote"
  log "Vérification : $w doit revenir Ready..."
  wait_for_node "$w" "True"
  log "  Backend après recovery : $(backend_ok)"
  log "✅ Cycle $w terminé."
}

main() {
  log "=== HELIOS — Test HA workers (NS=$NS, backend=$BACKEND_URL) ==="
  log "Rappel sécurité : UN worker à la fois, jamais les deux."
  baseline
  test_one_worker "$WORKER1"
  hr; log "Pause de stabilisation avant le worker suivant (60s)"; hr
  sleep 60
  log "Re-vérif baseline avant worker 2 :"; baseline
  test_one_worker "$WORKER2"
  hr; log "BILAN FINAL"; hr
  kubectl get nodes -o wide
  log "Pods Running : $(running_pods) | Backend : $(backend_ok)"
  log "✅ Test HA terminé. Si le backend est resté OK aux deux arrêts -> HA validée."
  log "   (PDB: backend-pdb/frontend-pdb/postgres-pdb + HPA backend assurent le reschedule.)"
}
main "$@"
