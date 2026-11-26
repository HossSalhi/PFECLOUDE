#!/usr/bin/env bash
# =============================================================================
# HELIOS — Registry prive local (sur .160)
# =============================================================================
# Deploie un registry Docker v2 leger sur la VM monitoring (.160).
# Les 3 noeuds K8s seront configures pour pull depuis ce registry.
#
# POURQUOI : en HA, chaque noeud doit pouvoir pull les images HELIOS.
# Sans registry, il faut `ctr import` manuellement sur chaque noeud.
# Avec le registry : build/push une fois, les 3 noeuds pull automatiquement.
#
# EXECUTER SUR .160 en root.
# =============================================================================
set -euo pipefail

REGISTRY_PORT=5000
REGISTRY_DIR="/opt/registry"
REGISTRY_DATA="${REGISTRY_DIR}/data"

echo "=== Installation du registry Docker v2 sur $(hostname) ==="

# 1. Installer le package registry
apt-get update -qq && apt-get install -y -qq docker-registry 2>/dev/null || {
    echo "docker-registry non disponible, installation manuelle..."
    mkdir -p "$REGISTRY_DATA"

    # Telecharger le binaire registry v2
    ARCH=$(dpkg --print-architecture 2>/dev/null || echo amd64)
    REG_VER="2.8.3"
    if [ ! -f /usr/local/bin/registry ]; then
        curl -sL "https://github.com/distribution/distribution/releases/download/v${REG_VER}/registry_${REG_VER}_linux_${ARCH}.tar.gz" \
            | tar xz -C /usr/local/bin registry 2>/dev/null || {
            echo "ERREUR: impossible de telecharger le binaire registry"
            echo "Alternative : docker run -d -p 5000:5000 -v ${REGISTRY_DATA}:/var/lib/registry registry:2"
            exit 1
        }
    fi
}

# 2. Configuration
mkdir -p "$REGISTRY_DATA" /etc/docker/registry
cat > /etc/docker/registry/config.yml <<'EOF'
version: 0.1
log:
  fields:
    service: registry
storage:
  filesystem:
    rootdirectory: /opt/registry/data
  delete:
    enabled: true
  maintenance:
    uploadpurging:
      enabled: true
      age: 168h
      interval: 24h
http:
  addr: :5000
  headers:
    X-Content-Type-Options: [nosniff]
health:
  storagedriver:
    enabled: true
    interval: 10s
    threshold: 3
EOF

# 3. Service systemd
cat > /etc/systemd/system/registry.service <<EOF
[Unit]
Description=Docker Registry v2
After=network.target

[Service]
Type=simple
ExecStart=/usr/local/bin/registry serve /etc/docker/registry/config.yml
Restart=always
RestartSec=5
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now registry.service
sleep 2

# 4. Verifier
if curl -s "http://localhost:${REGISTRY_PORT}/v2/" | grep -q "{}"; then
    echo "[OK] Registry ecoute sur :${REGISTRY_PORT}"
else
    echo "[WARN] Registry potentiellement pas encore pret"
fi

echo ""
echo "=== Configuration des noeuds K8s ==="
echo ""
echo "Sur CHAQUE noeud (.153, .155, .158), executer :"
echo ""
cat <<'NODES'
# Ajouter le registry comme miroir insecure (HTTP) dans containerd :
cat >> /etc/containerd/config.toml <<'CONTAINERD'

[plugins."io.containerd.grpc.v1.cri".registry.mirrors."192.168.148.160:5000"]
  endpoint = ["http://192.168.148.160:5000"]
[plugins."io.containerd.grpc.v1.cri".registry.configs."192.168.148.160:5000".tls]
  insecure_skip_verify = true
CONTAINERD

systemctl restart containerd

# Verifier la connectivite :
curl -s http://192.168.148.160:5000/v2/
NODES

echo ""
echo "=== Push des images HELIOS ==="
echo ""
echo "Depuis le noeud ou les images sont buildees (.153 ou .161) :"
echo ""
cat <<'PUSH'
# Tagger pour le registry prive
ctr -n k8s.io images tag pfe-backend:0.2.150 192.168.148.160:5000/helios/backend:0.2.150
ctr -n k8s.io images tag pfe-frontend:1.1.6 192.168.148.160:5000/helios/frontend:1.1.6

# Push
ctr -n k8s.io images push --plain-http 192.168.148.160:5000/helios/backend:0.2.150
ctr -n k8s.io images push --plain-http 192.168.148.160:5000/helios/frontend:1.1.6

# Verifier
curl -s http://192.168.148.160:5000/v2/_catalog
PUSH

echo ""
echo "=== Mise a jour des deployments ==="
echo ""
echo "Apres le push, mettre a jour les images dans les deployments :"
echo "  kubectl -n pfe set image deploy/backend backend=192.168.148.160:5000/helios/backend:0.2.150"
echo "  kubectl -n pfe set image deploy/frontend frontend=192.168.148.160:5000/helios/frontend:1.1.6"
echo ""
echo "Ou mettre a jour values.yaml du Helm chart :"
echo "  backend.image.repository: 192.168.148.160:5000/helios/backend"
echo "  frontend.image.repository: 192.168.148.160:5000/helios/frontend"
echo ""
echo "[DONE] Registry pret sur http://192.168.148.160:${REGISTRY_PORT}"
