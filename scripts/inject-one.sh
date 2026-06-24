#!/usr/bin/env bash
# =============================================================================
# HELIOS — Injecter UN SEUL test de chaos a la demande (au lieu du lot complet).
# HELIOS detecte le type DISTINCT puis le resout (IA -> Camunda -> kubectl -> verif).
#
# Usage : ./scripts/inject-one.sh <type>
#         ./scripts/inject-one.sh --list
#
# Pre-requis : kubectl configure sur le contexte du cluster (namespace pfe).
#   - Sur Windows : lancer dans Git Bash (pas PowerShell : heredocs/$() = bash).
#   - Ou en SSH sur le master 192.168.148.153.
#   - Les apps doivent etre SAINES d'abord : python scripts/reset_demo_clean.py
# =============================================================================
set -uo pipefail
NS=pfe
T="${1:-}"

# type -> "ACTION HELIOS attendue"
declare -A FIX=(
  [crashloop]="ROLLBACK_DEPLOYMENT  (kubectl rollout undo)"
  [imagepull]="ROLLBACK_DEPLOYMENT  (kubectl rollout undo)"
  [oom]="ROLLBACK_DEPLOYMENT  (kubectl rollout undo)"
  [cpu]="FORCE_RECREATE_DEPLOYMENT  (kubectl rollout restart)"
  [service]="REMOVE_NETWORK_POLICY  (kubectl delete networkpolicy)"
  [network]="REMOVE_NETWORK_POLICY  (kubectl delete networkpolicy)"
  [dns]="REMOVE_NETWORK_POLICY  (kubectl delete networkpolicy)"
  [disk]="DELETE_CRASHLOOPING_PODS  (kubectl delete pods)"
  [http5xx]="ROLLBACK_DEPLOYMENT  (kubectl rollout undo)"
  [latency]="ROLLBACK_DEPLOYMENT  (kubectl rollout undo)"
  [pending]="ROLLBACK_DEPLOYMENT  (kubectl rollout undo)"
  [init]="ROLLBACK_DEPLOYMENT  (kubectl rollout undo)"
  [config]="ROLLBACK_DEPLOYMENT  (kubectl rollout undo)"
  [unavailable]="ROLLBACK_DEPLOYMENT  (kubectl rollout undo)"
  [db]="REMOVE_NETWORK_POLICY  (kubectl delete networkpolicy)"
  [hpa]="SCALE_DEPLOYMENT  (kubectl scale +1)"
)
# type -> TYPE D'INCIDENT HELIOS
declare -A TYPE=(
  [crashloop]="POD_CRASH_LOOP" [imagepull]="POD_IMAGE_PULL_BACKOFF" [oom]="MEMORY_STRESS"
  [cpu]="CPU_STRESS" [service]="SERVICE_UNAVAILABLE" [network]="NETWORK_PARTITION"
  [dns]="DNS_FAILURE" [disk]="NODE_DISK_PRESSURE" [http5xx]="HIGH_ERROR_RATE"
  [latency]="LATENCY_DEGRADATION" [pending]="POD_PENDING_INSUFFICIENT_RESOURCES"
  [init]="POD_INIT_CONTAINER_FAILURE" [config]="POD_CONFIG_ERROR"
  [unavailable]="DEPLOYMENT_UNAVAILABLE" [db]="DATABASE_CONNECTIVITY" [hpa]="AUTOSCALING"
)

list_types(){
  echo "Types disponibles (16) :"
  for k in crashloop imagepull oom cpu service network dns disk http5xx latency pending init config unavailable db hpa; do
    printf "  %-12s -> %-34s | fix: %s\n" "$k" "${TYPE[$k]}" "${FIX[$k]}"
  done
}

if [ -z "$T" ] || [ "$T" = "-h" ] || [ "$T" = "--help" ]; then
  echo "Usage: $0 <type>"; list_types; exit 1
fi
if [ "$T" = "--list" ] || [ "$T" = "-l" ]; then list_types; exit 0; fi
if [ -z "${TYPE[$T]:-}" ]; then echo "Type inconnu: '$T'"; list_types; exit 1; fi

echo ">> Injection '$T' -> incident attendu ${TYPE[$T]} | resolution HELIOS: ${FIX[$T]}"

# hog CPU 300s (cpu/hpa) : assez long pour la detection ML/cAdvisor (~2-3 min)
HOG="nohup sh -c 'yes >/dev/null 2>&1 & p=\$!; sleep 300; kill \$p' >/dev/null 2>&1 & echo hog"

case "$T" in
  crashloop)
    kubectl -n $NS patch deploy helios-crashloop --type=json \
      -p '[{"op":"add","path":"/spec/template/spec/containers/0/command","value":["sh","-c","echo boot; exit 1"]}]' ;;
  imagepull)
    kubectl -n $NS set image deploy/helios-imagepull app=127.0.0.1:1/nope:helios ;;
  oom)
    kubectl -n $NS patch deploy helios-oom --type=json \
      -p '[{"op":"add","path":"/spec/template/spec/containers/0/command","value":["sh","-c","tail /dev/zero"]}]' ;;
  cpu)
    POD=$(kubectl -n $NS get pod -l app=helios-cpu -o jsonpath='{.items[0].metadata.name}')
    kubectl -n $NS exec "$POD" -- sh -c "$HOG" ;;
  service)
    kubectl -n $NS apply -f - <<'EOF'
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata: {name: helios-demo-service, namespace: pfe}
spec: {podSelector: {matchLabels: {app: helios-service}}, policyTypes: [Ingress]}
EOF
    ;;
  network)
    kubectl -n $NS apply -f - <<'EOF'
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata: {name: helios-demo-network, namespace: pfe}
spec: {podSelector: {matchLabels: {app: helios-network}}, policyTypes: [Ingress, Egress]}
EOF
    ;;
  dns)
    kubectl -n $NS apply -f - <<'EOF'
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata: {name: helios-demo-dns, namespace: pfe}
spec:
  podSelector: {matchLabels: {app: helios-dns}}
  policyTypes: [Egress]
  egress:
    - to: []
      ports: [{protocol: TCP, port: 80}, {protocol: TCP, port: 443}, {protocol: TCP, port: 9898}]
EOF
    ;;
  disk)
    POD=$(kubectl -n $NS get pod -l app=helios-disk -o jsonpath='{.items[0].metadata.name}')
    kubectl -n $NS exec "$POD" -- sh -c "dd if=/dev/zero of=/data/fill bs=1M count=120 2>/dev/null; echo filled" ;;
  http5xx)
    kubectl -n $NS patch deploy helios-http5xx --type=json \
      -p '[{"op":"add","path":"/spec/template/spec/containers/0/command","value":["./podinfo","--random-error=true"]}]' ;;
  latency)
    kubectl -n $NS patch deploy helios-latency --type=json \
      -p '[{"op":"add","path":"/spec/template/spec/containers/0/command","value":["./podinfo","--random-delay=true"]}]' ;;
  pending)
    kubectl -n $NS patch deploy helios-pending --type=json \
      -p '[{"op":"replace","path":"/spec/template/spec/containers/0/resources","value":{"requests":{"cpu":"2","memory":"32Mi"},"limits":{"cpu":"2","memory":"96Mi"}}}]' ;;
  init)
    kubectl -n $NS patch deploy helios-init --type=json \
      -p '[{"op":"replace","path":"/spec/template/spec/initContainers/0/command","value":["sh","-c","echo init; exit 1"]}]' ;;
  config)
    kubectl -n $NS patch deploy helios-config --type=json \
      -p '[{"op":"replace","path":"/spec/template/spec/containers/0/envFrom","value":[{"configMapRef":{"name":"helios-missing-cm"}}]}]' ;;
  unavailable)
    kubectl -n $NS patch deploy helios-unavailable --type=json \
      -p '[{"op":"replace","path":"/spec/template/spec/containers/0/readinessProbe/httpGet/path","value":"/nonexistent-404-helios"}]' ;;
  db)
    kubectl -n $NS apply -f - <<'EOF'
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata: {name: helios-demo-db, namespace: pfe}
spec:
  podSelector: {matchLabels: {app: helios-db}}
  policyTypes: [Egress]
  egress:
    - to: []
      ports: [{protocol: UDP, port: 53}, {protocol: TCP, port: 53}, {protocol: TCP, port: 80}, {protocol: TCP, port: 443}]
EOF
    ;;
  hpa)
    POD=$(kubectl -n $NS get pod -l app=helios-hpa -o jsonpath='{.items[0].metadata.name}')
    kubectl -n $NS exec "$POD" -- sh -c "$HOG" ;;
esac

echo ">> OK. HELIOS detecte au prochain cycle de polling (~30s) puis resout via Camunda."
echo "   Suivre : kubectl -n $NS get incidents 2>/dev/null  (ou l'UI HELIOS)"
