#!/usr/bin/env bash
# =============================================================================
# HELIOS — Phase 5 : Installer Alertmanager sur la VM monitoring (.160)
# =============================================================================
# EXECUTER SUR .160 en root.
# =============================================================================
set -euo pipefail

ALERTMANAGER_VERSION="0.27.0"
INSTALL_DIR="/opt/alertmanager"

echo "=== Installation d'Alertmanager ${ALERTMANAGER_VERSION} ==="

# ── 1. Telecharger ────────────────────────────────────────────────────────────
echo "[1/5] Telechargement..."
cd /tmp
wget -q "https://github.com/prometheus/alertmanager/releases/download/v${ALERTMANAGER_VERSION}/alertmanager-${ALERTMANAGER_VERSION}.linux-amd64.tar.gz" \
    -O alertmanager.tar.gz || {
    echo "  [WARN] Telechargement echoue. Installer manuellement."
    echo "  URL: https://github.com/prometheus/alertmanager/releases/download/v${ALERTMANAGER_VERSION}/alertmanager-${ALERTMANAGER_VERSION}.linux-amd64.tar.gz"
    exit 1
}

# ── 2. Installer ──────────────────────────────────────────────────────────────
echo "[2/5] Installation..."
mkdir -p "$INSTALL_DIR" /etc/alertmanager /var/lib/alertmanager
tar xzf alertmanager.tar.gz --strip-components=1 -C "$INSTALL_DIR"
ln -sf "$INSTALL_DIR/alertmanager" /usr/local/bin/alertmanager
ln -sf "$INSTALL_DIR/amtool" /usr/local/bin/amtool

# ── 3. Configuration ─────────────────────────────────────────────────────────
echo "[3/5] Configuration..."
cp /root/infra/ha-upgrade/alertmanager.yml /etc/alertmanager/alertmanager.yml 2>/dev/null || \
    echo "  [INFO] Copier manuellement alertmanager.yml vers /etc/alertmanager/"

# ── 4. Service systemd ───────────────────────────────────────────────────────
echo "[4/5] Creation du service systemd..."
cat > /etc/systemd/system/alertmanager.service << 'EOF'
[Unit]
Description=Prometheus Alertmanager
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
ExecStart=/usr/local/bin/alertmanager \
    --config.file=/etc/alertmanager/alertmanager.yml \
    --storage.path=/var/lib/alertmanager \
    --web.listen-address=:9093 \
    --web.external-url=http://192.168.148.160:9093 \
    --cluster.listen-address=""
ExecReload=/bin/kill -HUP $MAINPID
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable alertmanager
systemctl start alertmanager

# ── 5. Configurer Prometheus pour envoyer les alertes a Alertmanager ──────────
echo "[5/5] Mise a jour de Prometheus..."
# Ajouter la section alerting dans prometheus.yml si elle n'existe pas
PROM_CONF="/etc/prometheus/prometheus.yml"
if [ -f "$PROM_CONF" ]; then
    if ! grep -q "alertmanagers" "$PROM_CONF"; then
        # Inserer la config alerting apres global:
        sed -i '/^global:/a\\nalerting:\n  alertmanagers:\n    - static_configs:\n        - targets:\n            - "localhost:9093"' "$PROM_CONF"
        echo "  Prometheus configure pour envoyer les alertes a Alertmanager."
        systemctl reload prometheus 2>/dev/null || systemctl restart prometheus
    else
        echo "  Prometheus deja configure pour Alertmanager."
    fi
else
    echo "  [WARN] $PROM_CONF introuvable. Ajouter manuellement :"
    echo "  alerting:"
    echo "    alertmanagers:"
    echo "      - static_configs:"
    echo "          - targets: ['localhost:9093']"
fi

echo ""
echo "=== Alertmanager installe ==="
echo "  URL : http://192.168.148.160:9093"
echo "  Verifier : curl -s http://localhost:9093/-/healthy"
echo ""

# Test
sleep 3
curl -s http://localhost:9093/-/healthy && echo "  Alertmanager OK" || echo "  [WARN] Alertmanager ne repond pas encore"
