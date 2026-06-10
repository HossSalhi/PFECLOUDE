#!/usr/bin/env bash
# grafana-export-dashboards.sh
#
# Exports all Grafana dashboards as JSON files into the provisioning directory.
# On next Grafana start, dashboards are auto-loaded from these files — surviving
# a DB reset or container recreation.
#
# Run on VM .160 (192.168.148.160), or from any host with access to Grafana.
#
# Usage:
#   ./grafana-export-dashboards.sh                          # uses defaults
#   GRAFANA_URL=http://192.168.148.160:3000 \
#   GRAFANA_USER=admin GRAFANA_PASS=admin \
#     ./grafana-export-dashboards.sh
#
set -euo pipefail

GRAFANA_URL="${GRAFANA_URL:-http://192.168.148.160:3000}"
GRAFANA_USER="${GRAFANA_USER:-admin}"
GRAFANA_PASS="${GRAFANA_PASS:-admin}"
PROVISION_DIR="${PROVISION_DIR:-/etc/grafana/provisioning/dashboards}"
BACKUP_DIR="${BACKUP_DIR:-/opt/helios/grafana-dashboards-backup}"
TIMESTAMP=$(date +%Y%m%d-%H%M%S)

AUTH="${GRAFANA_USER}:${GRAFANA_PASS}"
EXPORT_DIR="${BACKUP_DIR}/export-${TIMESTAMP}"

mkdir -p "${EXPORT_DIR}"

echo "Exporting Grafana dashboards from ${GRAFANA_URL}..."

# ── 1. Fetch all dashboard UIDs ───────────────────────────────────────────────
DASHBOARDS=$(curl -sf -u "${AUTH}" \
    "${GRAFANA_URL}/api/search?type=dash-db&limit=500" \
    | python3 -c "import sys,json; [print(d['uid']+'|'+d.get('title','unknown')) for d in json.load(sys.stdin)]")

COUNT=0
while IFS='|' read -r uid title; do
    [[ -z "${uid}" ]] && continue
    SAFE_TITLE=$(echo "${title}" | tr ' /' '__' | tr -dc '[:alnum:]_-')
    OUTFILE="${EXPORT_DIR}/${SAFE_TITLE}.json"

    curl -sf -u "${AUTH}" \
        "${GRAFANA_URL}/api/dashboards/uid/${uid}" \
        | python3 -c "
import sys, json
data = json.load(sys.stdin)
# Strip server-side metadata so the dashboard can be re-imported cleanly
db = data['dashboard']
db.pop('id', None)
db.pop('version', None)
out = {'dashboard': db, 'overwrite': True, 'folderId': data.get('meta', {}).get('folderId', 0)}
print(json.dumps(out, indent=2))
" > "${OUTFILE}"

    echo "  Exported: ${title} → $(basename "${OUTFILE}")"
    COUNT=$((COUNT + 1))
done <<< "${DASHBOARDS}"

echo "Exported ${COUNT} dashboard(s) to ${EXPORT_DIR}"

# ── 2. Copy to provisioning directory (optional) ──────────────────────────────
if [[ -d "${PROVISION_DIR}" ]]; then
    cp "${EXPORT_DIR}"/*.json "${PROVISION_DIR}/" 2>/dev/null || true
    echo "Copied to provisioning dir: ${PROVISION_DIR}"
    echo "Grafana will auto-load these on next restart."
else
    echo "Provisioning dir ${PROVISION_DIR} not found — skipping copy."
    echo "To enable auto-provisioning, create ${PROVISION_DIR} and add:"
    cat << 'EOF'
# /etc/grafana/provisioning/dashboards/helios.yaml
apiVersion: 1
providers:
  - name: helios
    type: file
    updateIntervalSeconds: 30
    options:
      path: /etc/grafana/provisioning/dashboards
EOF
fi

# ── 3. Latest symlink ─────────────────────────────────────────────────────────
ln -sfn "${EXPORT_DIR}" "${BACKUP_DIR}/latest"
echo "Backup complete → ${BACKUP_DIR}/latest"
