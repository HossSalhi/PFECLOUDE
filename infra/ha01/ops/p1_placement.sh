#!/usr/bin/env bash
# HELIOS HA-01 — C2 : garde-fous de placement sur .153 (k8s-master).
#   - PDB ingress-nginx  : minAvailable 2 -> 1   (avec 2 réplicas, 2 bloquerait toute éviction)
#   - taint .153         : node-role.kubernetes.io/control-plane:NoSchedule  (aucun pod applicatif nouveau ; les pods
#                          existants ne sont PAS évincés ; opérateur CNPG, falco, calico, kube-proxy, speaker, canari le tolèrent)
#   - ingress-nginx      : 3 -> 2 réplicas, la réplique de .153 supprimée EN PREMIER (sinon on ne contrôle pas laquelle part)
# Usage : ./p1_placement.sh apply | rollback | status
# Exécuté depuis le poste d'administration ; kubectl via le worker vivant (.155 puis .158).
set -euo pipefail
ACTION="${1:-status}"
pick() { for ip in 155 158 153; do if ssh -o BatchMode=yes -o ConnectTimeout=6 kube@192.168.148.$ip 'kubectl get --raw=/readyz --request-timeout=6s' >/dev/null 2>&1; then echo $ip; return; fi; done; return 1; }
VIA=$(pick) || { echo "aucun apiserver joignable"; exit 2; }
K() { local c="kubectl --request-timeout=30s" a; for a in "$@"; do c="$c $(printf '%q' "$a")"; done; ssh -o BatchMode=yes -o ConnectTimeout=8 kube@192.168.148.$VIA "$c"; }
MEM153() { ssh -o BatchMode=yes kube@192.168.148.153 "awk '/MemAvailable/{print int(\$2/1024)}' /proc/meminfo"; }
status() {
  echo "-- taints k8s-master:"; K get node k8s-master -o jsonpath='{.spec.taints}'; echo
  echo "-- ingress-nginx:"; K -n ingress-nginx get pods --no-headers -o custom-columns=N:.metadata.name,S:.status.phase,NODE:.spec.nodeName | awk '{print "   "$1, $2, $3}'
  echo "-- PDB:"; K -n ingress-nginx get pdb ingress-nginx-controller --no-headers
  echo "-- replicas deploy:"; K -n ingress-nginx get deploy ingress-nginx-controller -o jsonpath='{.spec.replicas}'; echo
}
case "$ACTION" in
  status) status ;;
  apply)
    M=$(MEM153); echo "MemAvailable .153 = ${M} Mo"; [ "$M" -ge 500 ] || { echo "STOP: .153 < 500 Mo"; exit 3; }
    echo "[1/4] PDB minAvailable 1";  K -n ingress-nginx patch pdb ingress-nginx-controller --type merge -p '{"spec":{"minAvailable":1}}'
    echo "[2/4] taint .153";          K taint nodes k8s-master node-role.kubernetes.io/control-plane:NoSchedule --overwrite
    echo "[3/4] suppression de la réplique ingress de .153 (elle ne peut plus être replanifiée -> Pending)"
    POD=$(K -n ingress-nginx get pods --no-headers -o custom-columns=N:.metadata.name,NODE:.spec.nodeName | awk '$2=="k8s-master"{print $1}')
    [ -n "$POD" ] && K -n ingress-nginx delete pod "$POD" --wait=false || echo "   (aucune réplique sur .153)"
    sleep 8
    echo "[4/4] scale à 2 (le pod Pending est supprimé en premier)"
    K -n ingress-nginx scale deploy ingress-nginx-controller --replicas=2
    sleep 15; status ;;
  rollback)
    K -n ingress-nginx scale deploy ingress-nginx-controller --replicas=3
    K taint nodes k8s-master node-role.kubernetes.io/control-plane:NoSchedule-
    K -n ingress-nginx patch pdb ingress-nginx-controller --type merge -p '{"spec":{"minAvailable":2}}'
    sleep 15; status ;;
  *) echo "usage: $0 apply|rollback|status"; exit 1 ;;
esac
