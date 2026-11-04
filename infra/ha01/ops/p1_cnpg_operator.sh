#!/usr/bin/env bash
# HELIOS HA-01 — C7 : durcit le bail leader de l'opérateur CNPG (cause mesurée des 25+ redémarrages/jour sur .153 :
#   "Failed to renew lease ... Get https://10.96.0.1:443/...leases/db9c8771.cnpg.io?timeout=5s: context deadline exceeded"
#   -> "leader election lost" -> exit 1 -> redémarrage -> rafale CPU sur un nœud 2 vCPU -> nouvelles lenteurs API).
# Flags confirmés par `/manager controller --help` (1.28.4) : --leader-lease-duration (défaut 15) / --leader-renew-deadline (défaut 10).
# Valeurs alignées sur celles déjà appliquées au controller-manager/scheduler : 60 / 40.
# Usage : ./p1_cnpg_operator.sh apply | rollback | status
set -euo pipefail
ACTION="${1:-status}"
pick() { for ip in 155 158 153; do if ssh -o BatchMode=yes -o ConnectTimeout=6 kube@192.168.148.$ip 'kubectl get --raw=/readyz --request-timeout=6s' >/dev/null 2>&1; then echo $ip; return; fi; done; return 1; }
VIA=$(pick) || { echo "aucun apiserver joignable"; exit 2; }
K() { local c="kubectl --request-timeout=30s" a; for a in "$@"; do c="$c $(printf '%q' "$a")"; done; ssh -o BatchMode=yes -o ConnectTimeout=8 kube@192.168.148.$VIA "$c"; }
show() { K -n cnpg-system get deploy cnpg-controller-manager -o jsonpath='{.spec.template.spec.containers[0].args}'; echo; K -n cnpg-system get pods --no-headers -o custom-columns=N:.metadata.name,S:.status.phase,R:.status.containerStatuses[0].restartCount,NODE:.spec.nodeName | awk '{print "   "$0}'; }
case "$ACTION" in
  status) show ;;
  apply)
    CUR=$(K -n cnpg-system get deploy cnpg-controller-manager -o jsonpath='{.spec.template.spec.containers[0].args}')
    case "$CUR" in *leader-lease-duration*) echo "déjà appliqué"; show; exit 0;; esac
    # stratégie Recreate pour ne jamais avoir 2 opérateurs (même nodeSelector) pendant la bascule
    K -n cnpg-system patch deploy cnpg-controller-manager --type merge -p '{"spec":{"strategy":{"type":"Recreate","rollingUpdate":null}}}'
    K -n cnpg-system patch deploy cnpg-controller-manager --type json -p '[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--leader-lease-duration=60"},{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--leader-renew-deadline=40"}]'
    K -n cnpg-system rollout status deploy/cnpg-controller-manager --timeout=180s
    show ;;
  rollback)
    K -n cnpg-system patch deploy cnpg-controller-manager --type json -p '[{"op":"remove","path":"/spec/template/spec/containers/0/args/7"},{"op":"remove","path":"/spec/template/spec/containers/0/args/6"}]'
    K -n cnpg-system rollout status deploy/cnpg-controller-manager --timeout=180s
    show ;;
  *) echo "usage: $0 apply|rollback|status"; exit 1 ;;
esac
