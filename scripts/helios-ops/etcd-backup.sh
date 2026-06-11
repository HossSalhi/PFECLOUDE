#!/usr/bin/env bash
# HELIOS — Backup etcd automatisé (L19).
# DÉPLOYÉ ET VÉRIFIÉ sur le master (192.168.148.153) le 2026-06-11 :
#   /usr/local/bin/etcd-backup.sh  +  /etc/cron.d/etcd-backup (toutes les 6h)
#   1er backup réel : /opt/etcd-backups/etcd-20260611-091501.db (23 MB)
#
# Snapshot via `crictl exec` dans le conteneur etcd (image distroless, etcdctl inclus),
# écrit dans le hostPath /var/lib/etcd puis déplacé vers /opt/etcd-backups.
# Rotation : garde les KEEP derniers (28 = 7 jours x 4/jour).
#
# Restauration (en cas de désastre) :
#   etcdutl snapshot restore /opt/etcd-backups/etcd-<ts>.db --data-dir /var/lib/etcd-restore
#   puis pointer le static pod etcd sur le nouveau data-dir. Voir .restore/ du repo.
set -uo pipefail
BACKUP_DIR=/opt/etcd-backups
KEEP=28
LOG=/var/log/etcd-backup.log
CRICTL="crictl -r unix:///run/containerd/containerd.sock"
ts=$(date +%Y%m%d-%H%M%S)
mkdir -p "$BACKUP_DIR"
CID=$($CRICTL ps --name etcd -q 2>/dev/null | head -1)
if [ -z "$CID" ]; then echo "$(date) ERROR: pas de conteneur etcd" >>"$LOG"; exit 1; fi
TMP="/var/lib/etcd/_etcd-snap-$ts.db"
if $CRICTL exec "$CID" etcdctl \
     --endpoints=https://127.0.0.1:2379 \
     --cacert=/etc/kubernetes/pki/etcd/ca.crt \
     --cert=/etc/kubernetes/pki/etcd/server.crt \
     --key=/etc/kubernetes/pki/etcd/server.key \
     snapshot save "$TMP" >>"$LOG" 2>&1; then
  mv "$TMP" "$BACKUP_DIR/etcd-$ts.db"
  ls -1t "$BACKUP_DIR"/etcd-*.db 2>/dev/null | tail -n +$((KEEP+1)) | xargs -r rm -f
  echo "$(date) OK $BACKUP_DIR/etcd-$ts.db ($(du -h "$BACKUP_DIR/etcd-$ts.db" | cut -f1))" >>"$LOG"
else
  rm -f "$TMP"
  echo "$(date) ERROR: snapshot échoué" >>"$LOG"
  exit 1
fi
