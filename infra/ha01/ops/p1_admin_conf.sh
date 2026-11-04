#!/usr/bin/env bash
# HELIOS HA-01 — C3 : /etc/kubernetes/admin.conf ne doit plus pointer l'IP de .160 (SPOF caché : outils root, watchdog Calico de .153 toutes les 5 min).
#   .155 et .158 -> leur apiserver LOCAL (https://192.168.148.<ip>:6443 ; l'IP du nœud est dans le SAN du certificat) : indépendants de DNS et des LB
#   .153         -> https://api.helios.orange.tn:6443 (nom -> LB -> apiservers des WORKERS ; .153 est en 'backup' dans HAProxy) :
#                   .153 est le nœud fragile, son watchdog ne doit pas charger SON propre apiserver (mesuré le 3 nov.)
# Sauvegarde .bak-ha01-<ts> (root, 600) ; vérification kubectl avec le nouveau fichier ; retour arrière automatique si la vérification échoue.
# Usage : ./p1_admin_conf.sh apply <153|155|158> | rollback <ip> | status <ip>
set -euo pipefail
ACTION="${1:?usage: $0 apply|rollback|status <153|155|158>}"
N="${2:?usage: $0 apply|rollback|status <153|155|158>}"
S=(ssh -o BatchMode=yes -o ConnectTimeout=8 "kube@192.168.148.$N")
TS=$(date -u +%Y%m%dT%H%M%SZ)
if [ "$N" = "153" ]; then NEW="https://api.helios.orange.tn:6443"; else NEW="https://192.168.148.$N:6443"; fi
case "$ACTION" in
  status) "${S[@]}" 'sudo -n grep -n "server:" /etc/kubernetes/admin.conf; sudo -n ls -la /etc/kubernetes/admin.conf* | awk "{print \$1, \$5, \$9}"' ;;
  apply)
    "${S[@]}" "set -e
      sudo -n test -f /etc/kubernetes/admin.conf
      sudo -n cp -p /etc/kubernetes/admin.conf /etc/kubernetes/admin.conf.bak-ha01-$TS
      sudo -n sed -i 's#server: https://192.168.148.160:6443#server: $NEW#' /etc/kubernetes/admin.conf
      sudo -n grep -n 'server:' /etc/kubernetes/admin.conf
      if sudo -n env KUBECONFIG=/etc/kubernetes/admin.conf kubectl get nodes --request-timeout=20s >/dev/null 2>&1; then echo 'verification kubectl OK'; else
        echo 'ECHEC verification -> retour arriere'; sudo -n cp -p /etc/kubernetes/admin.conf.bak-ha01-$TS /etc/kubernetes/admin.conf; exit 4; fi"
    ;;
  rollback)
    "${S[@]}" 'B=$(ls -1t /etc/kubernetes/admin.conf.bak-ha01-* | tail -1); echo "restauration de $B (la plus ANCIENNE = origine)"; sudo -n cp -p "$B" /etc/kubernetes/admin.conf; sudo -n grep -n "server:" /etc/kubernetes/admin.conf' ;;
  *) echo "usage"; exit 1 ;;
esac
