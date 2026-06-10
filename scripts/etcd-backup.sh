#!/usr/bin/env bash
# etcd-backup.sh
#
# Creates a snapshot of the K8s etcd datastore and uploads it to MinIO on .160.
# Run on the MASTER node (192.168.148.153) as root/sudo.
#
# Install as a daily systemd timer:
#   cp scripts/etcd-backup.sh /usr/local/bin/etcd-backup.sh
#   chmod +x /usr/local/bin/etcd-backup.sh
#   Then create /etc/systemd/system/etcd-backup.{service,timer} (see bottom of this file)
#
# Or cron (daily at 01:00):
#   0 1 * * * root /usr/local/bin/etcd-backup.sh >> /var/log/etcd-backup.log 2>&1
#
set -euo pipefail

ETCDCTL=/usr/local/bin/etcdctl
ETCD_ENDPOINTS="https://127.0.0.1:2379"
ETCD_CACERT="/etc/kubernetes/pki/etcd/ca.crt"
ETCD_CERT="/etc/kubernetes/pki/etcd/server.crt"
ETCD_KEY="/etc/kubernetes/pki/etcd/server.key"

MINIO_ALIAS="helios-minio"
MINIO_URL="http://192.168.148.160:9000"
MINIO_ACCESS_KEY="${MINIO_ACCESS_KEY:-minioadmin}"
MINIO_SECRET_KEY="${MINIO_SECRET_KEY:-minioadmin}"
BUCKET="etcd-backups"
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
SNAPSHOT="/tmp/etcd-snapshot-${TIMESTAMP}.db"
RETAIN_DAYS=7

echo "[$(date -Iseconds)] Starting etcd snapshot..."

# ── 1. Create etcd snapshot ───────────────────────────────────────────────────
ETCDCTL_API=3 "${ETCDCTL}" snapshot save "${SNAPSHOT}" \
    --endpoints="${ETCD_ENDPOINTS}" \
    --cacert="${ETCD_CACERT}" \
    --cert="${ETCD_CERT}" \
    --key="${ETCD_KEY}"

ETCDCTL_API=3 "${ETCDCTL}" snapshot status "${SNAPSHOT}" --write-out=table
echo "  Snapshot: ${SNAPSHOT} ($(du -sh "${SNAPSHOT}" | cut -f1))"

# ── 2. Upload to MinIO ────────────────────────────────────────────────────────
mc alias set "${MINIO_ALIAS}" "${MINIO_URL}" "${MINIO_ACCESS_KEY}" "${MINIO_SECRET_KEY}" --quiet
mc mb --ignore-existing "${MINIO_ALIAS}/${BUCKET}"
mc cp "${SNAPSHOT}" "${MINIO_ALIAS}/${BUCKET}/$(basename "${SNAPSHOT}")"
echo "  Uploaded to s3://${BUCKET}/$(basename "${SNAPSHOT}")"

# ── 3. Prune old snapshots ────────────────────────────────────────────────────
mc rm --recursive --force --older-than "${RETAIN_DAYS}d" "${MINIO_ALIAS}/${BUCKET}/" 2>/dev/null || true
echo "  Old snapshots pruned (>${RETAIN_DAYS}d)"

# ── 4. Cleanup ────────────────────────────────────────────────────────────────
rm -f "${SNAPSHOT}"
echo "[$(date -Iseconds)] etcd backup completed successfully"

# ── systemd unit files (copy-paste to create) ─────────────────────────────────
# /etc/systemd/system/etcd-backup.service:
# [Unit]
# Description=etcd snapshot backup to MinIO
# [Service]
# Type=oneshot
# ExecStart=/usr/local/bin/etcd-backup.sh
# StandardOutput=journal
# StandardError=journal
#
# /etc/systemd/system/etcd-backup.timer:
# [Unit]
# Description=Daily etcd backup at 01:00
# [Timer]
# OnCalendar=*-*-* 01:00:00
# Persistent=true
# [Install]
# WantedBy=timers.target
#
# Enable: systemctl daemon-reload && systemctl enable --now etcd-backup.timer
