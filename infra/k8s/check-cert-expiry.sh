#!/usr/bin/env bash
# HELIOS - controle d'echeance des certificats kubeadm (LECTURE SEULE). A lancer sur un noeud control-plane (sudo sans mot de passe).
# Code 0 = tous > SEUIL jours ; 1 = au moins un certificat sous le seuil ; 2 = lecture impossible. Seuil : SEUIL_JOURS (defaut 60).
set -u; SEUIL="${SEUIL_JOURS:-60}"; out=$(sudo -n kubeadm certs check-expiration 2>/dev/null) || { echo "ERREUR : kubeadm certs check-expiration impossible"; exit 2; }
min=99999; bad=0
while read -r name rest; do
  d=$(echo "$rest" | grep -oE '[0-9]+d' | head -n1 | tr -d d); [ -z "$d" ] && continue
  [ "$d" -lt "$min" ] && min=$d
  if [ "$d" -lt "$SEUIL" ]; then echo "ALERTE  $name : $d jours restants (seuil $SEUIL)"; bad=1; else echo "OK      $name : $d jours"; fi
done < <(echo "$out" | awk '/^[a-z-]+(\.conf)?[[:space:]]+[A-Z][a-z]{2} [0-9]/{print}')
echo "minimum : $min jours ; seuil : $SEUIL"; exit $bad