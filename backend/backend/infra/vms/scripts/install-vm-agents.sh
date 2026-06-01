#!/usr/bin/env bash
set -euo pipefail

ROLE="${1:-generic}"
MONITORING_VM_IP="${MONITORING_VM_IP:-192.168.148.160}"
NODE_EXPORTER_VERSION="${NODE_EXPORTER_VERSION:-1.9.1}"
ALLOY_VERSION="${ALLOY_VERSION:-1.8.3}"

sudo apt-get update
sudo apt-get install -y curl tar gpg apt-transport-https ca-certificates

if ! id -u node_exporter >/dev/null 2>&1; then
  sudo useradd --no-create-home --shell /usr/sbin/nologin node_exporter
fi

if ! command -v /usr/local/bin/node_exporter >/dev/null 2>&1; then
  tmp_dir="$(mktemp -d)"
  trap 'rm -rf "$tmp_dir"' EXIT
  curl -fsSL -o "$tmp_dir/node_exporter.tar.gz" \
    "https://github.com/prometheus/node_exporter/releases/download/v${NODE_EXPORTER_VERSION}/node_exporter-${NODE_EXPORTER_VERSION}.linux-amd64.tar.gz"
  tar -xzf "$tmp_dir/node_exporter.tar.gz" -C "$tmp_dir"
  sudo install -m 0755 "$tmp_dir/node_exporter-${NODE_EXPORTER_VERSION}.linux-amd64/node_exporter" /usr/local/bin/node_exporter
fi

cat <<'EOF' | sudo tee /etc/systemd/system/node-exporter.service >/dev/null
[Unit]
Description=Prometheus Node Exporter
After=network-online.target
Wants=network-online.target

[Service]
User=node_exporter
Group=node_exporter
ExecStart=/usr/local/bin/node_exporter
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo mkdir -p /etc/apt/keyrings
if [ ! -f /etc/apt/keyrings/grafana.gpg ]; then
  curl -fsSL https://apt.grafana.com/gpg.key | sudo gpg --dearmor -o /etc/apt/keyrings/grafana.gpg
fi

cat <<'EOF' | sudo tee /etc/apt/sources.list.d/grafana.list >/dev/null
deb [signed-by=/etc/apt/keyrings/grafana.gpg] https://apt.grafana.com stable main
EOF

sudo apt-get update
sudo apt-get install -y alloy

sudo mkdir -p /etc/alloy

host_name="$(hostname)"
cat <<EOF | sudo tee /etc/alloy/config.alloy >/dev/null
local.file_match "vm_syslog" {
  path_targets = [{
    __path__ = "/var/log/syslog",
    job      = "vm-syslog",
    host     = "${host_name}",
    role     = "${ROLE}",
  }]
}

loki.source.file "syslog" {
  targets    = local.file_match.vm_syslog.targets
  forward_to = [loki.write.monitoring.receiver]
}

local.file_match "k8s_container_logs" {
  path_targets = [{
    __path__ = "/var/log/containers/*.log",
    job      = "k8s-containers",
    host     = "${host_name}",
    role     = "${ROLE}",
    vm_ip    = "$(hostname -I | awk '{print \$1}')",
  }]
}

loki.source.file "k8s_containers" {
  targets    = local.file_match.k8s_container_logs.targets
  forward_to = [loki.write.monitoring.receiver]
}

loki.write "monitoring" {
  endpoint {
    url = "http://${MONITORING_VM_IP}:3101/loki/api/v1/push"
  }
}
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now node-exporter.service
sudo systemctl enable --now alloy.service
