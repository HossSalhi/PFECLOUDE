#!/bin/bash
# etcd-backup-cron.sh — runs on k8s-master as root via cron (every 6h)
# Crontab (root): 0 */6 * * * /opt/etcd-backup/etcd-backup-cron.sh >> /var/log/etcd-backup.log 2>&1

set -euo pipefail

BACKUP_DIR="/opt/etcd-backups"
KEEP_DAYS=7
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
SNAPSHOT_FILE="${BACKUP_DIR}/etcd-${TIMESTAMP}.db"

mkdir -p "${BACKUP_DIR}"

# Find the etcd pod name
ETCD_POD=$(crictl pods --namespace kube-system --name etcd -q 2>/dev/null | head -1)
if [ -z "${ETCD_POD}" ]; then
    echo "[$(date -Iseconds)] ERROR: no etcd pod found — aborting backup"
    exit 1
fi

ETCD_CONTAINER=$(crictl ps --pod "${ETCD_POD}" -q 2>/dev/null | head -1)
if [ -z "${ETCD_CONTAINER}" ]; then
    echo "[$(date -Iseconds)] ERROR: no etcd container in pod ${ETCD_POD}"
    exit 1
fi

echo "[$(date -Iseconds)] Starting etcd snapshot → ${SNAPSHOT_FILE}"

crictl exec "${ETCD_CONTAINER}" etcdctl \
    --endpoints=https://127.0.0.1:2379 \
    --cacert=/etc/kubernetes/pki/etcd/ca.crt \
    --cert=/etc/kubernetes/pki/etcd/healthcheck-client.crt \
    --key=/etc/kubernetes/pki/etcd/healthcheck-client.key \
    snapshot save /tmp/etcd-snap-${TIMESTAMP}.db

# Copy snapshot out of the container to the host
crictl exec "${ETCD_CONTAINER}" cat /tmp/etcd-snap-${TIMESTAMP}.db > "${SNAPSHOT_FILE}"
crictl exec "${ETCD_CONTAINER}" rm -f /tmp/etcd-snap-${TIMESTAMP}.db

SIZE=$(du -sh "${SNAPSHOT_FILE}" | cut -f1)
echo "[$(date -Iseconds)] Snapshot saved: ${SNAPSHOT_FILE} (${SIZE})"

# Verify integrity
crictl exec "${ETCD_CONTAINER}" etcdctl snapshot status /tmp/etcd-snap-${TIMESTAMP}.db 2>/dev/null \
    || etcdctl snapshot status "${SNAPSHOT_FILE}" 2>/dev/null \
    && echo "[$(date -Iseconds)] Snapshot integrity OK" \
    || echo "[$(date -Iseconds)] WARNING: could not verify snapshot integrity"

# Rotate: delete backups older than KEEP_DAYS
find "${BACKUP_DIR}" -name "etcd-*.db" -mtime +${KEEP_DAYS} -delete
REMAINING=$(ls "${BACKUP_DIR}"/etcd-*.db 2>/dev/null | wc -l)
echo "[$(date -Iseconds)] Backup rotation done — ${REMAINING} snapshot(s) kept"
