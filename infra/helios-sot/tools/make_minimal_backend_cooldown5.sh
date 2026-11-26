#!/usr/bin/env bash
# =============================================================================
# HELIOS — copie « MINIMALE » du backend pour le COOLDOWN DE REDEMARRAGE 5 min (18 nov. 2026), HORS dépôt.
#
# Consigne de l'utilisateur : « go cooldown 5 min » (le cooldown backend de 30 min avait différé deux arrêts de .153 de 15 à 23 min).
# Pourquoi NE PAS construire l'image depuis l'arbre de travail : par rapport aux sources de l'image EN PRODUCTION
# (pfe-backend:0.2.166-master153) l'arbre contient aussi le RETRAIT DE L'IA en Java (13 classes supprimées, 19 fichiers modifiés), jamais
# autorisé en production. Le construire tel quel le déploierait avec le cooldown.
#
# Principe (make_minimal_backend.sh, make_minimal_backend_master153.sh) : déployer UNIQUEMENT le changement nécessaire.
#   base     = sources de l'image EN PRODUCTION (copie minimale 0.2.166-master153 ; ses classes = l'empreinte déployée 5dc2990c…)
#   correctif = DEUX fichiers, vérifiés par SHA-256 APRES application :
#       VmPowerPolicy.java : RESTART_COOLDOWN 30 -> 5 min + POLICY_REVISION ;
#       VmPowerWatch.java  : fenêtre de recherche de la dernière action = MAX(cooldown, budget de preuves du rallumage DIRECT, 6 min)
#                            (sinon, à 5 min, la route directe — dormante — verrait « aucune action » 1 min avant la fin de son budget)
#   garde    = `diff -rq` base/copie sur src/ : EXACTEMENT ces deux fichiers diffèrent (rien d'autre n'a bougé)
# Les tests de la copie sont ceux de la base (non déployés, `-DskipTests`) : les tests mis à jour sont exécutés depuis l'arbre contre
# la copie (outils-tests/run_java_tests_153.py, MAIN_ROOT=<copie>).
#
# Usage : bash make_minimal_backend_cooldown5.sh [dossier-de-sortie] [dossier-base]
#   sortie : défaut /c/Users/houss/helios-minimal-build-0.2.167-cooldown5 ; base : défaut ...-0.2.166-master153
# Ne construit rien et ne touche NI le dépôt NI le cluster NI une VM.
# =============================================================================
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
HERE="$REPO/infra/helios-sot"
PATCH="$HERE/patches/cooldown5-sur-image-0.2.166-master153.patch"
SUMS="$HERE/patches/cooldown5-sur-image-0.2.166-master153.sha256"
M="${1:-${HELIOS_COOLDOWN5_DIR:-/c/Users/houss/helios-minimal-build-0.2.167-cooldown5}}"
BASE="${2:-${HELIOS_BASE_IMAGE_SRC:-/c/Users/houss/helios-minimal-build-0.2.166-master153}}"
J="src/main/java/com/orange/pfe/backend"
POLICY="$J/service/vmpower/VmPowerPolicy.java"
WATCH="$J/service/vmpower/VmPowerWatch.java"

[ -f "$PATCH" ] && [ -f "$SUMS" ] || { echo "correctif ou empreintes introuvables : $PATCH" >&2; exit 1; }
[ -f "$BASE/pom.xml" ] && [ -d "$BASE/src" ] || { echo "base introuvable : $BASE" >&2; exit 1; }
case "$M" in "$REPO"*) echo "le dossier de sortie ne doit pas etre DANS le depot : $M" >&2; exit 1;; esac
[ "$M" != "$BASE" ] || { echo "sortie = base : refus" >&2; exit 1; }
# la base doit etre celle de l'image en production : aucune source modifiee depuis la construction du tar Jib
if [ -f "$BASE/target/jib-image.tar" ] && [ -n "$(find "$BASE/src" -newer "$BASE/target/jib-image.tar" -type f | head -n 1)" ]; then
  echo "la base a ete modifiee depuis la construction de l'image : refus (elle ne correspond plus a la production)" >&2; exit 1
fi
# AVANT : la base est celle de .153 AUTO (cooldown 30 min), garde son code IA Java et n'a PAS deja le correctif
grep -q 'POLICY_REVISION = "2026-11-17-master-auto"' "$BASE/$POLICY" || { echo "base inattendue : revision 2026-11-17-master-auto absente" >&2; exit 1; }
grep -q 'RESTART_COOLDOWN = Duration.ofMinutes(30)' "$BASE/$POLICY" || { echo "base inattendue : cooldown de 30 min absent" >&2; exit 1; }
grep -q "AIOpsClientService" "$BASE/$J/service/CamundaJobWorkerLifecycle.java" || { echo "base inattendue (pas les sources de la production)" >&2; exit 1; }
if grep -q '2026-11-18-cooldown-5min' "$BASE/$POLICY" || grep -q 'windowMs' "$BASE/$WATCH"; then echo "la base contient deja le correctif cooldown 5 min : rien a faire" >&2; exit 1; fi

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
# APRES : empreinte SHA-256 (mode binaire) du fichier resultant + marqueurs
sha256sum -c --quiet "$SUMS" || { echo "ECHEC : empreinte differente apres application du correctif" >&2; exit 1; }
grep -q 'POLICY_REVISION = "2026-11-18-cooldown-5min"' "$POLICY"
grep -q 'RESTART_COOLDOWN = Duration.ofMinutes(5)' "$POLICY"
! grep -q 'Duration.ofMinutes(30)' "$POLICY"
grep -q 'long windowMs = Math.max(VmPowerPolicy.RESTART_COOLDOWN.toMillis()' "$WATCH"
grep -q "AIOpsClientService" "$J/service/CamundaJobWorkerLifecycle.java"        # le code IA de la production est CONSERVE
# MINIMALITE : exactement DEUX fichiers de src/ different de la base : VmPowerPolicy.java et VmPowerWatch.java
DIFF="$(diff -rq "$BASE/src" "$M/src" || true)"
N="$(printf '%s\n' "$DIFF" | grep -c . || true)"
if [ "$N" != "2" ] || ! printf '%s' "$DIFF" | grep -q "VmPowerPolicy.java" || ! printf '%s' "$DIFF" | grep -q "VmPowerWatch.java"; then
  echo "ECHEC : la copie differe de la base par autre chose que VmPowerPolicy.java et VmPowerWatch.java :" >&2; printf '%s\n' "$DIFF" >&2; exit 1
fi
echo "copie minimale cooldown 5 min prete : $M"
echo "  base : $BASE (sources de pfe-backend:0.2.166-master153) ; correctif : 2 fichiers (VmPowerPolicy.java, VmPowerWatch.java), empreintes verifiees, 2 seuls fichiers differents de la base"
echo "  suite (utilisateur, RAM hote >= 2 500 Mo) : RUNBOOK-MASTER-153.md section 8.quater"
