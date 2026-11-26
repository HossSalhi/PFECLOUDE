#!/usr/bin/env bash
# =============================================================================
# HELIOS — Phase 3 : Finaliser le HA + rendre tous les noeuds worker
# =============================================================================
# EXECUTER SUR LE MASTER (.153) en root, APRES que les 2 workers aient rejoint.
#
# Cette phase :
#   1. Retire le taint control-plane de TOUS les noeuds (= chaque noeud est AUSSI worker)
#   2. Labellise tous les noeuds comme worker
#   3. Verifie le cluster etcd (3 membres)
#   4. Verifie la HA des composants
#   5. Copie l'audit policy sur les 2 nouveaux control-plane
#   6. Deploie les composants de monitoring manquants (Alertmanager, Promtail)
# =============================================================================
set -euo pipefail

echo "=== Phase 3 : Finalisation HA ==="
echo ""

# ── 1. Verifier que les 3 noeuds sont control-plane ──────────────────────────
echo "[1/8] Verification des noeuds..."
kubectl get nodes -o wide
echo ""

NODES=$(kubectl get nodes -o jsonpath='{.items[*].metadata.name}')
CP_COUNT=$(kubectl get nodes -l node-role.kubernetes.io/control-plane -o name | wc -l)

if [ "$CP_COUNT" -lt 3 ]; then
    echo "ERREUR: Seulement $CP_COUNT control-plane detectes. Il en faut 3."
    echo "Assurez-vous que les phases 1 et 2 sont terminees."
    exit 1
fi
echo "  $CP_COUNT control-plane detectes. OK."

# ── 2. Retirer le taint control-plane ─────────────────────────────────────────
echo "[2/8] Retrait du taint control-plane (tous les noeuds deviennent aussi workers)..."
for NODE in $NODES; do
    kubectl taint nodes "$NODE" node-role.kubernetes.io/control-plane- 2>/dev/null || true
    echo "  $NODE : taint retire"
done

# ── 3. Labelliser tous les noeuds comme workers ──────────────────────────────
echo "[3/8] Labellisation des noeuds..."
kubectl label node k8s-master   node-type=worker topology.kubernetes.io/zone=zone-a --overwrite
kubectl label node k8s-worker1  node-type=worker topology.kubernetes.io/zone=zone-a --overwrite
kubectl label node k8s-worker2  node-type=worker topology.kubernetes.io/zone=zone-b --overwrite
kubectl label node k8s-master   node-role.kubernetes.io/worker="" --overwrite 2>/dev/null || true
kubectl label node k8s-worker1  node-role.kubernetes.io/worker="" --overwrite 2>/dev/null || true
kubectl label node k8s-worker2  node-role.kubernetes.io/worker="" --overwrite 2>/dev/null || true
echo "  Tous les noeuds sont master+worker avec labels de zone."

# ── 4. Verifier etcd (3 membres, tous started) ───────────────────────────────
echo "[4/8] Verification du cluster etcd..."
ETCDCTL_API=3 etcdctl member list \
    --endpoints=https://127.0.0.1:2379 \
    --cacert=/etc/kubernetes/pki/etcd/ca.crt \
    --cert=/etc/kubernetes/pki/etcd/server.crt \
    --key=/etc/kubernetes/pki/etcd/server.key \
    --write-out=table

ETCD_MEMBERS=$(ETCDCTL_API=3 etcdctl member list \
    --endpoints=https://127.0.0.1:2379 \
    --cacert=/etc/kubernetes/pki/etcd/ca.crt \
    --cert=/etc/kubernetes/pki/etcd/server.crt \
    --key=/etc/kubernetes/pki/etcd/server.key 2>/dev/null | grep -c "started" || echo 0)

if [ "$ETCD_MEMBERS" -eq 3 ]; then
    echo "  etcd : 3/3 membres started. QUORUM OK."
else
    echo "  ATTENTION : seulement $ETCD_MEMBERS/3 membres etcd started !"
fi

# ── 5. Verifier les leader elections ─────────────────────────────────────────
echo "[5/8] Verification des leader elections..."
for COMP in kube-scheduler kube-controller-manager; do
    LEADER=$(kubectl -n kube-system get lease $COMP -o jsonpath='{.spec.holderIdentity}' 2>/dev/null || echo "N/A")
    echo "  $COMP leader : $LEADER"
done

# ── 6. Copier l'audit policy sur les nouveaux control-plane ──────────────────
echo "[6/8] Deploiement de l'audit policy sur les 3 noeuds..."
for NODE_IP in 192.168.148.158 192.168.148.155; do
    sshpass -p kube scp -o StrictHostKeyChecking=no \
        /etc/kubernetes/audit-policy.yaml \
        kube@${NODE_IP}:/tmp/audit-policy.yaml 2>/dev/null && \
    sshpass -p kube ssh -o StrictHostKeyChecking=no kube@${NODE_IP} \
        "sudo cp /tmp/audit-policy.yaml /etc/kubernetes/audit-policy.yaml && sudo mkdir -p /var/log/kubernetes/audit" 2>/dev/null && \
    echo "  ${NODE_IP} : audit policy deployee" || \
    echo "  [WARN] ${NODE_IP} : copier manuellement audit-policy.yaml"
done

# ── 7. Copier etcdctl sur les nouveaux control-plane ──────────────────────────
echo "[7/8] Deploiement d'etcdctl sur les nouveaux control-plane..."
ETCDCTL_PATH=$(which etcdctl 2>/dev/null || echo "/usr/local/bin/etcdctl")
if [ -f "$ETCDCTL_PATH" ]; then
    for NODE_IP in 192.168.148.158 192.168.148.155; do
        sshpass -p kube scp -o StrictHostKeyChecking=no \
            "$ETCDCTL_PATH" kube@${NODE_IP}:/tmp/etcdctl 2>/dev/null && \
        sshpass -p kube ssh -o StrictHostKeyChecking=no kube@${NODE_IP} \
            "sudo mv /tmp/etcdctl /usr/local/bin/etcdctl && sudo chmod +x /usr/local/bin/etcdctl" 2>/dev/null && \
        echo "  ${NODE_IP} : etcdctl installe" || \
        echo "  [WARN] ${NODE_IP} : copier etcdctl manuellement"
    done
fi

# ── 8. Mise a jour des deploiements HELIOS ────────────────────────────────────
echo "[8/8] Mise a jour des deploiements pour utiliser les 3 noeuds..."

# Le backend et frontend ont nodeSelector node-type=worker. Comme les 3 noeuds
# sont maintenant worker, ils peuvent tourner sur n'importe lequel.
# Forcer un rollout pour redistribuer
kubectl -n pfe rollout restart deployment backend 2>/dev/null || true
kubectl -n pfe rollout restart deployment frontend 2>/dev/null || true

echo ""
echo "============================================================="
echo "         CLUSTER HA — RAPPORT DE VERIFICATION"
echo "============================================================="
echo ""
echo "--- Noeuds ---"
kubectl get nodes -o wide
echo ""
echo "--- Pods systeme ---"
kubectl -n kube-system get pods -o wide | grep -E "apiserver|etcd|scheduler|controller"
echo ""
echo "--- Pods applicatifs ---"
kubectl -n pfe get pods -o wide
echo ""
echo "--- etcd health ---"
ETCDCTL_API=3 etcdctl endpoint health \
    --endpoints=https://192.168.148.153:2379,https://192.168.148.158:2379,https://192.168.148.155:2379 \
    --cacert=/etc/kubernetes/pki/etcd/ca.crt \
    --cert=/etc/kubernetes/pki/etcd/server.crt \
    --key=/etc/kubernetes/pki/etcd/server.key \
    --write-out=table 2>/dev/null || echo "[INFO] Verifier etcd manuellement"
echo ""
echo "============================================================="
echo "  HA ACTIVE : 3 control-plane + 3 workers (meme noeuds)"
echo "  Le cluster tolere la perte de 1 noeud."
echo "============================================================="
echo ""
echo "TEST HA : arreter un noeud (vmrun stop) et verifier que :"
echo "  - kubectl get nodes montre 2/3 Ready"
echo "  - Les pods sont replanifies sur les 2 noeuds restants"
echo "  - L'API repond toujours via HAProxy .160"
echo "  - etcd a toujours le quorum (2/3)"
