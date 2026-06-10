#!/usr/bin/env bash
# Generate cryptographically random production secrets for HELIOS.
# Outputs a ready-to-apply K8s secret YAML — review, then pipe to kubectl apply.
#
# Usage:
#   bash scripts/generate-production-secrets.sh                   # print YAML
#   bash scripts/generate-production-secrets.sh | kubectl apply -f -
#   bash scripts/generate-production-secrets.sh --apply           # apply directly
#
set -euo pipefail

APPLY=false
while [[ $# -gt 0 ]]; do
  case $1 in
    --apply) APPLY=true; shift ;;
    *) echo "Unknown flag: $1"; exit 1 ;;
  esac
done

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || { echo "ERROR: '$1' is required but not found"; exit 1; }
}
require_cmd openssl

rand_base64()  { openssl rand -base64 "${1:-32}"; }
rand_pass()    { openssl rand -base64 24 | tr -d '+/=' | head -c "${1:-32}"; }
rand_hex()     { openssl rand -hex "${1:-16}"; }

JWT_SECRET=$(rand_base64 32)
SUPER_ADMIN_PASSWORD=$(rand_pass 28)
ADMIN_PASSWORD=$(rand_pass 24)
OPERATOR_PASSWORD=$(rand_pass 24)
DB_PASSWORD=$(rand_pass 20)
BACKEND_API_PASSWORD=$(rand_pass 24)

echo "# HELIOS production secrets — generated $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "# Review before applying. Store in a vault (Bitwarden, HashiCorp Vault, etc.)"
echo "#"
echo "# Generated values (save these somewhere safe):"
echo "#   JWT_SECRET            : ${JWT_SECRET}"
echo "#   SUPER_ADMIN_PASSWORD  : ${SUPER_ADMIN_PASSWORD}"
echo "#   ADMIN_PASSWORD        : ${ADMIN_PASSWORD}"
echo "#   OPERATOR_PASSWORD     : ${OPERATOR_PASSWORD}"
echo "#   DB_PASSWORD           : ${DB_PASSWORD}"
echo "#   BACKEND_API_PASSWORD  : ${BACKEND_API_PASSWORD}"
echo "---"

YAML=$(cat <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: backend-secret
  namespace: pfe
type: Opaque
stringData:
  SPRING_DATASOURCE_PASSWORD: "${DB_PASSWORD}"
  BACKEND_API_USERNAME: "operator"
  BACKEND_API_PASSWORD: "${BACKEND_API_PASSWORD}"
  CAMUNDA_USERNAME: ""
  CAMUNDA_PASSWORD: ""
  SUPER_ADMIN_PASSWORD: "${SUPER_ADMIN_PASSWORD}"
  ADMIN_PASSWORD: "${ADMIN_PASSWORD}"
  USER_PASSWORD: "${OPERATOR_PASSWORD}"
  JWT_SECRET: "${JWT_SECRET}"
  MAIL_HOST: "192.168.148.161"
  MAIL_PORT: "1025"
  MAIL_USERNAME: ""
  MAIL_PASSWORD: ""
  NOTIFICATION_EMAIL_TO: "houssamsalhi972@gmail.com"
  SUPER_ADMIN_EMAIL: "houssamsalhi972@gmail.com"
EOF
)

echo "$YAML"

if [ "$APPLY" = true ]; then
  echo "---"
  echo "Applying secret..."
  echo "$YAML" | kubectl apply -f -
  echo "Secret 'backend-secret' applied in namespace 'pfe'."
  echo "Restart backend pods to pick up new credentials:"
  echo "  kubectl rollout restart deployment/backend -n pfe"
fi
