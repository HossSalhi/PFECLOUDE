#!/usr/bin/env bash
# =============================================================================
# HELIOS — copie « MINIMALE » du backend pour la consigne `.153` AUTO (17 nov. 2026), HORS dépôt.
#
# Pourquoi NE PAS construire l'image depuis l'arbre de travail : par rapport aux sources de l'image EN PRODUCTION
# (pfe-backend:0.2.165-disk-e2e), l'arbre contient aussi le RETRAIT DE L'IA en Java (13 classes supprimées : AIOpsClientService,
# IncidentLearning*, FineTuningController… ; 19 fichiers modifiés : application.properties, SecurityConfig, OrchestrationService…),
# jamais autorisé en production, et l'édition `CamundaWorkflowService` (businessId) déjà exclue le 14 nov. Le construire tel quel
# déploierait tout cela AVEC `.153`.
#
# Principe de make_minimal_backend.sh : déployer UNIQUEMENT les changements nécessaires, sans écraser les modifications locales
# préexistantes. Base = copie minimale du 14 nov. (ses classes sont byte-identiques à celles du pod : 273/273) ; correctif = les
# 6 fichiers `.153` (5 fichiers entiers + 1 hunk de CamundaJobWorkerLifecycle), vérifié par SHA-256 APRÈS application.
#
# Usage : bash make_minimal_backend_master153.sh [dossier-de-sortie] [dossier-base]
#   sortie : défaut /c/Users/houss/helios-minimal-build-0.2.166-master153 ; base : défaut ...-0.2.165-disk-e2e
# Ne construit rien et ne touche NI le dépôt NI le cluster NI une VM.
# =============================================================================
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
HERE="$REPO/infra/helios-sot"
PATCH="$HERE/patches/master153-sur-image-0.2.165-disk-e2e.patch"
SUMS="$HERE/patches/master153-sur-image-0.2.165-disk-e2e.sha256"
M="${1:-${HELIOS_MASTER153_DIR:-/c/Users/houss/helios-minimal-build-0.2.166-master153}}"
BASE="${2:-${HELIOS_BASE_IMAGE_SRC:-/c/Users/houss/helios-minimal-build-0.2.165-disk-e2e}}"
J="src/main/java/com/orange/pfe/backend"

[ -f "$PATCH" ] && [ -f "$SUMS" ] || { echo "correctif ou empreintes introuvables : $PATCH" >&2; exit 1; }
[ -f "$BASE/pom.xml" ] && [ -d "$BASE/src" ] || { echo "base introuvable : $BASE" >&2; exit 1; }
case "$M" in "$REPO"*) echo "le dossier de sortie ne doit pas etre DANS le depot : $M" >&2; exit 1;; esac
# la base doit etre celle de l'image en production : aucune source modifiee depuis la construction du tar Jib
if [ -f "$BASE/target/jib-image.tar" ] && [ -n "$(find "$BASE/src" -newer "$BASE/target/jib-image.tar" -type f | head -n 1)" ]; then
  echo "la base a ete modifiee depuis la construction de l'image : refus (elle ne correspond plus a la production)" >&2; exit 1
fi
# AVANT : la base contient le code IA Java de la production et ne contient pas encore le marqueur .153
grep -q "AIOpsClientService" "$BASE/$J/service/CamundaJobWorkerLifecycle.java" || { echo "base inattendue (pas les sources de la production du 14 nov.)" >&2; exit 1; }
if grep -q "POLICY_REVISION" "$BASE/$J/service/vmpower/VmPowerPolicy.java"; then echo "la base contient deja le marqueur .153 : rien a faire" >&2; exit 1; fi

rm -rf "$M"; mkdir -p "$M"
( cd "$BASE" && cp -r pom.xml mvnw mvnw.cmd .mvn src "$M"/ )
cd "$M"
# Les sources sont en LF. `core.autocrlf=true` (poste Windows) réécrirait les fichiers en CRLF : on le neutralise, et on retire
# tout CR du correctif (une extraction Git avec conversion de fin de ligne ne doit pas le rendre inapplicable).
TMP_PATCH="$(mktemp)"
trap 'rm -f "$TMP_PATCH"' EXIT
tr -d '\r' < "$PATCH" > "$TMP_PATCH"
git -c core.autocrlf=false -c core.safecrlf=false apply --check --whitespace=nowarn -p1 "$TMP_PATCH"
git -c core.autocrlf=false -c core.safecrlf=false apply --whitespace=nowarn -p1 "$TMP_PATCH"
# APRES : empreintes SHA-256 (mode binaire) des 6 fichiers resultants + marqueurs
sha256sum -c --quiet "$SUMS" || { echo "ECHEC : empreintes differentes apres application du correctif" >&2; exit 1; }
grep -q 'POLICY_REVISION = "2026-11-17-master-auto"' "$J/service/vmpower/VmPowerPolicy.java"
grep -q "K8S_CONTROL_PLANE" "$J/service/vmpower/VmPowerWatch.java"
grep -q "AIOpsClientService" "$J/service/CamundaJobWorkerLifecycle.java"        # le code IA de la production est CONSERVE
test "$(grep -c "businessId" "$J/service/CamundaWorkflowService.java" || true)" = "0"   # edition exclue, comme le 14 nov.
echo "copie minimale .153 prete : $M"
echo "  base : $BASE (sources de pfe-backend:0.2.165-disk-e2e) ; correctif : 6 fichiers, 13 hunks, empreintes verifiees"
echo "  suite (utilisateur, RAM hote >= 2 500 Mo) : RUNBOOK-MASTER-153.md section 5, etape 2"
