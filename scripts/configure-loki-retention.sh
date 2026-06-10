#!/usr/bin/env bash
# configure-loki-retention.sh
#
# Enables log retention in Loki on VM .160 so the disk does not fill up.
# Default: keep logs for 15 days.
#
# Run on VM .160 (192.168.148.160) as root/sudo.
# Safe to re-run (idempotent).
#
set -euo pipefail

LOKI_CONFIG="${LOKI_CONFIG:-/etc/loki/loki-config.yaml}"
RETAIN_DAYS="${RETAIN_DAYS:-15}"
RETAIN_PERIOD="${RETAIN_DAYS}d"

if [[ ! -f "${LOKI_CONFIG}" ]]; then
    # Try common alternative paths
    for path in /etc/loki/config.yaml /opt/loki/loki-config.yaml /data/loki/loki-config.yaml; do
        [[ -f "${path}" ]] && { LOKI_CONFIG="${path}"; break; }
    done
fi

if [[ ! -f "${LOKI_CONFIG}" ]]; then
    echo "ERROR: Loki config not found. Set LOKI_CONFIG=/path/to/loki-config.yaml"
    exit 1
fi

echo "Patching Loki config: ${LOKI_CONFIG}"
cp "${LOKI_CONFIG}" "${LOKI_CONFIG}.bak.$(date +%Y%m%d%H%M%S)"

# ── Enable compactor retention ───────────────────────────────────────────────
if grep -q "retention_enabled" "${LOKI_CONFIG}"; then
    sed -i "s/retention_enabled:.*/retention_enabled: true/" "${LOKI_CONFIG}"
else
    # Add compactor block if absent
    cat >> "${LOKI_CONFIG}" << EOF

compactor:
  working_directory: /data/loki/boltdb-shipper-compactor
  retention_enabled: true
  retention_delete_delay: 2h
  retention_delete_worker_count: 150
EOF
fi

# ── Set global retention period in limits_config ─────────────────────────────
if grep -q "retention_period" "${LOKI_CONFIG}"; then
    sed -i "s/retention_period:.*/retention_period: ${RETAIN_PERIOD}/" "${LOKI_CONFIG}"
else
    if grep -q "^limits_config:" "${LOKI_CONFIG}"; then
        sed -i "/^limits_config:/a\\  retention_period: ${RETAIN_PERIOD}" "${LOKI_CONFIG}"
    else
        cat >> "${LOKI_CONFIG}" << EOF

limits_config:
  retention_period: ${RETAIN_PERIOD}
  enforce_metric_name: false
  reject_old_samples: true
  reject_old_samples_max_age: 168h
EOF
    fi
fi

echo "Retention set to ${RETAIN_PERIOD}"

# ── Restart Loki ─────────────────────────────────────────────────────────────
if systemctl is-active --quiet loki 2>/dev/null; then
    systemctl restart loki
    echo "Loki restarted (systemd)"
elif command -v docker &>/dev/null && docker ps --format '{{.Names}}' | grep -q loki; then
    docker restart loki
    echo "Loki restarted (docker)"
else
    echo "WARNING: Could not detect Loki process manager — restart Loki manually to apply config"
fi

echo "Done. Verify: curl -s http://localhost:3100/ready"
