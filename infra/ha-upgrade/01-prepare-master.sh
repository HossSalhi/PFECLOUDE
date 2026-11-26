#!/usr/bin/env bash
# =============================================================================
# HELIOS — Phase 1 : Preparer le master pour le HA
# =============================================================================
# EXECUTER SUR LE MASTER (.153) en root.
#
# Etapes :
#   1. Snapshot etcd (securite)
#   2. Mettre a jour le controlPlaneEndpoint dans kubeadm-config
#   3. Regenerer les certificats API Server avec les nouveaux SANs
#   4. Deployer l'audit policy
#   5. Upload des certificats pour le join
#   6. Generer la commande de join
# =============================================================================
set -euo pipefail

HAPROXY_IP="192.168.148.160"
MASTER_IP="192.168.148.153"
WORKER1_IP="192.168.148.158"
WORKER2_IP="192.168.148.155"
VIP="192.168.148.150"

echo "=== Phase 1 : Preparation du master pour HA ==="
echo ""

# ── 1. Snapshot etcd ──────────────────────────────────────────────────────────
echo "[1/7] Snapshot etcd de securite..."
ETCDCTL_API=3 etcdctl snapshot save /tmp/etcd-pre-ha-$(date +%Y%m%d%H%M).db \
    --endpoints=https://127.0.0.1:2379 \
    --cacert=/etc/kubernetes/pki/etcd/ca.crt \
    --cert=/etc/kubernetes/pki/etcd/server.crt \
    --key=/etc/kubernetes/pki/etcd/server.key
echo "  Snapshot sauvegarde."

# ── 2. Mettre a jour le ConfigMap kubeadm-config ──────────────────────────────
echo "[2/7] Mise a jour du ConfigMap kubeadm-config..."
kubectl -n kube-system get cm kubeadm-config -o yaml > /tmp/kubeadm-config-backup.yaml

# Remplacer le controlPlaneEndpoint
kubectl -n kube-system get cm kubeadm-config -o json | \
    python3 -c "
import json, sys, yaml
cm = json.load(sys.stdin)
cc = yaml.safe_load(cm['data']['ClusterConfiguration'])
cc['controlPlaneEndpoint'] = '${HAPROXY_IP}:6443'
# Ajouter les certSANs
cc.setdefault('apiServer', {})['certSANs'] = [
    '${MASTER_IP}', '${WORKER1_IP}', '${WORKER2_IP}',
    '${HAPROXY_IP}', '${VIP}',
    'k8s-master', 'k8s-worker1', 'k8s-worker2',
    'helios.orange.tn', '127.0.0.1', 'localhost'
]
cm['data']['ClusterConfiguration'] = yaml.dump(cc, default_flow_style=False)
json.dump(cm, sys.stdout)
" | kubectl apply -f -
echo "  controlPlaneEndpoint = ${HAPROXY_IP}:6443"

# ── 3. Regenerer les certificats API Server ───────────────────────────────────
echo "[3/7] Regeneration des certificats API Server..."
# Backup
cp -r /etc/kubernetes/pki /etc/kubernetes/pki.bak-ha-$(date +%Y%m%d)

# Supprimer les anciens certs API (kubeadm les regenere)
rm -f /etc/kubernetes/pki/apiserver.crt /etc/kubernetes/pki/apiserver.key

# Regenerer avec les nouveaux SANs
kubeadm init phase certs apiserver \
    --apiserver-cert-extra-sans="${MASTER_IP},${WORKER1_IP},${WORKER2_IP},${HAPROXY_IP},${VIP},k8s-master,k8s-worker1,k8s-worker2,helios.orange.tn"
echo "  Certificats regeneres avec SANs : ${MASTER_IP}, ${WORKER1_IP}, ${WORKER2_IP}, ${HAPROXY_IP}, ${VIP}"

# Verifier les SANs
echo "  Verification des SANs :"
openssl x509 -in /etc/kubernetes/pki/apiserver.crt -noout -text | grep -A1 "Subject Alternative Name"

# Redemarrer l'API Server pour charger les nouveaux certs
echo "  Redemarrage de l'API Server..."
mv /etc/kubernetes/manifests/kube-apiserver.yaml /tmp/
sleep 10
mv /tmp/kube-apiserver.yaml /etc/kubernetes/manifests/
sleep 30

# Attendre que l'API reponde
echo "  Attente de l'API Server..."
for i in $(seq 1 30); do
    kubectl get nodes &>/dev/null && break
    sleep 5
done
kubectl get nodes || { echo "ERREUR: API Server ne repond pas !"; exit 1; }
echo "  API Server OK."

# ── 4. Deployer l'audit policy ────────────────────────────────────────────────
echo "[4/7] Deploiement de l'audit policy..."
mkdir -p /var/log/kubernetes/audit
cp /root/infra/ha-upgrade/audit-policy.yaml /etc/kubernetes/audit-policy.yaml 2>/dev/null || \
    echo "  [INFO] Copier manuellement audit-policy.yaml vers /etc/kubernetes/audit-policy.yaml"

# ── 5. Mettre a jour les kubeconfigs pour utiliser HAProxy ────────────────────
echo "[5/7] Mise a jour des kubeconfigs vers HAProxy (${HAPROXY_IP}:6443)..."
for CONF in /etc/kubernetes/admin.conf \
            /etc/kubernetes/controller-manager.conf \
            /etc/kubernetes/scheduler.conf; do
    if [ -f "$CONF" ]; then
        sed -i "s|server: https://${MASTER_IP}:6443|server: https://${HAPROXY_IP}:6443|g" "$CONF"
        echo "  $CONF -> ${HAPROXY_IP}:6443"
    fi
done

# kubelet.conf
if [ -f /etc/kubernetes/kubelet.conf ]; then
    sed -i "s|server: https://${MASTER_IP}:6443|server: https://${HAPROXY_IP}:6443|g" /etc/kubernetes/kubelet.conf
    systemctl restart kubelet
    echo "  kubelet.conf -> ${HAPROXY_IP}:6443 (kubelet redémarre)"
fi

# ~/.kube/config
if [ -f ~/.kube/config ]; then
    sed -i "s|server: https://${MASTER_IP}:6443|server: https://${HAPROXY_IP}:6443|g" ~/.kube/config
fi

# kube-proxy ConfigMap
kubectl -n kube-system get cm kube-proxy -o yaml | \
    sed "s|server: https://${MASTER_IP}:6443|server: https://${HAPROXY_IP}:6443|g" | \
    kubectl apply -f - 2>/dev/null || true
# Redemarrer kube-proxy
kubectl -n kube-system rollout restart ds kube-proxy 2>/dev/null || true

# ── 6. Upload des certificats pour le join control-plane ──────────────────────
echo "[6/7] Upload des certificats pour le join..."
CERT_KEY=$(kubeadm init phase upload-certs --upload-certs 2>/dev/null | tail -1)
echo "  Certificate key : $CERT_KEY"

# ── 7. Generer la commande de join ────────────────────────────────────────────
echo "[7/7] Generation de la commande de join..."
JOIN_CMD=$(kubeadm token create --print-join-command 2>/dev/null)
echo ""
echo "============================================================="
echo "COMMANDES A EXECUTER SUR CHAQUE WORKER (un par un) :"
echo "============================================================="
echo ""
echo "# --- Sur le WORKER (apres drain) ---"
echo "kubeadm reset -f"
echo "$JOIN_CMD --control-plane --certificate-key $CERT_KEY"
echo ""
echo "# Puis sur le master :"
echo "kubectl taint nodes <NOM_DU_NOEUD> node-role.kubernetes.io/control-plane- 2>/dev/null || true"
echo ""
echo "============================================================="
echo ""

# Sauvegarder les commandes dans un fichier
cat > /tmp/ha-join-commands.sh << EOF
#!/bin/bash
# Commandes de join generees le $(date)
# Validite du token : 24h

# CERT_KEY (validite 2h — regenerer si expire avec : kubeadm init phase upload-certs --upload-certs)
CERT_KEY="$CERT_KEY"

# Commande de join
$JOIN_CMD --control-plane --certificate-key $CERT_KEY
EOF
chmod 600 /tmp/ha-join-commands.sh

echo "Commandes sauvegardees dans /tmp/ha-join-commands.sh"
echo ""
echo "=== Phase 1 terminee ==="
echo "Executez maintenant 02-join-workers.sh sur CHAQUE worker (un a la fois)."
