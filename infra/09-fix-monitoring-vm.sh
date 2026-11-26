#!/usr/bin/env bash
# =============================================================================
# HELIOS — Correction VM Monitoring (.160)
# =============================================================================
# Installe les composants manquants sur la VM monitoring :
#   1. Alertmanager (notifications email des alertes Prometheus)
#   2. Registry Docker v2 (pull images K8s-natif)
#   3. Suppression de Keepalived (plus necessaire avec MetalLB)
#   4. Nettoyage HAProxy apps (garde uniquement control-plane)
#
# EXECUTER SUR .160 en root.
# =============================================================================
set -euo pipefail

echo "================================================================"
echo " HELIOS — Correction VM Monitoring (.160)"
echo "================================================================"

# ------------------------------------------------------------------ 1. Alertmanager
echo ""
echo "=== 1. Installation Alertmanager ==="

ALERTMANAGER_VER="0.27.0"
ARCH=$(dpkg --print-architecture 2>/dev/null || echo amd64)

if systemctl is-active --quiet alertmanager 2>/dev/null; then
    echo "[OK] Alertmanager deja installe et actif"
else
    echo "[INSTALL] Telechargement Alertmanager v${ALERTMANAGER_VER}..."

    cd /tmp
    if [ ! -f alertmanager-${ALERTMANAGER_VER}.linux-${ARCH}.tar.gz ]; then
        curl -sLO "https://github.com/prometheus/alertmanager/releases/download/v${ALERTMANAGER_VER}/alertmanager-${ALERTMANAGER_VER}.linux-${ARCH}.tar.gz" || {
            echo "[WARN] Impossible de telecharger Alertmanager. Verifier la connexion internet."
            echo "  Telecharger manuellement : https://github.com/prometheus/alertmanager/releases"
        }
    fi

    if [ -f alertmanager-${ALERTMANAGER_VER}.linux-${ARCH}.tar.gz ]; then
        tar xzf alertmanager-${ALERTMANAGER_VER}.linux-${ARCH}.tar.gz
        cp alertmanager-${ALERTMANAGER_VER}.linux-${ARCH}/alertmanager /usr/local/bin/
        cp alertmanager-${ALERTMANAGER_VER}.linux-${ARCH}/amtool /usr/local/bin/
        chmod +x /usr/local/bin/alertmanager /usr/local/bin/amtool

        # Creer l'utilisateur
        useradd --no-create-home --shell /bin/false alertmanager 2>/dev/null || true
        mkdir -p /etc/alertmanager /var/lib/alertmanager
        chown alertmanager:alertmanager /var/lib/alertmanager

        # Configuration
        cat > /etc/alertmanager/alertmanager.yml <<'AMCFG'
global:
  resolve_timeout: 5m

route:
  group_by: ['alertname', 'namespace', 'severity']
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 4h
  receiver: 'helios-email'
  routes:
    - receiver: 'helios-critical'
      match:
        severity: critical
      group_wait: 10s
      repeat_interval: 1h

receivers:
  - name: 'helios-email'
    email_configs:
      - to: 'houssamsalhi972@gmail.com'
        from: 'helios-alertmanager@orange.tn'
        smarthost: 'localhost:25'
        require_tls: false
        send_resolved: true
        headers:
          Subject: '[HELIOS] {{ .GroupLabels.alertname }} - {{ .GroupLabels.severity }}'

  - name: 'helios-critical'
    email_configs:
      - to: 'houssamsalhi972@gmail.com'
        from: 'helios-alertmanager@orange.tn'
        smarthost: 'localhost:25'
        require_tls: false
        send_resolved: true
        headers:
          Subject: '[HELIOS CRITIQUE] {{ .GroupLabels.alertname }}'

inhibit_rules:
  - source_match:
      severity: 'critical'
    target_match:
      severity: 'warning'
    equal: ['alertname', 'namespace']
AMCFG

        # Service systemd
        cat > /etc/systemd/system/alertmanager.service <<'AMSVC'
[Unit]
Description=Prometheus Alertmanager
After=network.target

[Service]
User=alertmanager
Group=alertmanager
Type=simple
ExecStart=/usr/local/bin/alertmanager \
    --config.file=/etc/alertmanager/alertmanager.yml \
    --storage.path=/var/lib/alertmanager \
    --web.listen-address=:9093
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
AMSVC

        systemctl daemon-reload
        systemctl enable --now alertmanager
        sleep 2

        if systemctl is-active --quiet alertmanager; then
            echo "[OK] Alertmanager installe et actif sur :9093"
        else
            echo "[FAIL] Alertmanager n'a pas demarre"
            journalctl -u alertmanager --no-pager -n 5
        fi
    fi
fi

# Configurer Prometheus pour utiliser Alertmanager
echo ""
echo "=== 1b. Configuration Prometheus -> Alertmanager ==="
PROM_CFG="/etc/prometheus/prometheus.yml"
if [ -f "$PROM_CFG" ]; then
    if grep -q "alertmanagers" "$PROM_CFG"; then
        echo "[OK] Prometheus a deja une section alertmanagers"
    else
        echo "[PATCH] Ajout d'alertmanagers dans prometheus.yml..."
        # Ajouter la config alertmanager au debut du fichier
        sed -i '/^global:/a\\nalerting:\n  alertmanagers:\n    - static_configs:\n        - targets: ["localhost:9093"]' "$PROM_CFG"
        systemctl reload prometheus 2>/dev/null || systemctl restart prometheus 2>/dev/null || true
        echo "[OK] Prometheus configure pour envoyer les alertes a Alertmanager"
    fi
else
    echo "[WARN] $PROM_CFG introuvable. Configurer manuellement :"
    echo "  alerting:"
    echo "    alertmanagers:"
    echo "      - static_configs:"
    echo "          - targets: ['localhost:9093']"
fi

# ------------------------------------------------------------------ 2. Registry
echo ""
echo "=== 2. Installation Registry Docker v2 ==="

REGISTRY_PORT=5000
REGISTRY_DIR="/opt/registry"
REGISTRY_DATA="${REGISTRY_DIR}/data"

if systemctl is-active --quiet registry 2>/dev/null; then
    echo "[OK] Registry deja installe et actif sur :${REGISTRY_PORT}"
elif curl -s "http://localhost:${REGISTRY_PORT}/v2/" 2>/dev/null | grep -q "{}"; then
    echo "[OK] Registry deja actif sur :${REGISTRY_PORT}"
else
    echo "[INSTALL] Installation du registry Docker v2..."

    apt-get update -qq && apt-get install -y -qq docker-registry 2>/dev/null || {
        mkdir -p "$REGISTRY_DATA"
        REG_VER="2.8.3"
        if [ ! -f /usr/local/bin/registry ]; then
            curl -sL "https://github.com/distribution/distribution/releases/download/v${REG_VER}/registry_${REG_VER}_linux_${ARCH}.tar.gz" \
                | tar xz -C /usr/local/bin registry 2>/dev/null || {
                echo "[WARN] Impossible de telecharger le binaire registry"
                echo "  Alternative : docker run -d -p 5000:5000 -v ${REGISTRY_DATA}:/var/lib/registry registry:2"
            }
        fi
    }

    mkdir -p "$REGISTRY_DATA" /etc/docker/registry
    cat > /etc/docker/registry/config.yml <<'REGCFG'
version: 0.1
log:
  fields:
    service: registry
storage:
  filesystem:
    rootdirectory: /opt/registry/data
  delete:
    enabled: true
http:
  addr: :5000
  headers:
    X-Content-Type-Options: [nosniff]
health:
  storagedriver:
    enabled: true
    interval: 10s
    threshold: 3
REGCFG

    cat > /etc/systemd/system/registry.service <<'REGSVC'
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
REGSVC

    systemctl daemon-reload
    systemctl enable --now registry.service
    sleep 2

    if curl -s "http://localhost:${REGISTRY_PORT}/v2/" | grep -q "{}"; then
        echo "[OK] Registry actif sur :${REGISTRY_PORT}"
    else
        echo "[WARN] Registry ne repond pas encore"
    fi
fi

# ------------------------------------------------------------------ 3. Keepalived
echo ""
echo "=== 3. Suppression de Keepalived (remplace par MetalLB K8s-natif) ==="

if systemctl is-active --quiet keepalived 2>/dev/null; then
    systemctl stop keepalived
    systemctl disable keepalived
    echo "[OK] Keepalived arrete et desactive"
elif dpkg -l keepalived &>/dev/null 2>&1; then
    systemctl disable keepalived 2>/dev/null || true
    echo "[OK] Keepalived desactive"
else
    echo "[OK] Keepalived pas installe (rien a faire)"
fi

# ------------------------------------------------------------------ 4. HAProxy
echo ""
echo "=== 4. HAProxy : garder UNIQUEMENT le control-plane K8s ==="
echo ""
echo "Le HAProxy sur .160 reste pour le control-plane kubeadm (port 6443)."
echo "Les sections apps (port 80/443) vers NodePort ne sont PLUS necessaires"
echo "car MetalLB + ingress-nginx LoadBalancer gerent le trafic applicatif."
echo ""
echo "Configuration HAProxy recommandee (/etc/haproxy/haproxy.cfg) :"
echo "  - GARDER : frontend k8s_api (bind *:6443) + backend k8s_masters"
echo "  - SUPPRIMER : frontend apps_http, apps_https, backend ingress_http, ingress_https"
echo ""

if [ -f /etc/haproxy/haproxy.cfg ]; then
    # Backup
    cp /etc/haproxy/haproxy.cfg /etc/haproxy/haproxy.cfg.bak-$(date +%Y%m%d)
    echo "[OK] Backup de haproxy.cfg cree"

    # Verifier si les sections apps existent
    if grep -q "apps_http\|apps_https\|ingress_http\|ingress_https" /etc/haproxy/haproxy.cfg; then
        echo "[INFO] Sections apps detectees dans haproxy.cfg."
        echo "  Pour les supprimer manuellement :"
        echo "    nano /etc/haproxy/haproxy.cfg"
        echo "  Supprimer : frontend apps_http, frontend apps_https,"
        echo "              backend ingress_http, backend ingress_https"
        echo "  Puis : systemctl reload haproxy"
    else
        echo "[OK] Pas de sections apps dans haproxy.cfg"
    fi
else
    echo "[INFO] haproxy.cfg introuvable — HAProxy pas encore configure"
fi

# ------------------------------------------------------------------ 5. Resume
echo ""
echo "================================================================"
echo " RESUME — VM Monitoring (.160)"
echo "================================================================"
echo ""
echo "  Alertmanager : $(systemctl is-active alertmanager 2>/dev/null || echo 'non installe')"
echo "  Registry     : $(curl -s http://localhost:5000/v2/ 2>/dev/null | grep -q '{}' && echo 'actif :5000' || echo 'inactif')"
echo "  Keepalived   : $(systemctl is-active keepalived 2>/dev/null || echo 'desactive/absent')"
echo "  HAProxy      : $(systemctl is-active haproxy 2>/dev/null || echo 'inactif')"
echo "  Prometheus   : $(systemctl is-active prometheus 2>/dev/null || echo 'inactif')"
echo "  Grafana      : $(systemctl is-active grafana-server 2>/dev/null || echo 'inactif')"
echo "  Loki         : $(systemctl is-active loki 2>/dev/null || echo 'inactif')"
echo ""
echo "================================================================"
