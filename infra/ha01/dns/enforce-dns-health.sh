#!/usr/bin/env bash
# HELIOS HA-01 — passe UN LB du DNS statique au DNS à santé contrôlée (mode ENFORCE).
#   1. écrit d'abord le fichier hostsdir avec l'état courant (les 2 IP)  -> aucune coupure de résolution
#   2. remplace les host-record statiques de dnsmasq par hostsdir=/etc/dnsmasq-helios.d (sauvegarde .bak-ha01-*)
#   3. valide la config (dnsmasq --test), redémarre dnsmasq, vérifie la réponse DNS locale
#   4. met le mode du contrôleur sur "enforce"
# Usage : ./enforce-dns-health.sh 162     (puis 160).   Retour arrière : ./rollback-dns-health.sh <ip>
set -euo pipefail
LB="${1:?usage: $0 <160|162>}"
SSH=(ssh -o BatchMode=yes -o ConnectTimeout=8 "kube@192.168.148.$LB")
TS=$(date -u +%Y%m%dT%H%M%SZ)
"${SSH[@]}" "set -e
sudo -n test -f /etc/dnsmasq.d/helios.conf
# Sauvegardes HORS de /etc/dnsmasq.d : le service dnsmasq charge TOUS les fichiers de ce dossier (CONFIG_DIR=/etc/dnsmasq.d,.dpkg-dist,
# .dpkg-old,.dpkg-new), donc un .bak-ha01-* y provoque « illegal repeated keyword » et empêche le démarrage (constaté le 4 nov. sur .162).
sudo -n mkdir -p /etc/helios-dns-health/backup
sudo -n cp -p /etc/dnsmasq.d/helios.conf /etc/helios-dns-health/backup/helios.conf.bak-ha01-$TS
sudo -n cp -p /etc/dnsmasq.d/helios-api.conf /etc/helios-dns-health/backup/helios-api.conf.bak-ha01-$TS
# 1. fichier hostsdir initial = les deux LB (état actuel)
printf '192.168.148.160 helios.orange.tn api.helios.orange.tn\n192.168.148.162 helios.orange.tn api.helios.orange.tn\n' | sudo -n tee /etc/dnsmasq-helios.d/helios.hosts >/dev/null
# 2. retirer les host-record statiques, ajouter hostsdir
sudo -n sed -i '/^host-record=helios.orange.tn,/d' /etc/dnsmasq.d/helios.conf
sudo -n grep -q '^hostsdir=' /etc/dnsmasq.d/helios.conf || echo 'hostsdir=/etc/dnsmasq-helios.d' | sudo -n tee -a /etc/dnsmasq.d/helios.conf >/dev/null
sudo -n sed -i '/^host-record=api.helios.orange.tn,/d' /etc/dnsmasq.d/helios-api.conf
# 3. validation (avec le MÊME vérificateur que le service systemd : \`dnsmasq --test\` seul ne charge pas les mêmes fichiers) + redemarrage
sudo -n dnsmasq --test
if [ -x /usr/share/dnsmasq/systemd-helper ]; then sudo -n /usr/share/dnsmasq/systemd-helper checkconfig; fi
sudo -n systemctl restart dnsmasq
sleep 2
systemctl is-active dnsmasq
for n in helios.orange.tn api.helios.orange.tn; do printf '%s -> ' \$n; dig +short +time=2 +tries=1 @127.0.0.1 \$n | sort | tr '\n' ' '; echo; done
# 4. mode enforce
echo enforce | sudo -n tee /etc/helios-dns-health/mode >/dev/null
"
echo "LB .$LB en mode enforce ($TS)"
