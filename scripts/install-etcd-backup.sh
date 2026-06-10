#!/bin/bash
# install-etcd-backup.sh — run once on k8s-master to install the cron backup
# Usage: ssh kube@192.168.148.153 "bash -s" < scripts/install-etcd-backup.sh

set -euo pipefail

BACKUP_SCRIPT="/opt/etcd-backup/etcd-backup-cron.sh"
CRON_JOB="0 */6 * * * root ${BACKUP_SCRIPT} >> /var/log/etcd-backup.log 2>&1"

echo "kube" | sudo -S bash -c "
  mkdir -p /opt/etcd-backup /opt/etcd-backups
  # Install the backup script
  cat > ${BACKUP_SCRIPT} << 'EOFSCRIPT'
$(cat /dev/stdin)
EOFSCRIPT
  chmod +x ${BACKUP_SCRIPT}
  # Install cron job
  echo '${CRON_JOB}' > /etc/cron.d/etcd-backup
  chmod 644 /etc/cron.d/etcd-backup
  echo 'Cron job installed: every 6 hours'
  # Run immediately for first backup
  ${BACKUP_SCRIPT}
"
