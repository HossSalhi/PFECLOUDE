#!/usr/bin/env bash
# =============================================================================
# HELIOS — copie « MINIMALE » du backend, HORS dépôt (l'arbre de travail n'est JAMAIS modifié).
#
# Principe « déployer uniquement les changements nécessaires, sans écraser les modifications locales préexistantes » :
# l'arbre de travail contient, en plus de ce chantier, UNE édition antérieure jamais déployée et sans rapport avec le
# rallumage des VM — `CamundaWorkflowService` (identifiants métier `businessId` des instances Camunda, 31 oct. 22:36-22:38)
# et son client Camunda 8.9.0. La copie produite ici = arbre de travail actuel MOINS cette édition :
#   - CamundaWorkflowService.java := sauvegarde datée « before-business-id » (identique à l'image en place) ;
#   - pom.xml : camunda.version 8.9.0 -> 8.8.0 (client de l'image en place) ;
#   - CamundaWorkflowServiceNoDuplicateRemediationTest retiré DE LA COPIE (il ne teste que l'édition exclue).
# Preuve : dans la copie, CamundaWorkflowService.class est byte-identique à celui du pod en marche (empreintes SHA-256).
#
# Usage : bash make_minimal_backend.sh [dossier-de-sortie]       (défaut : C:\Users\houss\helios-minimal-build)
# =============================================================================
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
SRC="$REPO/backend/backend"
M="${1:-${HELIOS_MINIMAL_DIR:-/c/Users/houss/helios-minimal-build}}"
S="src/main/java/com/orange/pfe/backend/service"
BAK="$SRC/$S/CamundaWorkflowService.java.before-business-id-20261031-223641.bak"

[ -f "$BAK" ] || { echo "sauvegarde introuvable : $BAK" >&2; exit 1; }
case "$M" in "$REPO"*) echo "le dossier de sortie ne doit pas etre DANS le depot : $M" >&2; exit 1;; esac

rm -rf "$M"
mkdir -p "$M"
cd "$SRC"
cp -r pom.xml mvnw mvnw.cmd .mvn src "$M"/
cd "$M"
cp "$BAK" "$S/CamundaWorkflowService.java"
sed -i 's#<camunda.version>8.9.0</camunda.version>#<camunda.version>8.8.0</camunda.version>#' pom.xml
rm -f src/test/java/com/orange/pfe/backend/service/CamundaWorkflowServiceNoDuplicateRemediationTest.java
test "$(grep -c "businessId" "$S/CamundaWorkflowService.java" || true)" = "0"
grep -q "<camunda.version>8.8.0</camunda.version>" pom.xml
echo "copie minimale prete : $M (businessId absent, client Camunda 8.8.0)"
