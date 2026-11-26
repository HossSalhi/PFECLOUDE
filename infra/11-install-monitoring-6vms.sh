#!/usr/bin/env bash
# =============================================================================
# HELIOS — Deploiement du monitoring sur les 6 VMs
# =============================================================================
# Installe node_exporter + promtail sur la VM ou il est lance.
# A EXECUTER SUR CHACUNE DES 6 VMs en root.
#
#   ./11-install-monitoring-6vms.sh <nom-vm>
#
#   Noms attendus : k8s-master | k8s-worker1 | k8s-worker2
#                   monitoring | aicamunda   | lb2
#
# Sur .160 en plus : blackbox_exporter + rechargement Prometheus/Alertmanager.
# =============================================================================
set -euo pipefail

NOM_VM="${1:-}"
if [ -z "$NOM_VM" ]; then
    echo "Usage : $0 <nom-vm>"
    echo "  k8s-master | k8s-worker1 | k8s-worker2 | monitoring | aicamunda | lb2"
    exit 1
fi

MOI=$(hostname -I | awk '{print $1}')
LOKI="192.168.148.160:3100"

echo "================================================================"
echo " HELIOS — Monitoring sur $NOM_VM [$MOI]"
echo "================================================================"

# ══════════════════════════════════════════════════════════════════
#  1. NODE EXPORTER — metriques host (CPU/RAM/disque/reseau/systemd)
# ══════════════════════════════════════════════════════════════════
echo ""
echo "=== 1. node_exporter ==="

if systemctl is-active --quiet prometheus-node-exporter 2>/dev/null \
   || systemctl is-active --quiet node_exporter 2>/dev/null; then
    echo "[OK] node_exporter deja actif"
else
    apt-get update -qq
    apt-get install -y -qq prometheus-node-exporter
    echo "[OK] node_exporter installe"
fi

# ACTIVER le collecteur systemd : indispensable pour detecter
# kubelet/containerd/haproxy arretes (regles KubeletDown, SystemdUnitFailed).
CONF_NE="/etc/default/prometheus-node-exporter"
if [ -f "$CONF_NE" ]; then
    if ! grep -q "collector.systemd" "$CONF_NE"; then
        cp "$CONF_NE" "${CONF_NE}.bak"
        cat > "$CONF_NE" <<'NECFG'
# HELIOS : --collector.systemd est REQUIS pour les alertes de service
# (KubeletDown, ContainerRuntimeDown, SystemdUnitFailed).
# --collector.processes donne le nb de processus par etat (zombie, D-state).
ARGS="--collector.systemd \
      --collector.processes \
      --collector.systemd.unit-include=(kubelet|containerd|haproxy|docker|prometheus|loki|grafana-server|alertmanager|helios-.*|ssh)\.service"
NECFG
        echo "[OK] Collecteurs systemd + processes actives"
    else
        echo "[OK] Collecteur systemd deja actif"
    fi
fi

systemctl enable --now prometheus-node-exporter
systemctl restart prometheus-node-exporter
sleep 2

if curl -s "http://localhost:9100/metrics" | head -1 | grep -q "#"; then
    echo "[OK] node_exporter repond sur :9100"
else
    echo "[FAIL] node_exporter ne repond pas"
fi

# ══════════════════════════════════════════════════════════════════
#  2. PROMTAIL — logs systeme vers Loki
# ══════════════════════════════════════════════════════════════════
echo ""
echo "=== 2. Promtail (logs systeme -> Loki) ==="

if systemctl is-active --quiet promtail 2>/dev/null; then
    echo "[OK] Promtail deja actif"
else
    PROMTAIL_VER="3.0.0"
    if [ ! -f /usr/local/bin/promtail ]; then
        cd /tmp
        curl -sLO "https://github.com/grafana/loki/releases/download/v${PROMTAIL_VER}/promtail-linux-amd64.zip" || {
            echo "[WARN] Telechargement Promtail impossible — installer manuellement"
        }
        if [ -f promtail-linux-amd64.zip ]; then
            command -v unzip &>/dev/null || apt-get install -y -qq unzip
            unzip -oq promtail-linux-amd64.zip
            mv promtail-linux-amd64 /usr/local/bin/promtail
            chmod +x /usr/local/bin/promtail
            echo "[OK] Promtail v${PROMTAIL_VER} installe"
        fi
    fi

    mkdir -p /etc/promtail /var/lib/promtail

    # Config avec le bon label vm= pour CETTE machine
    cat > /etc/promtail/config.yml <<PTCFG
server:
  http_listen_port: 9080
  grpc_listen_port: 0
  log_level: info

positions:
  filename: /var/lib/promtail/positions.yaml

clients:
  - url: http://${LOKI}/loki/api/v1/push
    batchwait: 1s
    batchsize: 1048576
    backoff_config:
      min_period: 500ms
      max_period: 5m
      max_retries: 10
    external_labels:
      vm: ${NOM_VM}

scrape_configs:
  - job_name: journal
    journal:
      max_age: 12h
      path: /var/log/journal
      labels:
        job: systemd-journal
        vm: ${NOM_VM}
    relabel_configs:
      - source_labels: ['__journal__systemd_unit']
        target_label: unit
      - source_labels: ['__journal_priority_keyword']
        target_label: level

  - job_name: syslog
    static_configs:
      - targets: [localhost]
        labels: {job: syslog, vm: ${NOM_VM}, __path__: /var/log/syslog}

  - job_name: kernel
    static_configs:
      - targets: [localhost]
        labels: {job: kernel, vm: ${NOM_VM}, __path__: /var/log/kern.log}

  - job_name: auth
    static_configs:
      - targets: [localhost]
        labels: {job: auth, vm: ${NOM_VM}, __path__: /var/log/auth.log}
PTCFG

    # HAProxy uniquement sur .160 / .162
    if [ "$NOM_VM" = "monitoring" ] || [ "$NOM_VM" = "lb2" ]; then
        cat >> /etc/promtail/config.yml <<PTHA

  - job_name: haproxy
    static_configs:
      - targets: [localhost]
        labels: {job: haproxy, vm: ${NOM_VM}, __path__: /var/log/haproxy.log}
PTHA
    fi

    # Docker uniquement sur .161 (Camunda / Ollama)
    if [ "$NOM_VM" = "aicamunda" ]; then
        cat >> /etc/promtail/config.yml <<'PTDK'

  - job_name: docker
    static_configs:
      - targets: [localhost]
        labels: {job: docker, __path__: /var/lib/docker/containers/*/*-json.log}
    pipeline_stages:
      - json: {expressions: {output: log, stream: stream, timestamp: time}}
      - labels: {stream: }
      - timestamp: {source: timestamp, format: RFC3339Nano}
      - output: {source: output}
PTDK
    fi

    cat > /etc/systemd/system/promtail.service <<'PTSVC'
[Unit]
Description=Promtail — collecteur de logs Loki
After=network.target

[Service]
Type=simple
User=root
ExecStart=/usr/local/bin/promtail -config.file=/etc/promtail/config.yml
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
PTSVC

    systemctl daemon-reload
    systemctl enable --now promtail
    sleep 2

    if systemctl is-active --quiet promtail; then
        echo "[OK] Promtail actif (label vm=${NOM_VM})"
    else
        echo "[FAIL] Promtail n'a pas demarre"
        journalctl -u promtail --no-pager -n 10
    fi
fi

# ══════════════════════════════════════════════════════════════════
#  3. SPECIFIQUE .160 — blackbox_exporter + rechargement
# ══════════════════════════════════════════════════════════════════
if [ "$NOM_VM" = "monitoring" ]; then
    echo ""
    echo "=== 3. blackbox_exporter (sondes ICMP + HTTP) ==="

    if systemctl is-active --quiet prometheus-blackbox-exporter 2>/dev/null; then
        echo "[OK] blackbox_exporter deja actif"
    else
        apt-get install -y -qq prometheus-blackbox-exporter

        cat > /etc/prometheus/blackbox.yml <<'BBCFG'
modules:
  icmp:
    prober: icmp
    timeout: 5s
    icmp:
      preferred_ip_protocol: ip4
  http_2xx:
    prober: http
    timeout: 10s
    http:
      valid_status_codes: [200, 201, 301, 302, 401, 403]
      method: GET
      preferred_ip_protocol: ip4
      tls_config: {insecure_skip_verify: true}
  tcp_connect:
    prober: tcp
    timeout: 5s
BBCFG

        # L'ICMP exige CAP_NET_RAW
        setcap cap_net_raw+ep /usr/bin/prometheus-blackbox-exporter 2>/dev/null || true
        systemctl enable --now prometheus-blackbox-exporter
        echo "[OK] blackbox_exporter actif sur :9115"
    fi

    echo ""
    echo "=== 4. Deploiement des regles d'alerte ==="
    mkdir -p /etc/prometheus/rules
    if [ -f ./monitoring/rules-infrastructure.yml ]; then
        cp ./monitoring/rules-infrastructure.yml /etc/prometheus/rules/
        echo "[OK] rules-infrastructure.yml deploye"
    else
        echo "[WARN] rules-infrastructure.yml introuvable dans ./monitoring/"
        echo "       Copier infra/monitoring/rules-infrastructure.yml sur cette VM"
    fi

    # Token pour l'auth Alertmanager -> bridge Camunda
    if [ ! -f /etc/alertmanager/camunda-token ]; then
        mkdir -p /etc/alertmanager
        openssl rand -hex 24 > /etc/alertmanager/camunda-token
        chmod 600 /etc/alertmanager/camunda-token
        echo "[OK] Token Alertmanager->Camunda genere :"
        echo "     $(cat /etc/alertmanager/camunda-token)"
        echo "     -> Reporter cette valeur sur .161 dans HELIOS_ALERTMANAGER_TOKEN"
    fi

    echo ""
    echo "=== 5. Rechargement ==="
    promtool check config /etc/prometheus/prometheus.yml 2>/dev/null \
        && echo "[OK] Config Prometheus valide" \
        || echo "[WARN] Verifier /etc/prometheus/prometheus.yml"

    curl -sX POST http://localhost:9090/-/reload  && echo "[OK] Prometheus recharge"
    curl -sX POST http://localhost:9093/-/reload  && echo "[OK] Alertmanager recharge"
fi

# ══════════════════════════════════════════════════════════════════
#  RESUME
# ══════════════════════════════════════════════════════════════════
echo ""
echo "================================================================"
echo " TERMINE — $NOM_VM"
echo "================================================================"
echo "  node_exporter : $(systemctl is-active prometheus-node-exporter 2>/dev/null || echo inactif) (:9100)"
echo "  promtail      : $(systemctl is-active promtail 2>/dev/null || echo inactif) (:9080)"
if [ "$NOM_VM" = "monitoring" ]; then
echo "  blackbox      : $(systemctl is-active prometheus-blackbox-exporter 2>/dev/null || echo inactif) (:9115)"
echo "  prometheus    : $(systemctl is-active prometheus 2>/dev/null || echo inactif) (:9090)"
echo "  alertmanager  : $(systemctl is-active alertmanager 2>/dev/null || echo inactif) (:9093)"
fi
echo ""
echo "  Verifier depuis .160 :"
echo "    curl -s localhost:9090/api/v1/targets | jq '.data.activeTargets[]|{job,health}'"
echo "================================================================"
