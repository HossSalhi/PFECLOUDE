#!/usr/bin/env bash
# setup-tls-ai-service.sh
#
# Puts nginx in front of the AI service on VM .161 so that:
#   - The backend calls https://192.168.148.161:8443  (TLS)
#   - nginx terminates TLS and proxies to localhost:8000 (FastAPI, unchanged)
#
# Run on VM .161 (192.168.148.161) as root/sudo.
# Safe to re-run (idempotent).
#
# After running:
#   1. Update backend application.properties:
#      aiops.service.base-url=https://192.168.148.161:8443
#   2. For self-signed certs the Java RestClient needs to trust the CA:
#      Pass AIOPS_TLS_SKIP_VERIFY=true (dev only) or import the cert into the JVM truststore.
#
set -euo pipefail

CERT_DIR="/etc/nginx/ssl/helios"
CERT_FILE="${CERT_DIR}/ai-service.crt"
KEY_FILE="${CERT_DIR}/ai-service.key"
NGINX_CONF="/etc/nginx/sites-available/ai-service-tls"
NGINX_LINK="/etc/nginx/sites-enabled/ai-service-tls"
HTTPS_PORT="${HTTPS_PORT:-8443}"
UPSTREAM_PORT="${UPSTREAM_PORT:-8000}"
DAYS="${DAYS:-3650}"

# ── 1. Install nginx if absent ──────────────────────────────────────────────
if ! command -v nginx &>/dev/null; then
    apt-get update -qq && apt-get install -y -qq nginx
fi

# ── 2. Generate self-signed certificate ────────────────────────────────────
mkdir -p "${CERT_DIR}"
if [[ ! -f "${CERT_FILE}" ]]; then
    openssl req -x509 -newkey rsa:4096 -nodes \
        -keyout "${KEY_FILE}" \
        -out "${CERT_FILE}" \
        -days "${DAYS}" \
        -subj "/CN=helios-ai-service/O=PFE-Orange/C=TN" \
        -addext "subjectAltName=IP:192.168.148.161,DNS:helios-ai-service"
    chmod 600 "${KEY_FILE}"
    echo "TLS certificate generated: ${CERT_FILE}"
else
    echo "TLS certificate already exists — skipping generation."
fi

# ── 3. Write nginx site config ──────────────────────────────────────────────
cat > "${NGINX_CONF}" << EOF
# HELIOS AI service — TLS termination
# nginx listens on :${HTTPS_PORT} (HTTPS) and proxies to FastAPI on :${UPSTREAM_PORT} (HTTP).
# The X-API-Key and X-Request-ID headers are forwarded transparently.

server {
    listen ${HTTPS_PORT} ssl;
    server_name 192.168.148.161 helios-ai-service;

    ssl_certificate     ${CERT_FILE};
    ssl_certificate_key ${KEY_FILE};
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;
    ssl_session_cache   shared:SSL:10m;
    ssl_session_timeout 10m;

    # Limit request body to 10 MB (feature vectors are small)
    client_max_body_size 10m;

    location / {
        proxy_pass         http://127.0.0.1:${UPSTREAM_PORT};
        proxy_http_version 1.1;
        proxy_set_header   Host              \$host;
        proxy_set_header   X-Real-IP         \$remote_addr;
        proxy_set_header   X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto https;

        # Forward HELIOS service-to-service headers unchanged
        proxy_set_header   X-API-Key         \$http_x_api_key;
        proxy_set_header   X-Request-ID      \$http_x_request_id;

        proxy_read_timeout  60s;
        proxy_send_timeout  60s;
        proxy_connect_timeout 5s;
    }

    # Health probe — unauthenticated so K8s / monitoring can reach it
    location /health {
        proxy_pass http://127.0.0.1:${UPSTREAM_PORT}/health;
        proxy_http_version 1.1;
        proxy_read_timeout 5s;
    }
}
EOF

# ── 4. Enable site, disable default ────────────────────────────────────────
ln -sfn "${NGINX_CONF}" "${NGINX_LINK}"
rm -f /etc/nginx/sites-enabled/default 2>/dev/null || true

# ── 5. Validate config + reload ─────────────────────────────────────────────
nginx -t
if systemctl is-active --quiet nginx; then
    systemctl reload nginx
else
    systemctl enable --now nginx
fi

echo ""
echo "Done. AI service is now reachable at:"
echo "  https://192.168.148.161:${HTTPS_PORT}  (TLS, proxied to :${UPSTREAM_PORT})"
echo ""
echo "Next steps:"
echo "  1. Export the CA cert for the Java backend:"
echo "     scp root@192.168.148.161:${CERT_FILE} /tmp/helios-ai.crt"
echo "  2. Import into JVM truststore (or set AIOPS_TLS_SKIP_VERIFY=true for dev):"
echo "     keytool -importcert -alias helios-ai -file /tmp/helios-ai.crt \\"
echo "             -keystore \${JAVA_HOME}/lib/security/cacerts -storepass changeit -noprompt"
echo "  3. Set in K8s secret: AIOPS_SERVICE_BASE_URL=https://192.168.148.161:${HTTPS_PORT}"
