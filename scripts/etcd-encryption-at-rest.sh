#!/usr/bin/env bash
# etcd-encryption-at-rest.sh
#
# Enables AES-256-CBC encryption for secrets stored in etcd on a kubeadm cluster.
# Run ONCE on the master node (192.168.148.153) as root/sudo.
#
# What it does:
#   1. Generates a 32-byte random key (base64-encoded)
#   2. Writes /etc/kubernetes/enc/encryption-config.yaml
#   3. Patches the kube-apiserver static pod manifest to load the config
#   4. Waits for the apiserver to restart
#   5. Triggers a re-encryption pass over all existing Secrets
#
# Safe to re-run: key generation is skipped if the config file already exists.
#
# Verify afterwards:
#   ETCDCTL_API=3 etcdctl get /registry/secrets/default/test-secret \
#     --endpoints=https://127.0.0.1:2379 \
#     --cacert=/etc/kubernetes/pki/etcd/ca.crt \
#     --cert=/etc/kubernetes/pki/etcd/server.crt \
#     --key=/etc/kubernetes/pki/etcd/server.key \
#     | head -c 20   # should start with "k8s:enc:aescbc:v1"
#
set -euo pipefail

ENC_DIR="/etc/kubernetes/enc"
ENC_CFG="${ENC_DIR}/encryption-config.yaml"
APISERVER_MANIFEST="/etc/kubernetes/manifests/kube-apiserver.yaml"

# ── 1. Generate key ───────────────────────────────────────────────────────────
if [[ -f "${ENC_CFG}" ]]; then
    echo "[1/5] Encryption config already exists — skipping key generation"
else
    echo "[1/5] Generating AES-256 encryption key..."
    mkdir -p "${ENC_DIR}"
    chmod 700 "${ENC_DIR}"
    KEY=$(head -c 32 /dev/urandom | base64)

    cat > "${ENC_CFG}" <<EOF
apiVersion: apiserver.config.k8s.io/v1
kind: EncryptionConfiguration
resources:
  - resources:
      - secrets
    providers:
      - aescbc:
          keys:
            - name: helios-key-1
              secret: ${KEY}
      - identity: {}
EOF

    chmod 600 "${ENC_CFG}"
    echo "    Key written to ${ENC_CFG}"
fi

# ── 2. Check apiserver manifest ───────────────────────────────────────────────
echo "[2/5] Checking kube-apiserver manifest..."

if grep -q "encryption-provider-config" "${APISERVER_MANIFEST}"; then
    echo "    encryption-provider-config already present — skipping patch"
else
    echo "[2/5] Patching kube-apiserver manifest..."
    # Add the --encryption-provider-config flag
    sed -i '/--enable-admission-plugins/a\    - --encryption-provider-config=/etc/kubernetes/enc/encryption-config.yaml' \
        "${APISERVER_MANIFEST}"

    # Mount the enc directory if not already mounted
    if ! grep -q "/etc/kubernetes/enc" "${APISERVER_MANIFEST}"; then
        # Insert volumeMount for enc dir after an existing hostPath mount
        python3 - <<'PYEOF'
import sys, re

with open("/etc/kubernetes/manifests/kube-apiserver.yaml") as f:
    content = f.read()

vm_block = """    - mountPath: /etc/kubernetes/enc
      name: enc-config
      readOnly: true"""

vol_block = """  - hostPath:
      path: /etc/kubernetes/enc
      type: DirectoryOrCreate
    name: enc-config"""

# Insert volumeMount before "volumes:" section marker
content = content.replace(
    "  volumes:",
    vm_block + "\n  volumes:",
    1
)
# Append volume definition after the last volume entry
content += "\n" + vol_block + "\n"

with open("/etc/kubernetes/manifests/kube-apiserver.yaml", "w") as f:
    f.write(content)
print("    Manifest patched with enc volume/mount")
PYEOF
    fi
fi

# ── 3. Wait for apiserver restart ─────────────────────────────────────────────
echo "[3/5] Waiting for kube-apiserver to restart (up to 90s)..."
for i in $(seq 1 18); do
    if kubectl get nodes --request-timeout=5s > /dev/null 2>&1; then
        echo "    apiserver is up"
        break
    fi
    echo "    Waiting ($((i*5))s)..."
    sleep 5
done

# ── 4. Verify apiserver loaded the config ─────────────────────────────────────
echo "[4/5] Verifying apiserver encryption config flag..."
if kubectl get pod -n kube-system -l component=kube-apiserver -o jsonpath='{.items[0].spec.containers[0].command}' \
       2>/dev/null | grep -q "encryption-provider-config"; then
    echo "    OK — encryption-provider-config is active"
else
    echo "    WARNING: could not confirm flag — check apiserver logs manually"
fi

# ── 5. Re-encrypt all existing Secrets ───────────────────────────────────────
echo "[5/5] Re-encrypting all existing Secrets in all namespaces..."
kubectl get secrets --all-namespaces -o json \
    | kubectl replace -f - \
    || echo "    Some secrets may have been skipped (namespace-specific RBAC?)"

echo ""
echo "Done. Verify with:"
echo "  ETCDCTL_API=3 etcdctl get /registry/secrets/default/<name> \\"
echo "    --endpoints=https://127.0.0.1:2379 \\"
echo "    --cacert=/etc/kubernetes/pki/etcd/ca.crt \\"
echo "    --cert=/etc/kubernetes/pki/etcd/server.crt \\"
echo "    --key=/etc/kubernetes/pki/etcd/server.key \\"
echo "    | head -c 30   # expect: k8s:enc:aescbc:v1:helios-key-1"
