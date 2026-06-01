#!/usr/bin/env bash
set -euo pipefail

CAMUNDA_RUN_VERSION="${CAMUNDA_RUN_VERSION:-8.9.0}"
MONITORING_VM_IP="${MONITORING_VM_IP:-192.168.148.160}"
TELEMETRY_PROMETHEUS_HOST="${TELEMETRY_PROMETHEUS_HOST:-${MONITORING_VM_IP}}"
TELEMETRY_PROMETHEUS_PORT="${TELEMETRY_PROMETHEUS_PORT:-9090}"
TELEMETRY_LOKI_HOST="${TELEMETRY_LOKI_HOST:-${MONITORING_VM_IP}}"
TELEMETRY_LOKI_PORT="${TELEMETRY_LOKI_PORT:-3101}"
MASTER_IP="${MASTER_IP:-192.168.148.153}"
WORKER1_IP="${WORKER1_IP:-192.168.148.158}"
WORKER2_IP="${WORKER2_IP:-192.168.148.155}"
AI_SERVICE_DIR="${AI_SERVICE_DIR:-/opt/pfe-ai-service}"
CAMUNDA_DIR="${CAMUNDA_DIR:-/opt/camunda/c8run}"

sudo apt-get update
sudo apt-get install -y curl unzip tar openjdk-21-jre-headless python3-venv python3-pip socat

if ! id -u camunda >/dev/null 2>&1; then
  sudo useradd --system --home /opt/camunda --shell /usr/sbin/nologin camunda
fi
if ! id -u aiops >/dev/null 2>&1; then
  sudo useradd --system --home "${AI_SERVICE_DIR}" --shell /usr/sbin/nologin aiops
fi

sudo mkdir -p /opt/camunda /opt/pfe-ai-service

installed_camunda_version=""
if [ -f "${CAMUNDA_DIR}/.env" ]; then
  installed_camunda_version="$(sed -n 's/^CAMUNDA_VERSION=//p' "${CAMUNDA_DIR}/.env" | head -n1 || true)"
fi

if [ ! -x "${CAMUNDA_DIR}/c8run" ] || [ "${installed_camunda_version}" != "${CAMUNDA_RUN_VERSION}" ]; then
  tmp_dir="$(mktemp -d)"
  trap 'rm -rf "$tmp_dir"' EXIT
  archive="camunda8-run-${CAMUNDA_RUN_VERSION}-linux-x86_64.tar.gz"
  curl -fsSL -o "${tmp_dir}/${archive}" "https://github.com/camunda/camunda/releases/download/${CAMUNDA_RUN_VERSION}/${archive}"
  sudo rm -rf "${CAMUNDA_DIR}"
  sudo mkdir -p "${CAMUNDA_DIR}"
  sudo tar -xzf "${tmp_dir}/${archive}" -C "${CAMUNDA_DIR}" --strip-components=1
fi

sudo chown -R camunda:camunda /opt/camunda
sudo chown -R aiops:aiops /opt/pfe-ai-service

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

cat <<'EOF' | sudo tee /etc/systemd/system/camunda8.service >/dev/null
[Unit]
Description=Camunda 8 Run
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
User=camunda
Group=camunda
WorkingDirectory=/opt/camunda/c8run
Environment=JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64
LimitNOFILE=65535
ExecStart=/opt/camunda/c8run/c8run start -detached
ExecStop=/opt/camunda/c8run/c8run stop

[Install]
WantedBy=multi-user.target
EOF

cat <<'EOF' | sudo tee /etc/systemd/system/camunda8-rest-proxy.service >/dev/null
[Unit]
Description=Camunda 8 REST Port Proxy
After=camunda8.service
Requires=camunda8.service

[Service]
Type=simple
ExecStart=/usr/bin/socat TCP-LISTEN:8088,fork,reuseaddr TCP:127.0.0.1:8080
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

cat <<EOF | sudo tee /etc/systemd/system/pfe-ai-service.service >/dev/null
[Unit]
Description=PFE AI Service
After=network-online.target
Wants=network-online.target

[Service]
User=aiops
Group=aiops
WorkingDirectory=${AI_SERVICE_DIR}
Environment=PATH=${AI_SERVICE_DIR}/.venv/bin:/usr/bin:/bin
Environment=AIOPS_LOG_LEVEL=INFO
Environment=AIOPS_ENABLE_UPSTREAM_QUERIES=true
Environment=AIOPS_LOKI_BASE_URL=http://${TELEMETRY_LOKI_HOST}:${TELEMETRY_LOKI_PORT}
Environment=AIOPS_PROMETHEUS_BASE_URL=http://${TELEMETRY_PROMETHEUS_HOST}:${TELEMETRY_PROMETHEUS_PORT}
Environment=AIOPS_MODEL_PATH=${AI_SERVICE_DIR}/model_artifacts/model.joblib
ExecStart=${AI_SERVICE_DIR}/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

for port in 8000 8088 26500 9600; do
  sudo ufw allow "${port}/tcp" || true
done

sudo systemctl daemon-reload
sudo systemctl enable --now pfe-pod-routes.service
sudo systemctl enable --now camunda8.service
sudo systemctl enable --now camunda8-rest-proxy.service
sudo systemctl enable --now pfe-ai-service.service
