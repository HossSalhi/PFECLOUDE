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
BACKEND_DIR="$(cd "$(dirname "$0")/../../backend/backend" && pwd)"
HEALTH_IP="${HEALTH_IP:-192.168.148.155}"
: "${SUDO_PASS:?Définir SUDO_PASS (mot de passe sudo de kube@<noeud>)}"
log(){ echo "[$(date +%H:%M:%S)] $*"; }

log "1) Build image $IMAGE (Jib exploded, sans Docker ; devtools exclu via scope=provided)"
# Un credsStore Docker Desktop périmé fait échouer le pull anonyme de l'image de base.
# On le met de côté le temps du build (Jib pulle alors en anonyme).
DOCKERCFG="$HOME/.docker/config.json"; MOVED=0
[ -f "$DOCKERCFG" ] && grep -q credsStore "$DOCKERCFG" 2>/dev/null && { mv "$DOCKERCFG" "$DOCKERCFG.helios.bak"; MOVED=1; }
( cd "$BACKEND_DIR" && MAVEN_OPTS="-Xmx1100m" ./mvnw -q -DskipTests compile jib:buildTar -Djib.to.image="$IMAGE" )
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

log "3) Capture image actuelle (pour rollback)"
PREV=$(kubectl get deploy backend -n "$NS" -o jsonpath='{.spec.template.spec.containers[0].image}')
log "   image actuelle = $PREV"

log "4) Rolling update -> $IMAGE"
kubectl set image deployment/backend backend="$IMAGE" -n "$NS"

log "5) Vérif (max 4 min)"
ok=1
for i in $(seq 1 24); do
  rd=$(kubectl get deploy backend -n "$NS" -o jsonpath='{.status.readyReplicas}')
  up=$(kubectl get deploy backend -n "$NS" -o jsonpath='{.status.updatedReplicas}')
  [ "$rd" = "2" ] && [ "$up" = "2" ] && { ok=0; break; }
  sleep 10
done

if [ $ok -eq 0 ] && [ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://$HEALTH_IP:30880/actuator/health)" = "200" ]; then
  log "✅ Déploiement $IMAGE OK (2/2, HTTP 200)"
else
  log "❌ Échec -> ROLLBACK vers $PREV"
  kubectl set image deployment/backend backend="$PREV" -n "$NS"
  kubectl rollout status deployment/backend -n "$NS" --timeout=120s
  log "rollback effectué. Diagnostiquer : kubectl logs -l app=backend -n $NS --previous"
  exit 1
fi
