#!/usr/bin/env bash
# Generate cryptographically random production secrets for HELIOS.
# Outputs a ready-to-apply K8s secret YAML — review, then pipe to kubectl apply.
#
# Usage:
#   bash scripts/generate-production-secrets.sh                   # print YAML
#   bash scripts/generate-production-secrets.sh | kubectl apply -f -
#   bash scripts/generate-production-secrets.sh --apply --rotate  # apply directly
#
# ATTENTION : ce script GENERE DE NOUVEAUX mots de passe et un nouveau JWT_SECRET (sessions invalidees, mot de passe
# de base de donnees a aligner sur CNPG). --apply exige donc --rotate : confirmation volontaire de la rotation.
# Les cles listees dans PRESERVE_KEYS (jetons Bridge, Falco, scrape, watchdog, cle API IA...) ne sont JAMAIS generees
# ni affichees : leur valeur est relue dans le cluster avant l'apply et restauree si l'apply l'avait supprimee.
#
set -euo pipefail
set +x   # jamais de trace des commandes : aucune valeur ne doit apparaitre dans un log

APPLY=false
ROTATE=false
while [[ $# -gt 0 ]]; do
  case $1 in
    --apply) APPLY=true; shift ;;
    --rotate) ROTATE=true; shift ;;
    *) echo "Unknown flag: $1"; exit 1 ;;
  esac
done

if [ "$APPLY" = true ] && [ "$ROTATE" != true ]; then
  echo "ERROR: --apply REMPLACE les mots de passe de production par de nouvelles valeurs aleatoires." >&2
  echo "       Ajoutez --rotate pour confirmer, ou omettez --apply pour seulement afficher le YAML." >&2
  exit 2
fi

# Cles de backend-secret NON produites par ce script (noms seulement ; valeurs jamais lues en clair ni affichees).
PRESERVE_KEYS=(
  HELIOS_AI_ASSIST_API_KEY
  HELIOS_BRIDGE_API_KEY
  HELIOS_BRIDGE_TOKEN
  HELIOS_FALCO_WEBHOOK_TOKEN
  HELIOS_METRICS_SCRAPE_TOKEN
  REMEDIATION_MASTER_WATCHDOG_TOKEN
)

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
echo "# Cles conservees, jamais regenerees ni affichees : ${PRESERVE_KEYS[*]}"
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
  require_cmd kubectl
  # Valeurs encodees (base64, jamais decodees ni affichees) des cles a preserver, relevees AVANT l'apply.
  declare -A KEPT=()
  for key in "${PRESERVE_KEYS[@]}"; do
    KEPT[$key]="$(kubectl -n pfe get secret backend-secret -o "jsonpath={.data.${key}}" 2>/dev/null || true)"
  done
  echo "---"
  echo "Applying secret..."
  echo "$YAML" | kubectl apply -f -
  # Verification : toute cle preservee disparue est restauree (patch lu sur l'entree standard : valeur absente des arguments).
  for key in "${PRESERVE_KEYS[@]}"; do
    [ -n "${KEPT[$key]}" ] || continue
    now="$(kubectl -n pfe get secret backend-secret -o "jsonpath={.data.${key}}" 2>/dev/null || true)"
    if [ -z "$now" ]; then
      printf '{"data":{"%s":"%s"}}' "$key" "${KEPT[$key]}" \
        | kubectl -n pfe patch secret backend-secret --type=merge --patch-file /dev/stdin >/dev/null \
        || { echo "ERROR: impossible de restaurer la cle '$key'" >&2; exit 1; }
      echo "Cle preservee restauree : $key"
    else
      echo "Cle preservee intacte   : $key"
    fi
  done
  echo "Secret 'backend-secret' applied in namespace 'pfe'."
  echo "Restart backend pods to pick up new credentials:"
  echo "  kubectl rollout restart deployment/backend -n pfe"
fi
