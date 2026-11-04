#!/usr/bin/env bash
# HELIOS HA-01 — sauvegarde HORS CLUSTER sur .161 (indépendante de .155, .158 et .160).
# Chaque exécution (timer quotidien 03:00 UTC, après le snapshot etcd de 01:30 et le CronJob de 02:00) :
#   1. repère un nœud dont l'API répond (.158, .155, puis .153 en dernier recours)
#   2. dump SQL gzip de pfe_db pris sur le PRIMAIRE courant   -> offsite/pg/        (validé gzip -t + taille)
#   3. snapshot etcd FRAIS pris sur ce nœud puis copié          -> offsite/etcd/      (taille + sha256)
#   4. copie (ajout seulement) des dumps du CronJob A si .158 est vivant -> offsite/cronjob-a/
#   5. rétention : ne supprime QUE les fichiers de pg/ et etcd/ créés par ce script au-delà des 30 derniers
# Ne supprime JAMAIS rien d'autre ; n'écrit rien sur les nœuds hormis un snapshot etcd horodaté ha01-offsite-*.
# Code retour 0 = tout OK ; 1 = au moins une étape en échec (journalisé) ; 2 = aucun nœud joignable.
set -uo pipefail
BASE=/opt/helios-backups/ha01/offsite
KH=/opt/helios-backups/ha01/known_hosts
NODES=(192.168.148.158 192.168.148.155 192.168.148.153)
SSHO=(-o BatchMode=yes -o ConnectTimeout=6 -o UserKnownHostsFile="$KH" -o StrictHostKeyChecking=yes -o LogLevel=ERROR)
TS=$(date -u +%Y%m%dT%H%M%SZ)
RC=0
log() { echo "$(date -u +%FT%TZ) $*"; }
mkdir -p "$BASE/pg" "$BASE/etcd" "$BASE/cronjob-a"
[ -s "$KH" ] || { log "known_hosts absent ($KH) : lancer install-offsite-backup.sh"; exit 2; }

LIVE=""
for n in "${NODES[@]}"; do
  if ssh "${SSHO[@]}" kube@"$n" 'kubectl get --raw=/readyz --request-timeout=8s' >/dev/null 2>&1; then LIVE="$n"; break; fi
done
[ -n "$LIVE" ] || { log "AUCUN noeud avec API vivante"; exit 2; }
log "noeud de reference: $LIVE"

# --- 2. dump SQL
PRIM=$(ssh "${SSHO[@]}" kube@"$LIVE" "kubectl -n pfe get pods -l cnpg.io/instanceRole=primary -o jsonpath='{.items[0].metadata.name}' --request-timeout=15s" 2>/dev/null || true)
if [ -n "$PRIM" ]; then
  F="$BASE/pg/pfe_db-$TS.sql.gz"
  if ssh "${SSHO[@]}" kube@"$LIVE" "kubectl -n pfe exec $PRIM -c postgres --request-timeout=120s -- pg_dump -U postgres -d pfe_db --no-owner --no-privileges" | gzip -9 > "$F.partial" \
     && gzip -t "$F.partial" && [ "$(stat -c %s "$F.partial")" -gt 10240 ]; then
    mv "$F.partial" "$F"; log "pg_dump OK ($PRIM) $(stat -c %s "$F") o"
  else
    rm -f "$F.partial"; log "ECHEC pg_dump"; RC=1
  fi
else
  log "ECHEC: aucun primaire PostgreSQL trouve"; RC=1
fi

# --- 3. snapshot etcd frais
SN="/var/backups/etcd/ha01-offsite-$TS.db"
if ssh "${SSHO[@]}" kube@"$LIVE" "sudo -n env ETCDCTL_API=3 etcdctl --endpoints=https://127.0.0.1:2379 --cacert=/etc/kubernetes/pki/etcd/ca.crt --cert=/etc/kubernetes/pki/etcd/server.crt --key=/etc/kubernetes/pki/etcd/server.key --command-timeout=90s snapshot save $SN >/dev/null 2>&1 && sudo -n chmod 600 $SN"; then
  OUT="$BASE/etcd/etcd-$TS-${LIVE##*.}.db"
  if ssh "${SSHO[@]}" kube@"$LIVE" "sudo -n cat $SN" > "$OUT.partial" && [ "$(stat -c %s "$OUT.partial")" -gt 10485760 ]; then
    mv "$OUT.partial" "$OUT"; sha256sum "$OUT" > "$OUT.sha256"; log "snapshot etcd OK $(stat -c %s "$OUT") o"
    # on supprime UNIQUEMENT le fichier temporaire que ce script vient de créer sur le nœud (jamais un autre snapshot)
    ssh "${SSHO[@]}" kube@"$LIVE" "sudo -n rm -f $SN" || true
  else
    rm -f "$OUT.partial"; log "ECHEC copie snapshot etcd"; RC=1
  fi
else
  log "ECHEC snapshot etcd sur $LIVE"; RC=1
fi

# --- 4. dumps du CronJob A (PVC sur .158), ajout seulement
if ssh "${SSHO[@]}" kube@192.168.148.158 true 2>/dev/null; then
  PVC=$(ssh "${SSHO[@]}" kube@192.168.148.158 "sudo -n ls -d /opt/local-path-provisioner/*_pfe_postgres-backups 2>/dev/null | head -1")
  if [ -n "$PVC" ]; then
    ssh "${SSHO[@]}" kube@192.168.148.158 "sudo -n tar -C $PVC -cf - ." | tar -C "$BASE/cronjob-a" --keep-old-files -xf - 2>/dev/null
    log "dumps CronJob A synchronises ($(ls "$BASE/cronjob-a" | wc -l) fichiers)"
  fi
else
  log "info: .158 injoignable, dumps CronJob A non synchronises cette fois"
fi

# --- 5. retention (uniquement nos fichiers pg/ et etcd/)
ls -1t "$BASE"/pg/pfe_db-*.sql.gz 2>/dev/null | tail -n +31 | xargs -r rm -f
ls -1t "$BASE"/etcd/etcd-*.db 2>/dev/null | tail -n +31 | while read -r f; do rm -f "$f" "$f.sha256"; done
log "terminé rc=$RC"
exit $RC
