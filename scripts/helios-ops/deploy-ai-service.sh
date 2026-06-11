#!/usr/bin/env bash
# =============================================================================
# HELIOS — Déploiement CONTRÔLÉ du repo ai-service vers la prod .161
# =============================================================================
# La prod .161 tourne une version PLUS ANCIENNE que le repo (vérifié 2026-06-11 :
# autonomous_monitor.py 398 lignes en prod vs 738 dans le repo). Ce script pousse
# le code du repo avec FILET DE SÉCURITÉ : backup + vérif santé + ROLLBACK AUTO.
#
# ⚠️ Provoque ~1-2 min d'indisponibilité de la détection IA pendant le restart.
#    À lancer HORS démo, puis re-tester (ex. test-worker-ha.sh).
#
# Pré-requis : SSH kube@.161, et le mot de passe sudo dans $SUDO_PASS.
# Usage : SUDO_PASS=*** ./deploy-ai-service.sh
# =============================================================================
set -uo pipefail
HOST="${HOST:-kube@192.168.148.161}"
REMOTE_APP="${REMOTE_APP:-/opt/pfe-ai-service/app}"
SVC="${SVC:-pfe-ai-service}"
LOCAL_APP="$(cd "$(dirname "$0")/../../backend/backend/ai-service/app" && pwd)"
HEALTH="http://192.168.148.161:8000/health"
READY="http://192.168.148.161:8000/ready"
: "${SUDO_PASS:?Définir SUDO_PASS (mot de passe sudo de kube@.161)}"
SUDO="echo \"$SUDO_PASS\" | sudo -S"
ts=$(date +%Y%m%d-%H%M%S)

log(){ echo "[$(date +%H:%M:%S)] $*"; }
remote(){ ssh -o BatchMode=yes "$HOST" "$@"; }

healthy(){ # 0 si /health ET /ready OK
  curl -fsS --max-time 6 "$HEALTH" >/dev/null 2>&1 && curl -fsS --max-time 6 "$READY" >/dev/null 2>&1
}

log "1) Backup du code prod -> ${REMOTE_APP}.bak-$ts"
remote "bash -c '$SUDO cp -a $REMOTE_APP ${REMOTE_APP}.bak-$ts'" || { log "backup échoué, abandon"; exit 1; }

log "2) Transfert du code repo ($LOCAL_APP) -> .161"
tar czf - -C "$LOCAL_APP" . | remote "cat > /tmp/helios-app-$ts.tgz"

log "3) Déploiement (remplace le contenu de app/, préserve owner)"
remote "bash -c '$SUDO rm -rf /tmp/newapp && mkdir -p /tmp/newapp && tar xzf /tmp/helios-app-$ts.tgz -C /tmp/newapp && \
  $SUDO cp -a /tmp/newapp/. $REMOTE_APP/ && \
  $SUDO chown -R --reference=${REMOTE_APP}.bak-$ts $REMOTE_APP'"

log "4) Restart $SVC"
remote "bash -c '$SUDO systemctl restart $SVC'"

log "5) Vérif santé (max 90s)..."
ok=1
for i in $(seq 1 18); do
  if healthy; then ok=0; log "   service SAIN ✓"; break; fi
  sleep 5
done

if [ $ok -ne 0 ]; then
  log "❌ service NON sain après déploiement -> ROLLBACK"
  remote "bash -c '$SUDO rm -rf $REMOTE_APP && $SUDO mv ${REMOTE_APP}.bak-$ts $REMOTE_APP && $SUDO systemctl restart $SVC'"
  for i in $(seq 1 12); do healthy && { log "   rollback OK, service rétabli ✓"; break; }; sleep 5; done
  log "ROLLBACK effectué. Aucun changement conservé."
  exit 1
fi

log "6) Sanity détection (monitor actif ?)"
curl -fsS --max-time 6 http://192.168.148.161:8000/monitor/status 2>/dev/null | grep -o '"running":[a-z]*' || true
log "✅ Déploiement réussi. Backup conservé : ${REMOTE_APP}.bak-$ts (supprimer après validation)."
log "   RE-TESTER la détection (ex. scripts/helios-tests/test-worker-ha.sh) avant la démo."
