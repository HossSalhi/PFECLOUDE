#!/usr/bin/env bash
# =============================================================================
# HELIOS — Migration vers Load Balancing K8s-natif (MetalLB + Ingress LB)
# =============================================================================
# REMPLACE le HAProxy externe (.160) + Keepalived VIP (.150) par :
#   - MetalLB (bare-metal LoadBalancer natif K8s)
#   - ingress-nginx en type LoadBalancer (MetalLB lui attribue une IP)
#
# POURQUOI : Le VIP Keepalived est une notion d'infrastructure EXTERNE qui
# contourne les mecanismes natifs de Kubernetes (Service LoadBalancer, kube-proxy).
# Un cluster K8s professionnel utilise MetalLB pour le bare-metal.
#
# Chaine AVANT (non-K8s) :
#   Client -> VIP .150 -> HAProxy .160/.162 -> NodePort -> ingress -> pods
#
# Chaine APRES (K8s-natif) :
#   Client -> MetalLB IP (.200) -> ingress-nginx pod -> Service -> pods
#
# EXECUTER SUR LE MASTER (.153) en root.
# =============================================================================
set -euo pipefail

echo "================================================================"
echo " HELIOS — Migration vers MetalLB + Ingress LoadBalancer"
echo "================================================================"

# ------------------------------------------------------------------ 1. MetalLB
echo ""
echo "=== 1. Verification / Installation de MetalLB ==="
if kubectl get ns metallb-system &>/dev/null; then
    echo "[OK] Namespace metallb-system existe deja"
else
    echo "[INSTALL] Installation de MetalLB v0.14.8..."
    kubectl apply -f https://raw.githubusercontent.com/metallb/metallb/v0.14.8/config/manifests/metallb-native.yaml
    echo "[WAIT] Attente que MetalLB soit pret..."
    kubectl -n metallb-system wait --for=condition=ready pod -l app=metallb --timeout=120s 2>/dev/null || \
    kubectl -n metallb-system wait --for=condition=ready pod -l component=controller --timeout=120s 2>/dev/null || true
    sleep 10
fi

# ------------------------------------------------------------------ 2. IP Pool
echo ""
echo "=== 2. Configuration du pool d'adresses MetalLB ==="
cat <<'EOF' | kubectl apply -f -
apiVersion: metallb.io/v1beta1
kind: IPAddressPool
metadata:
  name: helios-pool
  namespace: metallb-system
spec:
  addresses:
  - 192.168.148.200-192.168.148.210
---
apiVersion: metallb.io/v1beta1
kind: L2Advertisement
metadata:
  name: helios-l2
  namespace: metallb-system
spec:
  ipAddressPools:
  - helios-pool
EOF
echo "[OK] Pool MetalLB 192.168.148.200-210 configure"

# ------------------------------------------------------------------ 3. Ingress
echo ""
echo "=== 3. Patch ingress-nginx -> type LoadBalancer ==="

# Verifier que ingress-nginx existe
if ! kubectl -n ingress-nginx get svc ingress-nginx-controller &>/dev/null; then
    echo "[INSTALL] Installation de ingress-nginx..."
    kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.10.0/deploy/static/provider/baremetal/deploy.yaml
    sleep 15
fi

# Patcher le service de NodePort vers LoadBalancer
kubectl -n ingress-nginx patch svc ingress-nginx-controller \
    --type='json' \
    -p='[
        {"op": "replace", "path": "/spec/type", "value": "LoadBalancer"},
        {"op": "remove", "path": "/spec/ports/0/nodePort", "value": null},
        {"op": "remove", "path": "/spec/ports/1/nodePort", "value": null}
    ]' 2>/dev/null || \
kubectl -n ingress-nginx patch svc ingress-nginx-controller \
    --type='merge' \
    -p='{"spec": {"type": "LoadBalancer"}}' 2>/dev/null || true

echo "[WAIT] Attente de l'attribution de l'IP MetalLB..."
sleep 5

# Recuperer l'IP attribuee par MetalLB
EXTERNAL_IP=""
for i in $(seq 1 30); do
    EXTERNAL_IP=$(kubectl -n ingress-nginx get svc ingress-nginx-controller -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || true)
    if [ -n "$EXTERNAL_IP" ] && [ "$EXTERNAL_IP" != "<pending>" ]; then
        break
    fi
    echo "  Tentative $i/30 : en attente..."
    sleep 3
done

if [ -n "$EXTERNAL_IP" ] && [ "$EXTERNAL_IP" != "<pending>" ]; then
    echo "[OK] Ingress-nginx a recu l'IP MetalLB : $EXTERNAL_IP"
else
    echo "[WARN] Pas d'IP externe. Verification :"
    kubectl -n ingress-nginx get svc ingress-nginx-controller
    echo ""
    echo "Si MetalLB ne fonctionne pas sur VMware NAT, utiliser le NodePort direct :"
    echo "  kubectl -n ingress-nginx get svc -o wide"
    EXTERNAL_IP="(a verifier)"
fi

# ------------------------------------------------------------------ 4. Promtail
echo ""
echo "=== 4. Deploiement de Promtail DaemonSet ==="
if kubectl -n monitoring get ds promtail &>/dev/null; then
    echo "[OK] Promtail DaemonSet existe deja"
else
    echo "[INSTALL] Application de promtail-daemonset.yaml..."
    if [ -f /root/ha-upgrade/promtail-daemonset.yaml ]; then
        kubectl apply -f /root/ha-upgrade/promtail-daemonset.yaml
    elif [ -f /home/kube/promtail-daemonset.yaml ]; then
        kubectl apply -f /home/kube/promtail-daemonset.yaml
    else
        echo "[WARN] Fichier promtail-daemonset.yaml introuvable."
        echo "  Copier infra/ha-upgrade/promtail-daemonset.yaml sur ce noeud et relancer."
    fi
fi

# ------------------------------------------------------------------ 5. Metrics
echo ""
echo "=== 5. Verification metrics-server (requis pour HPA) ==="
if kubectl -n kube-system get deploy metrics-server &>/dev/null; then
    echo "[OK] metrics-server deploye"
else
    echo "[WARN] metrics-server absent — HPA ne fonctionnera pas"
    echo "  kubectl apply -f infra/k8s/metrics-server.yaml"
fi

# ------------------------------------------------------------------ 6. Verif
echo ""
echo "=== 6. Etat final ==="
echo ""
echo "--- Nodes ---"
kubectl get nodes -o wide
echo ""
echo "--- Ingress-nginx Service ---"
kubectl -n ingress-nginx get svc ingress-nginx-controller
echo ""
echo "--- MetalLB ---"
kubectl -n metallb-system get pods
echo ""
echo "--- Ingress ---"
kubectl -n pfe get ingress 2>/dev/null || echo "(pas d'ingress dans pfe)"
echo ""
echo "--- Promtail ---"
kubectl -n monitoring get ds promtail 2>/dev/null || echo "(promtail non deploye)"
echo ""

echo "================================================================"
echo " RESULTAT"
echo "================================================================"
echo ""
echo "  IP externe ingress-nginx : $EXTERNAL_IP"
echo ""
echo "  Pour acceder a HELIOS depuis le navigateur :"
echo "  1. Ajouter dans C:\\Windows\\System32\\drivers\\etc\\hosts :"
echo "     $EXTERNAL_IP  helios.orange.tn"
echo ""
echo "  2. Ouvrir : http://helios.orange.tn"
echo ""
echo "  Chaine K8s-native :"
echo "  Client -> $EXTERNAL_IP (MetalLB) -> ingress-nginx -> Service -> Pods"
echo ""
echo "  Le HAProxy sur .160 reste UNIQUEMENT pour le control-plane K8s (:6443)."
echo "  Keepalived / VIP .150 NE SONT PLUS NECESSAIRES pour les apps."
echo "================================================================"
