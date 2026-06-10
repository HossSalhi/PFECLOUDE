#!/usr/bin/env bash
# Generate a self-signed TLS certificate for HELIOS and apply it as a K8s TLS secret.
# Run from any machine that has kubectl access to the cluster.
#
# Usage:
#   bash scripts/generate-tls-secret.sh
#   bash scripts/generate-tls-secret.sh --days 730   # 2-year cert
#
set -euo pipefail

NAMESPACE="pfe"
SECRET_NAME="helios-tls"
DAYS=365
SUBJECT="/CN=helios.orange.local/O=Orange/C=FR"
# Add all worker/master IPs + a friendly hostname
SAN="IP:192.168.148.153,IP:192.168.148.158,IP:192.168.148.155,DNS:helios.orange.local,DNS:frontend.pfe.svc.cluster.local"

while [[ $# -gt 0 ]]; do
  case $1 in
    --days) DAYS="$2"; shift 2 ;;
    --namespace) NAMESPACE="$2"; shift 2 ;;
    *) echo "Unknown flag: $1"; exit 1 ;;
  esac
done

TMP_KEY=$(mktemp)
TMP_CRT=$(mktemp)
TMP_EXT=$(mktemp)

trap 'rm -f "$TMP_KEY" "$TMP_CRT" "$TMP_EXT"' EXIT

# Build the SAN extension file
cat > "$TMP_EXT" <<EOF
[req]
req_extensions = v3_req
distinguished_name = dn

[dn]

[v3_req]
basicConstraints = CA:FALSE
keyUsage = nonRepudiation, digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = ${SAN}

[alt_names]
EOF

echo "Generating self-signed TLS certificate (${DAYS} days)..."
echo "  Subject  : ${SUBJECT}"
echo "  SANs     : ${SAN}"
echo "  Namespace: ${NAMESPACE}"
echo "  Secret   : ${SECRET_NAME}"
echo

openssl req -x509 \
  -newkey rsa:4096 \
  -keyout "$TMP_KEY" \
  -out    "$TMP_CRT" \
  -days   "$DAYS" \
  -nodes \
  -subj   "$SUBJECT" \
  -extensions v3_req \
  -config "$TMP_EXT" \
  2>/dev/null

# Apply to cluster (idempotent via --dry-run + apply)
kubectl -n "$NAMESPACE" create secret tls "$SECRET_NAME" \
  --cert="$TMP_CRT" \
  --key="$TMP_KEY" \
  --dry-run=client -o yaml | kubectl apply -f -

echo
echo "Done. Secret '${SECRET_NAME}' applied in namespace '${NAMESPACE}'."
echo
echo "Next steps:"
echo "  1. Apply the updated frontend-deployment.yaml (adds TLS volume mount + port 8443)"
echo "  2. Apply the updated frontend-service.yaml (exposes NodePort 30443 -> 8443)"
echo "  3. Access HELIOS at https://192.168.148.158:30443  (accept self-signed cert)"
echo
echo "Renew annually with:"
echo "  bash scripts/generate-tls-secret.sh --days 365"
