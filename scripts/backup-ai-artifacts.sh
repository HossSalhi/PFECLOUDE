#!/usr/bin/env bash
# backup-ai-artifacts.sh
#
# Backs up AI model artifacts and ChromaDB data from VM .161 to MinIO on .160.
# Run on VM .161 (192.168.148.161) — schedule via cron or systemd timer.
#
# Prerequisites on .161:
#   mc (MinIO client): curl -O https://dl.min.io/client/mc/release/linux-amd64/mc && chmod +x mc
#   Or: apt install minio-client
#
# Cron (daily at 02:00):
#   0 2 * * * /opt/helios/scripts/backup-ai-artifacts.sh >> /var/log/helios-backup.log 2>&1
#
set -euo pipefail

MINIO_ALIAS="helios-minio"
MINIO_URL="http://192.168.148.160:9000"
MINIO_ACCESS_KEY="${MINIO_ACCESS_KEY:-minioadmin}"
MINIO_SECRET_KEY="${MINIO_SECRET_KEY:-minioadmin}"
BUCKET="ai-artifacts-backup"
TIMESTAMP=$(date +%Y%m%d-%H%M%S)

AI_SERVICE_DATA_DIR="${AI_SERVICE_DATA_DIR:-/opt/helios/ai-service}"
MODEL_ARTIFACTS_DIR="${AI_SERVICE_DATA_DIR}/model_artifacts"
CHROMA_DIR="${AI_SERVICE_DATA_DIR}/data/chroma"
DATASETS_DIR="${AI_SERVICE_DATA_DIR}/datasets"

BACKUP_DIR="/tmp/helios-ai-backup-${TIMESTAMP}"
ARCHIVE="/tmp/helios-ai-artifacts-${TIMESTAMP}.tar.gz"

echo "[$(date -Iseconds)] Starting AI artifacts backup..."

# ── 1. Create local archive ───────────────────────────────────────────────────
mkdir -p "${BACKUP_DIR}"

[[ -d "${MODEL_ARTIFACTS_DIR}" ]] && cp -r "${MODEL_ARTIFACTS_DIR}" "${BACKUP_DIR}/model_artifacts"
[[ -d "${CHROMA_DIR}" ]]          && cp -r "${CHROMA_DIR}"          "${BACKUP_DIR}/chroma"
[[ -d "${DATASETS_DIR}" ]]        && cp -r "${DATASETS_DIR}"        "${BACKUP_DIR}/datasets"

tar -czf "${ARCHIVE}" -C "$(dirname "${BACKUP_DIR}")" "$(basename "${BACKUP_DIR}")"
echo "  Archive: ${ARCHIVE} ($(du -sh "${ARCHIVE}" | cut -f1))"

# ── 2. Upload to MinIO ────────────────────────────────────────────────────────
mc alias set "${MINIO_ALIAS}" "${MINIO_URL}" "${MINIO_ACCESS_KEY}" "${MINIO_SECRET_KEY}" --quiet
mc mb --ignore-existing "${MINIO_ALIAS}/${BUCKET}"
mc cp "${ARCHIVE}" "${MINIO_ALIAS}/${BUCKET}/$(basename "${ARCHIVE}")"
echo "  Uploaded to s3://${BUCKET}/$(basename "${ARCHIVE}")"

# ── 3. Prune backups older than 7 days ───────────────────────────────────────
mc rm --recursive --force --older-than 7d "${MINIO_ALIAS}/${BUCKET}/" 2>/dev/null || true
echo "  Old backups pruned (>7d)"

# ── 4. Cleanup local temp files ──────────────────────────────────────────────
rm -rf "${BACKUP_DIR}" "${ARCHIVE}"

echo "[$(date -Iseconds)] AI artifacts backup completed successfully"
