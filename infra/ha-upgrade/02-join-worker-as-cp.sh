#!/usr/bin/env bash
# =============================================================================
# HELIOS — Phase 2 : Joindre un worker comme control-plane
# =============================================================================
# EXECUTER SUR CHAQUE WORKER (.158 puis .155) en root.
# IMPORTANT : Faire UN SEUL worker a la fois ! Attendre que le premier soit
# Ready + etcd membre avant de faire le deuxieme.
#
# Pre-requis :
#   - Phase 1 terminee sur le master
#   - HAProxy .160 fonctionnel (forward :6443 vers le master)
#   - /tmp/ha-join-commands.sh copie depuis le master (ou parametres ci-dessous)
# =============================================================================
set -euo pipefail

HAPROXY_IP="192.168.148.160"
MASTER_IP="192.168.148.153"
THIS_NODE_IP=$(hostname -I | awk '{print $1}')
THIS_NODE_NAME=$(hostname)

echo "=== Phase 2 : Join control-plane pour ${THIS_NODE_NAME} (${THIS_NODE_IP}) ==="
echo ""

# ── Parametres (modifier si les valeurs generees ont expire) ──────────────────
# Ces valeurs viennent de la Phase 1. Si le token a expire (24h), regenerer sur le master :
#   kubeadm token create --print-join-command
#   kubeadm init phase upload-certs --upload-certs
JOIN_TOKEN="${JOIN_TOKEN:-}"
CERT_KEY="${CERT_KEY:-}"
DISCOVERY_HASH="${DISCOVERY_HASH:-}"

if [ -z "$JOIN_TOKEN" ]; then
    echo "ERREUR: Definir JOIN_TOKEN, CERT_KEY et DISCOVERY_HASH"
    echo ""
    echo "Exemple :"
    echo "  export JOIN_TOKEN='abcdef.1234567890abcdef'"
    echo "  export CERT_KEY='abc123...'"
    echo "  export DISCOVERY_HASH='sha256:abc123...'"
    echo "  ./02-join-worker-as-cp.sh"
    echo ""
    echo "OU copier /tmp/ha-join-commands.sh depuis le master et executer directement."
    exit 1
fi

# ── 1. Drain ce noeud depuis le master ────────────────────────────────────────
echo "[1/6] Drain du noeud ${THIS_NODE_NAME}..."
echo "  (Les pods sont evacues vers l'autre worker)"
sshpass -p kube ssh -o StrictHostKeyChecking=no kube@${MASTER_IP} \
    "kubectl drain ${THIS_NODE_NAME} --ignore-daemonsets --delete-emptydir-data --force --timeout=120s" 2>/dev/null || \
    echo "  [WARN] Drain partiel — certains pods peuvent etre bloques"

# ── 2. Sauvegarder la config kubelet ──────────────────────────────────────────
echo "[2/6] Sauvegarde de la configuration..."
cp /etc/kubernetes/kubelet.conf /etc/kubernetes/kubelet.conf.bak-ha 2>/dev/null || true
cp /etc/kubernetes/pki/ca.crt /tmp/ca.crt.bak 2>/dev/null || true

# ── 3. Reset kubeadm ─────────────────────────────────────────────────────────
echo "[3/6] Reset kubeadm..."
kubeadm reset -f --cleanup-tmp-dir 2>/dev/null
rm -rf /etc/kubernetes/manifests /etc/kubernetes/pki
# Nettoyer etcd local (s'il existe)
rm -rf /var/lib/etcd/member 2>/dev/null || true

# ── 4. Creer le repertoire d'audit ────────────────────────────────────────────
echo "[4/6] Preparation des repertoires..."
mkdir -p /var/log/kubernetes/audit

# Copier l'audit policy depuis le master
sshpass -p kube scp -o StrictHostKeyChecking=no \
    kube@${MASTER_IP}:/etc/kubernetes/audit-policy.yaml \
    /etc/kubernetes/audit-policy.yaml 2>/dev/null || \
    echo "  [INFO] Copier manuellement audit-policy.yaml"

# ── 5. Join comme control-plane ──────────────────────────────────────────────
echo "[5/6] Join comme control-plane..."
echo "  Token: ${JOIN_TOKEN}"
echo "  CertKey: ${CERT_KEY:0:20}..."
echo ""

kubeadm join ${HAPROXY_IP}:6443 \
    --token "${JOIN_TOKEN}" \
    --discovery-token-ca-cert-hash "${DISCOVERY_HASH}" \
    --control-plane \
    --certificate-key "${CERT_KEY}" \
    --apiserver-advertise-address "${THIS_NODE_IP}"

echo "  Join reussi !"

# ── 6. Post-join : configurer kubectl local + mettre a jour kubelet ──────────
echo "[6/6] Post-join..."

# Configurer kubectl local
mkdir -p /root/.kube
cp /etc/kubernetes/admin.conf /root/.kube/config
chmod 600 /root/.kube/config

# S'assurer que le kubelet utilise HAProxy (pas le master direct)
if grep -q "${MASTER_IP}" /etc/kubernetes/kubelet.conf 2>/dev/null; then
    sed -i "s|${MASTER_IP}|${HAPROXY_IP}|g" /etc/kubernetes/kubelet.conf
    systemctl restart kubelet
fi

# Verifier
echo ""
echo "--- Verification ---"
sleep 15
kubectl get nodes -o wide 2>/dev/null || echo "  [INFO] L'API peut mettre quelques secondes a repondre"
echo ""
echo "--- Membres etcd ---"
ETCDCTL_API=3 etcdctl member list \
    --endpoints=https://127.0.0.1:2379 \
    --cacert=/etc/kubernetes/pki/etcd/ca.crt \
    --cert=/etc/kubernetes/pki/etcd/server.crt \
    --key=/etc/kubernetes/pki/etcd/server.key 2>/dev/null || \
    echo "  [INFO] etcd peut mettre quelques secondes a synchroniser"

echo ""
echo "=== Phase 2 terminee pour ${THIS_NODE_NAME} ==="
echo ""
echo "PROCHAINES ETAPES :"
echo "  1. Sur le master : kubectl get nodes  (verifier que ce noeud est Ready + control-plane)"
echo "  2. Sur le master : kubectl taint nodes ${THIS_NODE_NAME} node-role.kubernetes.io/control-plane- "
echo "  3. Si c'etait le premier worker, repeter cette phase sur le deuxieme worker"
echo "  4. Quand les 2 workers sont joins, executer 03-finalize-ha.sh sur le master"
