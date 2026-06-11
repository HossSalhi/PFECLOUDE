#!/usr/bin/env bash
# =============================================================================
# HELIOS — Test HA RÉEL : arrêt physique worker1 puis worker2 (vmrun + kubectl)
# =============================================================================
# Pilote VMware Workstation via vmrun pour ÉTEINDRE réellement (hard power-off)
# un worker à la fois, et vérifie automatiquement :
#   - le node passe NotReady,
#   - le service reste joignable (HA via la réplica survivante),
#   - le failover CNPG (postgres primary bascule),
#   - la DÉTECTION par l'IA HELIOS (incidents_triggered s'incrémente),
#   - la récupération complète après rallumage.
#
# ✅ Validé en réel le 2026-06-11 (voir BILAN-HELIOS-2026-06-11.md).
#
# ⚠️ SÉCURITÉ : UN worker à la fois, JAMAIS les deux. Rallumage + Ready avant
#    le worker suivant. Ne jamais re-gonfler la RAM de .161.
#
# Lancer depuis l'hôte Windows (Git-Bash) avec kubectl configuré.
# =============================================================================
set -uo pipefail

VMRUN="${VMRUN:-/c/Program Files (x86)/VMware/VMware Workstation/vmrun.exe}"
VM_DIR="${VM_DIR:-C:\\Users\\houss\\Documents\\Virtual Machines}"
NS="${NS:-pfe}"
AI_STATUS_URL="${AI_STATUS_URL:-http://192.168.148.161:8000/monitor/status}"

# node-name : vmx-name : worker-IP
WORKERS=(
  "k8s-worker1:k8s-worker1:192.168.148.158"
  "k8s-worker2:k8s-worker2:192.168.148.155"
)

ts(){ date '+%H:%M:%S'; }
log(){ echo "[$(ts)] $*"; }
hr(){ echo "------------------------------------------------------------------"; }

node_state(){ kubectl get node "$1" --no-headers 2>/dev/null | awk '{print $2}'; }
avail(){ kubectl get deploy "$1" -n "$NS" -o jsonpath='{.status.availableReplicas}' 2>/dev/null; }
primary(){ kubectl get cluster postgres -n "$NS" -o jsonpath='{.status.currentPrimary}' 2>/dev/null; }
http_code(){ curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://$1:30880/actuator/health" 2>/dev/null; }
helios_incidents(){ curl -fsS --max-time 6 "$AI_STATUS_URL" 2>/dev/null | grep -o '"incidents_triggered":[0-9]*' | grep -o '[0-9]*'; }

wait_state(){ # $1=node $2=Ready|NotReady $3=maxloops
  local n=0
  if [ "$2" = "Ready" ]; then
    until [ "$(node_state "$1")" = "Ready" ] || [ $n -ge "$3" ]; do sleep 10; n=$((n+1)); done
  else
    until [ "$(node_state "$1")" != "Ready" ] && [ -n "$(node_state "$1")" ] || [ $n -ge "$3" ]; do sleep 5; n=$((n+1)); done
  fi
}

baseline(){
  hr; log "BASELINE"; hr
  kubectl get nodes --no-headers | awk '{print "  "$1" "$2}'
  log "backend=$(avail backend)/2  frontend=$(avail frontend)/2  primary=$(primary)"
}

test_worker(){ # $1=node $2=vmx $3=ip
  local node="$1" vmx="$VM_DIR\\$2\\$2.vmx" ip="$3"
  hr; log "TEST HA — arrêt de $node ($ip)"; hr
  local inc_before; inc_before=$(helios_incidents)
  log "incidents HELIOS avant: ${inc_before:-?}"

  log "POWER OFF $node (hard)..."
  "$VMRUN" stop "$vmx" hard 2>&1 || log "vmrun stop rc=$?"
  wait_state "$node" NotReady 30
  log "$node = $(node_state "$node")"

  log "--- Vérif HA pendant $node DOWN ---"
  log "  backend=$(avail backend)/2  frontend=$(avail frontend)/2 (doit rester >=1)"
  log "  service via worker survivant -> HTTP $(for w in "${WORKERS[@]}"; do oip="${w##*:}"; [ "$oip" != "$ip" ] && http_code "$oip"; done | head -1) (200/403 = vivant)"
  # Failover CNPG si le primary était sur ce node
  local n=0
  until [ "$(primary)" != "" ] || [ $n -ge 6 ]; do sleep 5; n=$((n+1)); done
  log "  CNPG primary = $(primary) ($(kubectl get cluster postgres -n "$NS" --no-headers 2>/dev/null | awk '{print $4,$5}'))"

  log "--- Détection IA HELIOS (max ~150s, intervalle monitor 120s) ---"
  n=0
  until [ "$(helios_incidents)" -gt "${inc_before:-0}" ] 2>/dev/null || [ $n -ge 9 ]; do sleep 20; n=$((n+1)); done
  local inc_after; inc_after=$(helios_incidents)
  if [ "${inc_after:-0}" -gt "${inc_before:-0}" ]; then
    log "  ✅ HELIOS a DÉTECTÉ (incidents ${inc_before}->${inc_after}) — détection par l'IA, pas Alertmanager"
  else
    log "  ⏳ pas encore détecté (cooldown node actif ou cycle non écoulé) — vérifie l'UI"
  fi

  log "POWER ON $node..."
  "$VMRUN" start "$vmx" nogui 2>&1 || log "vmrun start rc=$?"
  wait_state "$node" Ready 24
  log "$node = $(node_state "$node")"
  n=0; until [ "$(avail backend)" = "2" ] || [ $n -ge 30 ]; do sleep 5; n=$((n+1)); done
  log "Recovery: backend=$(avail backend)/2 frontend=$(avail frontend)/2 primary=$(primary)"
  log "✅ Cycle $node terminé."
}

main(){
  log "=== HELIOS — Test HA workers (NS=$NS) ==="
  baseline
  for w in "${WORKERS[@]}"; do
    IFS=':' read -r node vmx ip <<<"$w"
    test_worker "$node" "$vmx" "$ip"
  done
  hr; log "BILAN FINAL"; hr
  kubectl get nodes --no-headers | awk '{print "  "$1" "$2}'
  log "backend=$(avail backend)/2 frontend=$(avail frontend)/2 primary=$(primary)"
  log "HELIOS: $(curl -fsS --max-time 6 "$AI_STATUS_URL" 2>/dev/null | grep -o '"running":[a-z]*')"
  log "✅ Si le service est resté joignable aux deux arrêts -> HA validée."
}
main "$@"
