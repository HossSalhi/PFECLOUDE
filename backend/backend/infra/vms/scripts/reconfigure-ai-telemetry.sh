#!/usr/bin/env bash
set -euo pipefail

MONITORING_VM_IP="${MONITORING_VM_IP:-192.168.148.160}"
TELEMETRY_PROMETHEUS_HOST="${TELEMETRY_PROMETHEUS_HOST:-${MONITORING_VM_IP}}"
TELEMETRY_PROMETHEUS_PORT="${TELEMETRY_PROMETHEUS_PORT:-9090}"
TELEMETRY_LOKI_HOST="${TELEMETRY_LOKI_HOST:-${MONITORING_VM_IP}}"
TELEMETRY_LOKI_PORT="${TELEMETRY_LOKI_PORT:-3100}"
AI_SERVICE_UNIT="${AI_SERVICE_UNIT:-pfe-ai-service.service}"

unit_path="/etc/systemd/system/${AI_SERVICE_UNIT}"

if [ ! -f "${unit_path}" ]; then
  echo "Missing systemd unit: ${unit_path}" >&2
  exit 1
fi

sudo sed -i \
  -e "s|^Environment=AIOPS_LOKI_BASE_URL=.*|Environment=AIOPS_LOKI_BASE_URL=http://${TELEMETRY_LOKI_HOST}:${TELEMETRY_LOKI_PORT}|" \
  -e "s|^Environment=AIOPS_PROMETHEUS_BASE_URL=.*|Environment=AIOPS_PROMETHEUS_BASE_URL=http://${TELEMETRY_PROMETHEUS_HOST}:${TELEMETRY_PROMETHEUS_PORT}|" \
  "${unit_path}"

sudo systemctl daemon-reload
sudo systemctl restart "${AI_SERVICE_UNIT}"
sudo systemctl status "${AI_SERVICE_UNIT}" --no-pager

echo
echo "AI telemetry sources updated:"
echo "  Prometheus: http://${TELEMETRY_PROMETHEUS_HOST}:${TELEMETRY_PROMETHEUS_PORT}"
echo "  Loki:       http://${TELEMETRY_LOKI_HOST}:${TELEMETRY_LOKI_PORT}"
