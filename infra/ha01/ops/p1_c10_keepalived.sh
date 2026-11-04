#!/usr/bin/env bash
# HELIOS HA-01 — C10 : retire le paquet keepalived (installé mais désactivé, sans configuration) de .162 puis .160.
# Contrainte HA-01 : aucun VIP/keepalived/VRRP. Le paquet n'était pas utilisé ; sa présence permettait une réactivation accidentelle.
# Garde-fous : refuse d'agir si le service est actif, activé, ou si une configuration /etc/keepalived/keepalived.conf existe.
# Usage : ./p1_c10_keepalived.sh 162  (puis 160)
set -euo pipefail
LB="${1:?usage: $0 <160|162>}"
ssh -o BatchMode=yes -o ConnectTimeout=8 "kube@192.168.148.$LB" 'set -e
  dpkg -l keepalived 2>/dev/null | grep -q "^ii" || { echo "keepalived absent : rien à faire"; exit 0; }
  [ "$(systemctl is-active keepalived || true)" = "active" ] && { echo "STOP: keepalived ACTIF"; exit 3; }
  [ "$(systemctl is-enabled keepalived 2>/dev/null || true)" = "enabled" ] && { echo "STOP: keepalived ACTIVE au démarrage"; exit 3; }
  sudo -n test -f /etc/keepalived/keepalived.conf && { echo "STOP: une configuration existe"; exit 3; }
  sudo -n DEBIAN_FRONTEND=noninteractive apt-get purge -y keepalived 2>&1 | tail -3
  dpkg -l keepalived 2>/dev/null | grep -q "^ii" && { echo "ECHEC: paquet toujours présent"; exit 4; } || echo "keepalived retiré"
  ip -4 -br addr show ens33 | cut -c1-90'
