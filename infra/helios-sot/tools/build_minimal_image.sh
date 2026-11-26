#!/usr/bin/env bash
# =============================================================================
# HELIOS — construit l'image backend « minimale » (voir make_minimal_backend.sh) SANS rien déployer :
# Jib `buildTar` (pas de Docker, pas de registre), dans un dossier HORS dépôt. Aucun effet sur le cluster ni sur l'hôte
# hors du dossier de sortie (le fichier ~/.docker/config.json est mis de côté pendant le build puis TOUJOURS restauré).
#
# Usage : TAG=0.2.163-vm4auto bash build_minimal_image.sh [dossier-de-sortie]
# Sortie : <dossier>/target/jib-image.tar  (importé ensuite sur les nœuds par scripts/helios-ops/build-deploy-backend.sh
#          avec BACKEND_DIR=<dossier> — qui reconstruit puis déploie avec retour arrière automatique).
# =============================================================================
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TAG="${TAG:-0.2.163-vm4auto}"
M="${1:-${HELIOS_MINIMAL_DIR:-/c/Users/houss/helios-minimal-build}}"

bash "$HERE/make_minimal_backend.sh" "$M"
# Procédure du dépôt (scripts/helios-ops/build-deploy-backend.sh) : un `credsStore` Docker Desktop périmé fait échouer le
# pull ANONYME de l'image de base (constaté le 6 nov. : 401 « incorrect username or password » ; DOCKER_CONFIG n'est pas
# pris en compte par Jib). On met le fichier de côté le temps du build et on le RESTAURE toujours (trap).
DOCKERCFG="$HOME/.docker/config.json"
MOVED=0
restore_docker_config() {
  if [ "$MOVED" = "1" ] && [ -f "$DOCKERCFG.helios.bak" ]; then mv "$DOCKERCFG.helios.bak" "$DOCKERCFG"; MOVED=0; fi
}
trap restore_docker_config EXIT
if [ -f "$DOCKERCFG" ] && grep -q credsStore "$DOCKERCFG" 2>/dev/null; then
  mv "$DOCKERCFG" "$DOCKERCFG.helios.bak"; MOVED=1
fi
cd "$M"
MAVEN_OPTS="${MAVEN_HEAP:--Xmx1100m}" ./mvnw -q -DskipTests compile jib:buildTar -Djib.to.image="pfe-backend:${TAG}"
test -s target/jib-image.tar
ls -la target/jib-image.tar
echo "image pfe-backend:${TAG} construite (tar) — non déployée"
