#!/usr/bin/env bash
# =============================================================================
# HELIOS — étapes d'ADMINISTRATION de l'auto-rallumage des 3 VM (.158 .155 .162), dans l'ordre, arrêt au premier échec.
# (.160, VM de supervision, est en DÉTECTION SEULE : jamais rallumée automatiquement — consigne finale du 6 nov. 2026.)
# DRY-RUN PAR DÉFAUT (affiche le plan, ne modifie rien). `--apply` exécute. `--stage <nom>` n'exécute qu'une étape.
#
# Pourquoi un script « administrateur » : le contrôle de permissions de l'assistant refuse (à juste titre) toute modification
# de sécurité / de secret / du cluster partagé. Ces étapes sont donc à lancer par vous, session ADMINISTRATEUR (PowerShell
# ou Git Bash élevé), p. ex. dans Claude Code avec le préfixe `!` :
#
#     ! bash infra/helios-sot/tools/apply_vm4_auto.sh --apply
#
# Ce qui est DÉJÀ fait (ne figure donc pas ici) : script du watchdog recopié (4 cibles, appelants .153/.155/.158), tâche
# HELIOS-Watchdog activée et démarrée (boot + session, S4U), ancien agent :9999 arrêté et bloqué, image backend construite.
#
# Étapes (chacune est idempotente ou protégée ; rien n'active le rallumage RÉEL : le backend démarre en MODE OMBRE) :
#   firewall  pare-feu 9099 : ALLOW des 3 nœuds + BLOCK de tout le reste (+ ancienne règle .161 désactivée)   [sécurité hôte]
#   token     jeton backend<->watchdog NOUVEAU (fichier hôte + secret backend-secret), jamais affiché         [secret]
#   configmap clés HELIOS_VMPOWER_* du ConfigMap vivant (patch, jamais `apply`) : ENABLED=true, DRYRUN=true    [cluster]
#   backend   image MINIMALE (sans l'édition businessId jamais déployée) : import ctr sur 3 nœuds, rolling update
#             surveillé, RETOUR ARRIÈRE automatique ; les pods lisent le nouveau jeton et la ConfigMap          [cluster]
#   bridge    Bridge cible sans power-on sur .161 (sauvegardes, jeton du watchdog retiré, preuves, retour auto) [.161]
#   verify    vérification lecture seule (verify_vm4_auto.py)
# =============================================================================
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
TAG="${TAG:-0.2.163-vm4auto}"
M="${HELIOS_MINIMAL_DIR:-/c/Users/houss/helios-minimal-build}"
STAGES=(firewall token configmap backend bridge verify)
APPLY=0
ONLY=""
while [ $# -gt 0 ]; do
  case "$1" in
    --apply) APPLY=1 ;;
    --stage) shift; ONLY="${1:?nom de l etape requis}" ;;
    *) echo "usage: $0 [--apply] [--stage ${STAGES[*]}]" >&2; exit 2 ;;
  esac
  shift
done
log() { printf '\n[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }
winpath() { cygpath -w "$1" 2>/dev/null || echo "$1"; }

run_stage() {
  case "$1" in
    firewall)
      log "firewall — pare-feu 9099 (ALLOW .153/.155/.158 + BLOCK des autres)"
      if [ "$APPLY" = 1 ]; then
        powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$(winpath "$REPO/infra/helios-sot/host/Set-HeliosWatchdogFirewall.ps1")" -Apply
      else
        powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$(winpath "$REPO/infra/helios-sot/host/Set-HeliosWatchdogFirewall.ps1")"
      fi ;;
    token)
      log "token — rotation du jeton backend<->watchdog (le Bridge ne le reçoit pas)"
      if [ "$APPLY" = 1 ]; then python -I "$HERE/rotate_watchdog_token.py" --apply; else python -I "$HERE/rotate_watchdog_token.py"; fi ;;
    configmap)
      log "configmap — HELIOS_VMPOWER_ENABLED=true, DRYRUN=true, ACTIVE_VMS vide (MODE OMBRE)"
      echo "   URL du watchdog dans le ConfigMap : $(kubectl -n pfe get configmap backend-config -o jsonpath='{.data.REMEDIATION_MASTER_WATCHDOG_URL}')"
      if [ "$APPLY" = 1 ]; then
        kubectl -n pfe patch configmap backend-config --type merge \
          -p '{"data":{"HELIOS_VMPOWER_ENABLED":"true","HELIOS_VMPOWER_DRYRUN":"true","HELIOS_VMPOWER_ACTIVE_VMS":""}}'
      else
        echo "   [dry-run] kubectl -n pfe patch configmap backend-config --type merge -p '{\"data\":{\"HELIOS_VMPOWER_ENABLED\":\"true\",\"HELIOS_VMPOWER_DRYRUN\":\"true\",\"HELIOS_VMPOWER_ACTIVE_VMS\":\"\"}}'"
      fi ;;
    backend)
      log "backend — image minimale pfe-backend:$TAG (copie hors dépôt : $M)"
      if [ "$APPLY" = 1 ]; then
        # La copie est TOUJOURS régénérée depuis l'arbre de travail actuel (jamais un tar périmé), puis le script du dépôt
        # construit, importe sur les 3 nœuds (sudo sans mot de passe), met à jour en rolling et revient en arrière si besoin.
        bash "$HERE/make_minimal_backend.sh" "$M"
        BACKEND_DIR="$M" SUDO_PASS="${SUDO_PASS:-sans-objet}" TAG="$TAG" bash "$REPO/scripts/helios-ops/build-deploy-backend.sh"
      else
        echo "   [dry-run] BACKEND_DIR=$M TAG=$TAG bash scripts/helios-ops/build-deploy-backend.sh   (import ctr x3, set image, 4 min de contrôle, retour arrière auto)"
        ls -la "$M/target/jib-image.tar" 2>&1 | sed 's/^/   /'
      fi ;;
    bridge)
      log "bridge — Bridge cible sans power-on sur .161"
      if [ "$APPLY" = 1 ]; then bash "$HERE/deploy_bridge_target.sh" --apply; else bash "$HERE/deploy_bridge_target.sh"; fi ;;
    verify)
      log "verify — vérification lecture seule"
      python -I "$HERE/verify_vm4_auto.py" || true ;;
    activate)
      # JAMAIS dans la boucle par défaut : décision distincte (porte du plan : mode ombre observé sans faux positif ;
      # essais réels sous autorisation écrite NOMINATIVE par VM). VMS = liste exacte des noms, ex. VMS=k8s-worker1.
      VMS="${VMS:?VMS requis : noms exacts séparés par des virgules (ex. VMS=k8s-worker1,k8s-worker2,k8s-lb2,k8s-master ; .160 est en détection seule)}"
      IFS=',' read -r -a _vms <<< "$VMS"
      for _v in "${_vms[@]}"; do
        case "$_v" in
          k8s-worker1|k8s-worker2|k8s-lb2|k8s-master) ;;   # k8s-master (.153) : cible AUTO depuis la consigne du 17 nov. 2026
          k8s-monitoring) echo "REFUS : « k8s-monitoring » (.160) est la VM de supervision : DÉTECTION SEULE, jamais rallumée automatiquement (consigne finale du 6 nov. 2026)" >&2; exit 2 ;;
          *) echo "REFUS : « $_v » n est pas une cible autorisée (noms exacts : k8s-worker1 k8s-worker2 k8s-lb2 k8s-master ; k8s-monitoring est en détection seule)" >&2; exit 2 ;;
        esac
      done
      log "activate — rallumage RÉEL pour : $VMS  (HELIOS_VMPOWER_DRYRUN=false)"
      if [ "$APPLY" = 1 ]; then
        kubectl -n pfe patch configmap backend-config --type merge \
          -p "{\"data\":{\"HELIOS_VMPOWER_ENABLED\":\"true\",\"HELIOS_VMPOWER_DRYRUN\":\"false\",\"HELIOS_VMPOWER_ACTIVE_VMS\":\"$VMS\"}}"
        kubectl -n pfe rollout restart deployment/backend
        kubectl -n pfe rollout status deployment/backend --timeout=240s
      else
        echo "   [dry-run] patch ConfigMap DRYRUN=false ACTIVE_VMS=$VMS puis rollout restart deployment/backend"
      fi ;;
    deactivate)
      log "deactivate — retour au MODE OMBRE (aucun rallumage réel) : DRYRUN=true, ACTIVE_VMS vide"
      if [ "$APPLY" = 1 ]; then
        kubectl -n pfe patch configmap backend-config --type merge \
          -p '{"data":{"HELIOS_VMPOWER_DRYRUN":"true","HELIOS_VMPOWER_ACTIVE_VMS":""}}'
        kubectl -n pfe rollout restart deployment/backend
        kubectl -n pfe rollout status deployment/backend --timeout=240s
      else
        echo "   [dry-run] patch ConfigMap DRYRUN=true ACTIVE_VMS= puis rollout restart deployment/backend"
      fi ;;
    *) echo "étape inconnue : $1 (${STAGES[*]} | activate | deactivate)" >&2; exit 2 ;;
  esac
}

if [ "$APPLY" = 0 ]; then log "DRY-RUN (rien n'est modifié). Relancer avec --apply pour exécuter."; fi
if [ "$ONLY" = "activate" ] || [ "$ONLY" = "deactivate" ]; then
  run_stage "$ONLY"        # étapes explicites uniquement : jamais exécutées par la boucle par défaut
else
  for s in "${STAGES[@]}"; do
    if [ -z "$ONLY" ] || [ "$ONLY" = "$s" ]; then run_stage "$s"; fi
  done
fi
log "terminé"
if [ "$APPLY" = 1 ]; then
  echo "Rappel : le backend est en MODE OMBRE. L'activation du rallumage RÉEL (HELIOS_VMPOWER_DRYRUN=false + ACTIVE_VMS) est une décision distincte."
fi
