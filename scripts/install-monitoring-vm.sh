#!/usr/bin/env bash
set -euo pipefail

MASTER_IP="${MASTER_IP:-192.168.148.153}"
WORKER1_IP="${WORKER1_IP:-192.168.148.158}"
WORKER2_IP="${WORKER2_IP:-192.168.148.155}"
CLUSTER_PROMETHEUS_HOST="${CLUSTER_PROMETHEUS_HOST:-192.168.148.158}"
CLUSTER_PROMETHEUS_PORT="${CLUSTER_PROMETHEUS_PORT:-30090}"
CLUSTER_LOKI_HOST="${CLUSTER_LOKI_HOST:-192.168.148.158}"
CLUSTER_LOKI_PORT="${CLUSTER_LOKI_PORT:-31080}"
MINIO_ROOT_USER="${MINIO_ROOT_USER:?MINIO_ROOT_USER is required}"
MINIO_ROOT_PASSWORD="${MINIO_ROOT_PASSWORD:?MINIO_ROOT_PASSWORD is required}"
MINIO_BACKUP_ACCESS_KEY="${MINIO_BACKUP_ACCESS_KEY:?MINIO_BACKUP_ACCESS_KEY is required}"
MINIO_BACKUP_SECRET_KEY="${MINIO_BACKUP_SECRET_KEY:?MINIO_BACKUP_SECRET_KEY is required}"
MINIO_BACKUP_BUCKET="${MINIO_BACKUP_BUCKET:-cnpg-backups}"
GRAFANA_ADMIN_PASSWORD="${GRAFANA_ADMIN_PASSWORD:-admin-change-me}"

sudo apt-get update
sudo apt-get install -y curl tar unzip gpg apt-transport-https ca-certificates software-properties-common nginx
sudo apt-get install -y prometheus

sudo mkdir -p /etc/apt/keyrings
if [ ! -f /etc/apt/keyrings/grafana.gpg ]; then
  curl -fsSL https://apt.grafana.com/gpg.key | sudo gpg --dearmor -o /etc/apt/keyrings/grafana.gpg
fi

cat <<'EOF' | sudo tee /etc/apt/sources.list.d/grafana.list >/dev/null
deb [signed-by=/etc/apt/keyrings/grafana.gpg] https://apt.grafana.com stable main
EOF

sudo apt-get update
sudo apt-get install -y grafana alloy

if ! command -v /usr/local/bin/minio >/dev/null 2>&1; then
  curl -fsSL -o /tmp/minio https://dl.min.io/server/minio/release/linux-amd64/minio
  sudo install -m 0755 /tmp/minio /usr/local/bin/minio
fi

if ! command -v /usr/local/bin/mc >/dev/null 2>&1; then
  curl -fsSL -o /tmp/mc https://dl.min.io/client/mc/release/linux-amd64/mc
  sudo install -m 0755 /tmp/mc /usr/local/bin/mc
fi

if ! id -u minio >/dev/null 2>&1; then
  sudo useradd --system --home /var/lib/minio --shell /usr/sbin/nologin minio
fi

cat <<EOF | sudo tee /usr/local/bin/pfe-configure-pod-routes.sh >/dev/null
#!/usr/bin/env bash
set -euo pipefail

ip route replace 192.168.126.0/26 via ${WORKER2_IP}
ip route replace 192.168.194.64/26 via ${WORKER1_IP}
ip route replace 192.168.235.192/26 via ${MASTER_IP}
EOF
sudo chmod 0755 /usr/local/bin/pfe-configure-pod-routes.sh

cat <<'EOF' | sudo tee /etc/systemd/system/pfe-pod-routes.service >/dev/null
[Unit]
Description=Static routes for Kubernetes pod CIDRs
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/pfe-configure-pod-routes.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

sudo mkdir -p /var/lib/minio/data /etc/minio
sudo chown -R minio:minio /var/lib/minio

cat <<EOF | sudo tee /etc/default/minio >/dev/null
MINIO_ROOT_USER=${MINIO_ROOT_USER}
MINIO_ROOT_PASSWORD=${MINIO_ROOT_PASSWORD}
EOF

cat <<'EOF' | sudo tee /etc/systemd/system/minio.service >/dev/null
[Unit]
Description=MinIO Object Storage
After=network-online.target
Wants=network-online.target

[Service]
User=minio
Group=minio
EnvironmentFile=/etc/default/minio
ExecStart=/usr/local/bin/minio server /var/lib/minio/data --console-address=:9001
Restart=always
RestartSec=5
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
EOF

sudo mkdir -p /etc/prometheus
cat <<EOF | sudo tee /etc/prometheus/prometheus.yml >/dev/null
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: prometheus
    static_configs:
      - targets: ['127.0.0.1:9090']

  - job_name: vm-node-exporters
    static_configs:
      - targets:
          - '192.168.148.153:9100'
          - '192.168.148.158:9100'
          - '192.168.148.155:9100'
          - '192.168.148.160:9100'
          - '192.168.148.161:9100'

  - job_name: camunda
    metrics_path: /actuator/prometheus
    static_configs:
      - targets: ['192.168.148.161:9600']

  - job_name: cluster-prometheus-federate
    metrics_path: /federate
    honor_labels: true
    params:
      'match[]':
        - '{__name__=~"up|container_cpu_usage_seconds_total|container_memory_working_set_bytes|kube_pod_container_status_restarts_total|kube_deployment_status_replicas_unavailable|kube_pod_info|node_cpu_seconds_total|node_memory_MemAvailable_bytes|node_memory_MemTotal_bytes|process_.*|go_.*|http_server_requests_seconds_.*|jvm_.*|pg_.*"}'
    static_configs:
      - targets: ['${CLUSTER_PROMETHEUS_HOST}:${CLUSTER_PROMETHEUS_PORT}']
EOF

sudo rm -f /etc/nginx/sites-enabled/default
cat <<EOF | sudo tee /etc/nginx/conf.d/pfe-loki-proxy.conf >/dev/null
# Port 3100: Loki read/query (Grafana datasource)
server {
    listen 3100;
    server_name _;

    location / {
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_pass http://${CLUSTER_LOKI_HOST}:${CLUSTER_LOKI_PORT};
    }
}

# Port 3101: Loki push/ingest (Alloy agents on each VM)
server {
    listen 3101;
    server_name _;

    location / {
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_pass http://${CLUSTER_LOKI_HOST}:${CLUSTER_LOKI_PORT};
    }
}
EOF

sudo mkdir -p /etc/grafana/provisioning/datasources
cat <<'EOF' | sudo tee /etc/grafana/provisioning/datasources/pfe.yaml >/dev/null
apiVersion: 1

datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://127.0.0.1:9090
    isDefault: true
  - name: Loki
    type: loki
    access: proxy
    url: http://127.0.0.1:3100
EOF

sudo sed -i "s/^;*http_addr =.*/http_addr = 0.0.0.0/" /etc/grafana/grafana.ini
sudo sed -i "s/^;*http_port =.*/http_port = 3000/" /etc/grafana/grafana.ini
sudo sed -i "s/^;*admin_password =.*/admin_password = ${GRAFANA_ADMIN_PASSWORD}/" /etc/grafana/grafana.ini

sudo systemctl daemon-reload
sudo systemctl enable --now pfe-pod-routes.service
sudo systemctl enable --now prometheus.service
sudo systemctl enable --now grafana-server.service
sudo systemctl disable --now loki.service 2>/dev/null || true
sudo systemctl enable --now minio.service
sudo systemctl enable --now alloy.service
sudo systemctl enable --now nginx.service

for port in 3000 3100 3101 9090 9000 9001; do
  sudo ufw allow "${port}/tcp" || true
done

sleep 10
/usr/local/bin/mc alias set local http://127.0.0.1:9000 "${MINIO_ROOT_USER}" "${MINIO_ROOT_PASSWORD}"
/usr/local/bin/mc mb --ignore-existing "local/${MINIO_BACKUP_BUCKET}"
if ! /usr/local/bin/mc admin user info local "${MINIO_BACKUP_ACCESS_KEY}" >/dev/null 2>&1; then
  /usr/local/bin/mc admin user add local "${MINIO_BACKUP_ACCESS_KEY}" "${MINIO_BACKUP_SECRET_KEY}"
fi
/usr/local/bin/mc admin policy attach local readwrite --user "${MINIO_BACKUP_ACCESS_KEY}"
