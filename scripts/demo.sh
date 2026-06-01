#!/usr/bin/env bash
# =============================================================================
# HELIOS AIOps — Démo naturelle (sans injection artificielle)
# Scénarios réels K8s : mauvais déploiement, panne service, nouvelle app
# Usage: ./demo.sh [status|bad-deploy|outage|new-app|restore|clean]
# =============================================================================
set -euo pipefail

MASTER="192.168.148.153"
NS="pfe"
HELIOS_UI="http://192.168.148.153:30880"
GOOD_IMAGE="nginxinc/nginx-unprivileged:1.29-alpine"
BAD_IMAGE="nginxinc/nginx-unprivileged:version-inexistante-xyz"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'
info()    { echo -e "${BLUE}[INFO]${NC} $*"; }
success() { echo -e "${GREEN}[OK]${NC}   $*"; }
warn()    { echo -e "${YELLOW}[ATTENTE]${NC} $*"; }
header()  { echo -e "\n${BOLD}═══ $* ═══${NC}\n"; }

# ── STATUS ────────────────────────────────────────────────────────────────────
demo_status() {
    header "HELIOS AIOps — État du système"

    info "Pods applicatifs (pfe namespace):"
    ssh -o StrictHostKeyChecking=no kube@$MASTER \
        "kubectl -n $NS get pods -l 'app in (backend,frontend,demo-app)' -o wide --no-headers" 2>/dev/null | \
        awk '{printf "  %-45s %-10s %s\n", $1, $4, $7}'

    echo ""
    info "Incidents actifs:"
    ssh -o StrictHostKeyChecking=no kube@$MASTER \
        "kubectl -n $NS exec postgres-4 -- psql -U postgres pfe_db -t -c \
        \"SELECT '  #'||id||' ['||status||'] '||namespace||'/'||app||' — '||incident_type \
         FROM incident WHERE status IN ('NEW','OPEN','IN_PROGRESS','WAITING_FOR_APPROVAL') \
         ORDER BY id DESC LIMIT 5;\"" 2>/dev/null | grep -v "^$" || echo "  (aucun incident actif — système sain)"

    echo ""
    info "Interfaces:"
    echo "  • HELIOS UI   : $HELIOS_UI"
    echo "  • Incidents   : $HELIOS_UI/incidents"
    echo "  • Tâches IA   : $HELIOS_UI/ai-decisions"
    echo "  • Remédiation : $HELIOS_UI/remediation"
    echo ""
}

# ── SCÉNARIO 1 : MAUVAIS DÉPLOIEMENT ─────────────────────────────────────────
# Ce qui se passe en production quand un dev pousse une image inexistante
demo_bad_deploy() {
    header "Scénario 1 — Mauvais déploiement (ImagePullBackOff)"

    info "Situation : un développeur a pushé une version avec une mauvaise image"
    info "Déploiement en cours : demo-app → image inexistante..."
    echo ""

    ssh -o StrictHostKeyChecking=no kube@$MASTER \
        "kubectl -n $NS set image deployment/demo-app demo=$BAD_IMAGE" 2>/dev/null
    success "Image erronée déployée : $BAD_IMAGE"

    echo ""
    warn "Dans ~30 secondes : K8s tente de télécharger l'image → ErrImagePull"
    warn "Dans ~2 minutes   : HELIOS détecte des replicas unavailable → incident créé"
    warn "Dans ~3 minutes   : HELIOS recommande un ROLLBACK_DEPLOYMENT"
    echo ""
    info "Surveille dans l'UI : $HELIOS_UI/incidents"
    info "Pour voir les pods : ssh kube@$MASTER kubectl -n $NS get pods -l app=demo-app -w"
    echo ""
    echo -e "${YELLOW}Quand l'incident apparaît dans l'UI → utilise './demo.sh restore' pour revenir à la normale${NC}"
}

# ── SCÉNARIO 2 : PANNE DE SERVICE ────────────────────────────────────────────
# Ce qui se passe quand un opérateur scale accidentellement à 0
demo_outage() {
    header "Scénario 2 — Panne de service (scale à 0)"

    info "Situation : un opérateur a accidentellement mis demo-app à 0 replicas"
    info "Exécution du scale-down..."
    echo ""

    ssh -o StrictHostKeyChecking=no kube@$MASTER \
        "kubectl -n $NS scale deployment/demo-app --replicas=0" 2>/dev/null
    success "demo-app : 0 replicas → service en panne"

    echo ""
    warn "Dans ~30 secondes : kube-state-metrics voit unavailable_replicas=2"
    warn "Dans ~2 minutes   : HELIOS détecte la panne → incident CRITICAL créé"
    warn "Dans ~3 minutes   : HELIOS auto-remédie (SCALE_DEPLOYMENT en mode auto)"
    echo ""
    info "Surveille dans l'UI : $HELIOS_UI/incidents"
    echo ""
    echo -e "${YELLOW}HELIOS va automatiquement rescaler à 2 replicas sans intervention${NC}"
}

# ── SCÉNARIO 3 : NOUVELLE APPLICATION ────────────────────────────────────────
# HELIOS surveille automatiquement toute nouvelle app déployée
demo_new_app() {
    header "Scénario 3 — Nouvelle application (auto-découverte)"

    info "Situation : l'équipe déploie une nouvelle application dans le cluster"
    info "Déploiement de 'mon-api' dans le namespace pfe..."
    echo ""

    ssh -o StrictHostKeyChecking=no kube@$MASTER "kubectl apply -f -" 2>/dev/null << 'YAML'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: mon-api
  namespace: pfe
  labels:
    app: mon-api
spec:
  replicas: 1
  selector:
    matchLabels:
      app: mon-api
  template:
    metadata:
      labels:
        app: mon-api
    spec:
      containers:
      - name: mon-api
        image: nginxinc/nginx-unprivileged:1.29-alpine
        ports:
        - containerPort: 8080
        resources:
          requests:
            cpu: 10m
            memory: 16Mi
          limits:
            cpu: 200m
            memory: 64Mi
YAML

    success "Application 'mon-api' déployée dans le namespace pfe"
    echo ""
    warn "Dans ~45 secondes : le watcher K8s de HELIOS détecte 'mon-api'"
    warn "Dans ~2 minutes   : première analyse IA automatique"
    warn "Aucune configuration nécessaire — HELIOS surveille tout automatiquement"
    echo ""
    info "Vérifier la surveillance dans les logs:"
    echo "  ssh kube@$MASTER kubectl -n $NS logs deploy/backend --since=3m | grep mon-api"
    echo ""
    echo -e "${YELLOW}Pour nettoyer : ./demo.sh clean${NC}"
}

# ── RESTORE ────────────────────────────────────────────────────────────────────
demo_restore() {
    header "Restauration — Retour à l'état normal"

    info "Restauration de l'image correcte sur demo-app..."
    ssh -o StrictHostKeyChecking=no kube@$MASTER \
        "kubectl -n $NS set image deployment/demo-app demo=$GOOD_IMAGE" 2>/dev/null && \
        success "Image restaurée : $GOOD_IMAGE"

    info "Rescale demo-app à 2 replicas..."
    ssh -o StrictHostKeyChecking=no kube@$MASTER \
        "kubectl -n $NS scale deployment/demo-app --replicas=2" 2>/dev/null && \
        success "demo-app → 2 replicas"

    echo ""
    info "Attente démarrage des pods..."
    ssh -o StrictHostKeyChecking=no kube@$MASTER \
        "kubectl -n $NS rollout status deployment/demo-app --timeout=60s" 2>/dev/null
    echo ""
    success "Système restauré — demo-app opérationnel"
}

# ── CLEAN ─────────────────────────────────────────────────────────────────────
demo_clean() {
    header "Nettoyage complet"

    demo_restore

    info "Suppression de mon-api (si déployée)..."
    ssh -o StrictHostKeyChecking=no kube@$MASTER \
        "kubectl -n $NS delete deployment mon-api --ignore-not-found" 2>/dev/null && \
        success "mon-api supprimée"

    echo ""
    info "Incidents actifs restants:"
    ssh -o StrictHostKeyChecking=no kube@$MASTER \
        "kubectl -n $NS exec postgres-4 -- psql -U postgres pfe_db -t -c \
        \"SELECT '  #'||id||' ['||status||'] '||app FROM incident \
         WHERE status IN ('NEW','OPEN','IN_PROGRESS','WAITING_FOR_APPROVAL') \
         ORDER BY id DESC LIMIT 5;\"" 2>/dev/null | \
        grep -v "^$" || echo "  (aucun — propre!)"
}

# ── DISPATCH ──────────────────────────────────────────────────────────────────
CMD="${1:-status}"
case "$CMD" in
    status)     demo_status     ;;
    bad-deploy) demo_bad_deploy ;;
    outage)     demo_outage     ;;
    new-app)    demo_new_app    ;;
    restore)    demo_restore    ;;
    clean)      demo_clean      ;;
    *)
        echo ""
        echo -e "${BOLD}HELIOS AIOps — Démo naturelle${NC}"
        echo ""
        echo "Usage: $0 [commande]"
        echo ""
        echo "  status       — état du système (pods, incidents, URLs)"
        echo "  bad-deploy   — Scénario 1 : mauvais déploiement (ImagePullBackOff)"
        echo "  outage       — Scénario 2 : panne de service (scale à 0)"
        echo "  new-app      — Scénario 3 : nouvelle app → auto-découverte HELIOS"
        echo "  restore      — restaurer demo-app à l'état normal"
        echo "  clean        — nettoyer tous les scénarios"
        echo ""
        echo "Flow suggéré pour la soutenance:"
        echo "  1. ./demo.sh status     (montrer l'état sain)"
        echo "  2. ./demo.sh bad-deploy (déclencher le scénario)"
        echo "  3. Ouvrir $HELIOS_UI/incidents"
        echo "  4. Attendre l'incident (2-3 min)"
        echo "  5. ./demo.sh restore    (ou laisser HELIOS gérer)"
        exit 1
        ;;
esac
