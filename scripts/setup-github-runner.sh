#!/usr/bin/env bash
# Install a self-hosted GitHub Actions runner on the K8s master VM.
# This fixes the C-1 CD limitation: GitHub Actions can't SSH into the private LAN,
# but the runner connects OUT to GitHub — no inbound port needed.
#
# Run ON the master VM (192.168.148.153) as the kube user:
#   scp scripts/setup-github-runner.sh kube@192.168.148.153:/tmp/
#   ssh kube@192.168.148.153 "bash /tmp/setup-github-runner.sh"
#
# You will be prompted for:
#   GITHUB_OWNER  — your GitHub org or username (e.g. SALHIHoussam)
#   GITHUB_REPO   — repository name (e.g. PFE-Orange)
#   RUNNER_TOKEN  — registration token from:
#                   https://github.com/<owner>/<repo>/settings/actions/runners/new
#
set -euo pipefail

RUNNER_VERSION="2.317.0"
RUNNER_USER="${RUNNER_USER:-kube}"
RUNNER_DIR="/opt/actions-runner"
RUNNER_NAME="${RUNNER_NAME:-kubernetes-master}"
RUNNER_LABELS="self-hosted,linux,x64,kubernetes-master"
SERVICE_NAME="github-runner"

# ── Collect config ───────────────────────────────────────────────────────────
if [[ -z "${GITHUB_OWNER:-}" ]]; then
  read -rp "GitHub owner (user/org): " GITHUB_OWNER
fi
if [[ -z "${GITHUB_REPO:-}" ]]; then
  read -rp "GitHub repository name : " GITHUB_REPO
fi
if [[ -z "${RUNNER_TOKEN:-}" ]]; then
  echo "Get a runner token at: https://github.com/${GITHUB_OWNER}/${GITHUB_REPO}/settings/actions/runners/new"
  read -rp "Runner registration token: " RUNNER_TOKEN
fi

echo
echo "Installing GitHub Actions self-hosted runner"
echo "  Repo    : ${GITHUB_OWNER}/${GITHUB_REPO}"
echo "  Name    : ${RUNNER_NAME}"
echo "  Labels  : ${RUNNER_LABELS}"
echo "  Dir     : ${RUNNER_DIR}"
echo

# ── Install runner ────────────────────────────────────────────────────────────
sudo mkdir -p "$RUNNER_DIR"
sudo chown "$RUNNER_USER:$RUNNER_USER" "$RUNNER_DIR"

cd "$RUNNER_DIR"

ARCH="x64"
TAR="actions-runner-linux-${ARCH}-${RUNNER_VERSION}.tar.gz"

if [[ ! -f "$TAR" ]]; then
  echo "[1/4] Downloading runner ${RUNNER_VERSION}..."
  curl -fsSL \
    "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/${TAR}" \
    -o "$TAR"
fi

echo "[2/4] Extracting..."
tar xzf "$TAR" --overwrite

echo "[3/4] Configuring runner..."
sudo -u "$RUNNER_USER" ./config.sh \
  --url "https://github.com/${GITHUB_OWNER}/${GITHUB_REPO}" \
  --token "$RUNNER_TOKEN" \
  --name "$RUNNER_NAME" \
  --labels "$RUNNER_LABELS" \
  --work "_work" \
  --unattended \
  --replace

echo "[4/4] Installing as systemd service..."
sudo ./svc.sh install "$RUNNER_USER"
sudo ./svc.sh start

echo
echo "Runner '${RUNNER_NAME}' registered and running."
echo "Status: sudo systemctl status ${SERVICE_NAME}"
echo
echo "In GitHub Actions, use this runner with:"
echo "  runs-on: [self-hosted, kubernetes-master]"
echo
echo "The deploy job in backend-ci.yml will now run directly on this VM,"
echo "eliminating the need for SSH access from GitHub to the private LAN."
