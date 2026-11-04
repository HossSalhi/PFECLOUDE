#!/usr/bin/env bash
# HELIOS HA-01 — retour arrière DNS d'UN LB : remet les host-record statiques (dernière sauvegarde .bak-ha01-*)
# et repasse le contrôleur en shadow. Usage : ./rollback-dns-health.sh 162
set -euo pipefail
LB="${1:?usage: $0 <160|162>}"
SSH=(ssh -o BatchMode=yes -o ConnectTimeout=8 "kube@192.168.148.$LB")
"${SSH[@]}" 'set -e
echo shadow | sudo -n tee /etc/helios-dns-health/mode >/dev/null
BK=/etc/helios-dns-health/backup
B1=$(ls -1t $BK/helios.conf.bak-ha01-* | head -1); B2=$(ls -1t $BK/helios-api.conf.bak-ha01-* | head -1)
sudo -n cp -p "$B1" /etc/dnsmasq.d/helios.conf; sudo -n cp -p "$B2" /etc/dnsmasq.d/helios-api.conf
sudo -n rm -f /etc/dnsmasq-helios.d/helios.hosts
sudo -n dnsmasq --test && sudo -n systemctl restart dnsmasq && sleep 2 && systemctl is-active dnsmasq
for n in helios.orange.tn api.helios.orange.tn; do printf "%s -> " $n; dig +short +time=2 +tries=1 @127.0.0.1 $n | sort | tr "\n" " "; echo; done'
