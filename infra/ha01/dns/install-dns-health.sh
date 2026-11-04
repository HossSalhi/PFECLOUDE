#!/usr/bin/env bash
# HELIOS HA-01 — installe le contrôleur DNS sur UN LB, en mode SHADOW (n'écrit rien dans dnsmasq).
# Exécuter DEPUIS le poste d'administration, une machine à la fois :  ./install-dns-health.sh 162   puis   ./install-dns-health.sh 160
# Idempotent. Retour arrière : ./uninstall-dns-health.sh <ip>
set -euo pipefail
LB="${1:?usage: $0 <160|162>}"
HERE="$(cd "$(dirname "$0")" && pwd)"
SSH=(ssh -o BatchMode=yes -o ConnectTimeout=8 "kube@192.168.148.$LB")
"${SSH[@]}" 'sudo -n mkdir -p /etc/dnsmasq-helios.d /var/lib/helios-dns-health /etc/helios-dns-health'
cat "$HERE/helios-dns-health.py"       | "${SSH[@]}" 'sudo -n tee /usr/local/bin/helios-dns-health.py >/dev/null && sudo -n chmod 755 /usr/local/bin/helios-dns-health.py'
cat "$HERE/helios-dns-health.service"  | "${SSH[@]}" 'sudo -n tee /etc/systemd/system/helios-dns-health.service >/dev/null'
"${SSH[@]}" 'test -f /etc/helios-dns-health/mode || echo shadow | sudo -n tee /etc/helios-dns-health/mode >/dev/null'
"${SSH[@]}" 'sudo -n python3 /usr/local/bin/helios-dns-health.py --selftest && sudo -n systemctl daemon-reload && sudo -n systemctl enable --now helios-dns-health && sleep 6 && systemctl is-active helios-dns-health && cat /var/lib/helios-dns-health/state.json'
echo
