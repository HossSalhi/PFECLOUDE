#!/usr/bin/env bash
# HELIOS — Installe node_exporter sur les 3 nœuds K8s (L8).
# DÉPLOYÉ ET VÉRIFIÉ le 2026-06-11 : les 3 nœuds (.153/.155/.158) étaient down dans
# Prometheus (job=vm-node-exporters) ; .160/.161 déjà up. Après ce script : 5/5 UP.
#
# Pré-requis : SSH par clé vers kube@<noeud> avec sudo SANS mot de passe (cas du lab).
# Le binaire est récupéré depuis un nœud qui l'a déjà (.158), puis poussé sur les autres.
#
# Usage : ./install-node-exporter.sh
set -uo pipefail
SRC=192.168.148.158                          # nœud source (a déjà le binaire)
NODES=(192.168.148.153 192.168.148.158 192.168.148.155)
UNIT="$(dirname "$0")/node_exporter.service"
TMP=/tmp/node_exporter.helios

echo ">> récupération du binaire depuis $SRC"
ssh -o BatchMode=yes kube@"$SRC" 'cat /usr/local/bin/node_exporter' > "$TMP"
[ -s "$TMP" ] || { echo "ERREUR: binaire vide"; exit 1; }

for ip in "${NODES[@]}"; do
  echo ">> $ip : install binaire + unit + start"
  ssh -o BatchMode=yes kube@"$ip" \
    'cat > /tmp/ne && sudo install -m0755 -o root -g root /tmp/ne /usr/local/bin/node_exporter && rm -f /tmp/ne' < "$TMP"
  ssh -o BatchMode=yes kube@"$ip" 'sudo tee /etc/systemd/system/node_exporter.service >/dev/null' < "$UNIT"
  ssh -o BatchMode=yes kube@"$ip" 'sudo systemctl daemon-reload && sudo systemctl enable --now node_exporter && echo "  $(systemctl is-active node_exporter) :9100=$(ss -ltn|grep -c :9100)"'
done
rm -f "$TMP"
echo ">> Vérifier : curl -s http://192.168.148.160:9090/api/v1/query?query='up{job=\"vm-node-exporters\"}'"
