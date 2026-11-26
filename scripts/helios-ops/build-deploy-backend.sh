#!/usr/bin/env bash
# =============================================================================
# HELIOS — Build + déploiement de l'image backend SANS Docker ni registry
# =============================================================================
# Contourne L18 (RAM hôte : pas de VM Docker) ET L20 (air-gap : pas de registry).
# Build via Jib buildTar (mode packaged = fat-jar Spring Boot, DevTools EXCLU)
# -> tar local -> `ctr images import` sur les 3 nœuds -> rolling update + rollback.
#
# Prouvé le 2026-06-11 : Jib build OK sans Docker. NB: l'image Jib en mode
# EXPLODED embarque DevTools (-> app self-exit `restartedMain`) ; le mode
# `packaged` (configuré dans pom.xml) l'exclut. Toujours utiliser ce script.
#
# Pré-requis : SSH kube@<noeud> + sudo password dans $SUDO_PASS ; kubectl admin.
# Usage : SUDO_PASS=*** TAG=0.2.24 ./build-deploy-backend.sh
# =============================================================================
set -uo pipefail
TAG="${TAG:-0.2.24}"
NS="${NS:-pfe}"
IMAGE="pfe-backend:${TAG}"
NODES=(192.168.148.153 192.168.148.158 192.168.148.155)
# BACKEND_DIR peut être surchargé (ex. copie « minimale » hors dépôt : jamais d'écrasement de l'arbre de travail).
BACKEND_DIR="${BACKEND_DIR:-$(cd "$(dirname "$0")/../../backend/backend" && pwd)}"
HEALTH_IP="${HEALTH_IP:-192.168.148.155}"
: "${SUDO_PASS:?Définir SUDO_PASS (mot de passe sudo de kube@<noeud>)}"
log(){ echo "[$(date +%H:%M:%S)] $*"; }

if [ "${SKIP_BUILD:-0}" = "1" ]; then
  # Reprise après un retour arrière FAUX (6 nov. 2026 18:16) : l'image est déjà construite et importée sur les 3 nœuds.
  # Si elle manquait quelque part, le pod resterait en ErrImagePull -> la vérification échoue -> retour arrière automatique.
  log "1-2) SKIP_BUILD=1 : build et import ignorés (image $IMAGE supposée déjà présente sur les nœuds)"
else
log "1) Build image $IMAGE (Jib exploded, sans Docker ; devtools exclu via scope=provided)"
# Un credsStore Docker Desktop périmé fait échouer le pull anonyme de l'image de base.
# On le met de côté le temps du build (Jib pulle alors en anonyme).
DOCKERCFG="$HOME/.docker/config.json"; MOVED=0
[ -f "$DOCKERCFG" ] && grep -q credsStore "$DOCKERCFG" 2>/dev/null && { mv "$DOCKERCFG" "$DOCKERCFG.helios.bak"; MOVED=1; }
# MAVEN_HEAP réglable : sur hôte saturé (L18) baisser à -Xmx512m pour tenir dans la RAM libre.
( cd "$BACKEND_DIR" && MAVEN_OPTS="${MAVEN_HEAP:--Xmx1100m}" ./mvnw -q -DskipTests compile jib:buildTar -Djib.to.image="$IMAGE" )
rc=$?
[ "$MOVED" = "1" ] && mv "$DOCKERCFG.helios.bak" "$DOCKERCFG"
[ $rc -eq 0 ] || { log "build KO"; exit 1; }
TAR="$BACKEND_DIR/target/jib-image.tar"
[ -s "$TAR" ] || { log "tar absent"; exit 1; }

log "2) Import sur les ${#NODES[@]} nœuds via ctr"
for ip in "${NODES[@]}"; do
  scp -o BatchMode=yes "$TAR" kube@"$ip":/tmp/jib-image.tar
  ssh -o BatchMode=yes kube@"$ip" "echo '$SUDO_PASS' | sudo -S ctr -n k8s.io images import /tmp/jib-image.tar; echo '$SUDO_PASS' | sudo -S rm -f /tmp/jib-image.tar" 2>&1 | grep -i unpacking
done
fi

if [ "${SKIP_DEPLOY:-0}" = "1" ]; then
  # Construction + import SEULEMENT (consigne .153, 17 nov. 2026) : le déploiement est ensuite fait à la main
  # (`kubectl set image` + `kubectl rollout status` + santé interne), la vérification ci-dessous ayant donné de FAUX retours
  # arrière les 6 et 14 nov. 2026. Par défaut (SKIP_DEPLOY absent) le comportement du script est INCHANGÉ.
  log "SKIP_DEPLOY=1 : image $IMAGE construite et importée sur les ${#NODES[@]} nœuds ; AUCUN déploiement lancé"
  exit 0
fi

log "3) Capture image actuelle (pour rollback)"
PREV=$(kubectl get deploy backend -n "$NS" -o jsonpath='{.spec.template.spec.containers[0].image}')
log "   image actuelle = $PREV"

log "4) Rolling update -> $IMAGE"
kubectl set image deployment/backend backend="$IMAGE" -n "$NS"

log "5) Vérif (max 4 min) : 2/2 prêts et à jour, puis santé INTERNE de chaque pod"
ok=1
why=""
rd=""; up=""; av=""
for i in $(seq 1 24); do
  rd=$(kubectl get deploy backend -n "$NS" -o jsonpath='{.status.readyReplicas}')
  up=$(kubectl get deploy backend -n "$NS" -o jsonpath='{.status.updatedReplicas}')
  av=$(kubectl get deploy backend -n "$NS" -o jsonpath='{.status.availableReplicas}')
  [ "$rd" = "2" ] && [ "$up" = "2" ] && [ "$av" = "2" ] && { ok=0; break; }
  sleep 10
done
[ $ok -eq 0 ] || why="rollout non terminé en 4 min (prêts=${rd:-0} à jour=${up:-0} disponibles=${av:-0})"

# Santé INTERNE de chaque pod (curl DANS le pod, comme sa propre sonde) : fiable, contrairement au NodePort externe qui
# répond 403 (endpoint sécurisé) ou 200 selon l'adresse source et dont une seule requête de 5 s, au moment exact d'une
# bascule, a donné un FAUX échec le 6 nov. 2026 (retour arrière alors que les 2 pods étaient prêts).
# Pods contrôlés = UNIQUEMENT ceux de la NOUVELLE image et NON en cours d'arrêt. Les anciens pods restent listés (Terminating)
# plusieurs dizaines de secondes après la bascule : les tester a provoqué, le 6 nov. 2026 à 18:16, un FAUX échec suivi d'un
# retour arrière alors que les 2 nouveaux pods étaient prêts et sains (santé interne 200, empreinte du contenu conforme).
new_pods(){
  kubectl get pods -n "$NS" -l app=backend -o go-template='{{range .items}}{{if not .metadata.deletionTimestamp}}{{(index .spec.containers 0).image}} {{.metadata.name}}{{"\n"}}{{end}}{{end}}' 2>/dev/null \
    | awk -v img="$IMAGE" '$1==img{print $2}'
}
if [ $ok -eq 0 ]; then
  PODS=$(new_pods)
  n=$(printf '%s\n' "$PODS" | grep -c . || true)
  if [ "$n" != "2" ]; then ok=1; why="pods de la nouvelle image (hors arrêt) = ${n} au lieu de 2"; fi
  for p in $PODS; do
    code=""
    for t in $(seq 1 12); do
      code=$(kubectl exec -n "$NS" "$p" -- sh -c "curl -s -m 8 -o /dev/null -w '%{http_code}' http://localhost:8080/actuator/health" 2>/dev/null || true)
      [ "$code" = "200" ] && break
      sleep 10
    done
    log "   santé interne de $p : HTTP ${code:-aucune réponse}"
    if [ "$code" != "200" ]; then ok=1; why="santé interne de $p = ${code:-aucune réponse}"; fi
  done
fi

# NodePort externe : INFORMATIF seulement (403 = endpoint sécurisé mais servi ; 200 = servi).
HC=$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 http://$HEALTH_IP:30880/actuator/health || true)
log "   NodePort externe $HEALTH_IP:30880 : HTTP ${HC:-000} (informatif)"

if [ $ok -eq 0 ]; then
  log "✅ Déploiement $IMAGE OK (2/2 prêts, santé interne 200 sur chaque pod)"
else
  # Les preuves de l'échec sont CAPTURÉES avant le retour arrière (les pods de la nouvelle révision seront supprimés).
  DIAG="${DIAG_DIR:-$HOME/helios-deploy-diag}/$(date +%Y%m%d-%H%M%S)"
  mkdir -p "$DIAG" 2>/dev/null || true
  {
    kubectl get pods -n "$NS" -l app=backend -o wide
    echo "--- replicasets ---"; kubectl get rs -n "$NS" -l app=backend
    echo "--- describe deploy ---"; kubectl describe deploy backend -n "$NS"
    echo "--- événements ---"; kubectl get events -n "$NS" --sort-by=.lastTimestamp
  } > "$DIAG/etat.txt" 2>&1 || true
  for p in $(kubectl get pods -n "$NS" -l app=backend -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' 2>/dev/null); do
    kubectl logs -n "$NS" "$p" --tail=600 > "$DIAG/$p.log" 2>&1 || true
    kubectl logs -n "$NS" "$p" --previous --tail=300 > "$DIAG/$p.previous.log" 2>&1 || true
  done
  log "❌ Échec : ${why:-vérification non satisfaite}. Preuves capturées dans $DIAG -> ROLLBACK vers $PREV"
  kubectl set image deployment/backend backend="$PREV" -n "$NS"
  kubectl rollout status deployment/backend -n "$NS" --timeout=120s
  log "rollback effectué. Diagnostic : $DIAG (etat.txt + journaux des pods de la nouvelle révision)"
  exit 1
fi
