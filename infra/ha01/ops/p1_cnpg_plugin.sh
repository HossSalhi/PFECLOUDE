#!/usr/bin/env bash
# HELIOS HA-01 — C7 : installe le plugin kubectl-cnpg 1.28.4 (aligné sur l'opérateur) dans /home/kube/bin de .155 et .158.
# Sert au switchover contrôlé de PostgreSQL (kubectl-cnpg promote) pendant les répétitions. Aucun effet sur le cluster.
# Intégrité : empreinte SHA-256 du tar.gz comparée à celle publiée par la release GitHub (digest de l'asset).
# Usage : ./p1_cnpg_plugin.sh <chemin du kubectl-cnpg_1.28.4_linux_x86_64.tar.gz>
set -euo pipefail
TGZ="${1:?usage: $0 <tar.gz>}"
EXPECT="9c7d206ee5872c411f693f2c6ff4402a22acaf415aae60c98fb2e2de034a532f"
GOT=$(sha256sum "$TGZ" | awk '{print $1}')
[ "$GOT" = "$EXPECT" ] || { echo "SHA-256 inattendu: $GOT"; exit 3; }
echo "SHA-256 conforme à la release officielle."
for ip in 155 158; do
  cat "$TGZ" | ssh -o BatchMode=yes kube@192.168.148.$ip 'mkdir -p /home/kube/bin /tmp/cnpg-plugin && tar -xzf - -C /tmp/cnpg-plugin kubectl-cnpg && install -m 755 /tmp/cnpg-plugin/kubectl-cnpg /home/kube/bin/kubectl-cnpg && rm -rf /tmp/cnpg-plugin && /home/kube/bin/kubectl-cnpg version 2>&1 | head -2'
done
