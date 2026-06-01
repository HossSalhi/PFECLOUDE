#!/usr/bin/env bash
# =============================================================================
# HELIOS AIOps — Script de démonstration PFE Orange
# Usage: ./demo.sh [status|cpu|memory|crash|clean]
# =============================================================================
set -euo pipefail

MASTER="192.168.148.153"
AI="192.168.148.161"
MAILHOG_UI="http://192.168.148.161:8025"
HELIOS_UI="http://192.168.148.153:30880"
NS="pfe"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
info()    { echo -e "${BLUE}[INFO]${NC} $*"; }
success() { echo -e "${GREEN}[OK]${NC}   $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*"; }

get_demo_pod() {
    ssh -o StrictHostKeyChecking=no kube@$MASTER \
        "kubectl -n $NS get pod -l app=demo-app -o jsonpath='{.items[0].metadata.name}' 2>/dev/null"
}

# ── STATUS ────────────────────────────────────────────────────────────────────
demo_status() {
    echo ""
    echo "══════════════════════════════════════════════════════"
    echo "  HELIOS AIOps — État du système"
    echo "══════════════════════════════════════════════════════"
    echo ""
    info "Pods pfe namespace:"
    ssh -o StrictHostKeyChecking=no kube@$MASTER "kubectl -n $NS get pods --no-headers" 2>/dev/null | \
        awk '{printf "  %-42s %-12s\n", $1, $4}'
    echo ""
    info "Incidents actifs en DB:"
    ssh -o StrictHostKeyChecking=no kube@$MASTER \
        "kubectl -n $NS exec postgres-4 -- psql -U postgres pfe_db -t -c \
        \"SELECT '  #'||id||' ['||status||'] '||app||' — '||substring(title,1,50) FROM incident \
         WHERE status IN ('NEW','OPEN','IN_PROGRESS','WAITING_FOR_APPROVAL') ORDER BY id DESC LIMIT 5;\"" 2>/dev/null | \
        grep -v "^$" || echo "  (aucun incident actif)"
    echo ""
    info "AI service:   http://$AI:8000/health"
    info "MailHog:      $MAILHOG_UI"
    info "Interface UI: $HELIOS_UI"
    echo ""
}

# ── CPU STRESS ────────────────────────────────────────────────────────────────
demo_cpu() {
    info "Injection CPU stress sur demo-app (4 workers)..."
    POD=$(get_demo_pod)
    [[ -z "$POD" ]] && { error "demo-app pod non trouvé"; exit 1; }

    for i in 1 2 3 4; do
        ssh -o StrictHostKeyChecking=no kube@$MASTER \
            "kubectl -n $NS exec $POD -- sh -c 'nohup sh -c \"while :; do :; done\" &>/dev/null &'" 2>/dev/null
    done
    success "CPU stress injecté sur $POD (4 boucles infinies)"
    echo ""
    warn "Détection AI dans ~3-5 minutes. Vérifiez ensuite:"
    echo "  • UI: $HELIOS_UI"
    echo "  • Emails: $MAILHOG_UI"
    echo "  • Nettoyage: ./demo.sh clean"
}

# ── MEMORY PRESSURE (simulation directe AI) ───────────────────────────────────
demo_memory() {
    info "Test détection MEMORY_PRESSURE (inject via feature_overrides)..."
    RESULT=$(curl -s -X POST "http://$AI:8000/predict" \
        -H 'Content-Type: application/json' \
        -d '{"namespace":"pfe","app":"demo-app","window_minutes":5,
             "feature_overrides":{"memory_working_set_bytes":1900000000,
             "metric_risk_score":0.88,"overall_risk_score":0.88}}' 2>/dev/null)
    [[ -z "$RESULT" ]] && { error "AI service non disponible"; exit 1; }
    INCIDENT=$(echo "$RESULT" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['incident'])")
    TYPE=$(echo "$RESULT"     | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('incident_type','?'))")
    SEV=$(echo "$RESULT"      | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('severity','?'))")
    CONF=$(echo "$RESULT"     | python3 -c "import json,sys; d=json.load(sys.stdin); print(f\"{d.get('confidence',0):.2f}\")")
    if [[ "$INCIDENT" == "True" ]]; then
        success "DÉTECTÉ — type=$TYPE severity=$SEV confidence=$CONF"
    else
        warn "Non détecté — type=$TYPE confidence=$CONF (modèle OK, seuil non atteint)"
    fi
}

# ── DISK PRESSURE (simulation directe AI) ─────────────────────────────────────
demo_disk() {
    info "Test détection DISK_PRESSURE (inject via feature_overrides)..."
    RESULT=$(curl -s -X POST "http://$AI:8000/predict" \
        -H 'Content-Type: application/json' \
        -d '{"namespace":"pfe","app":"demo-app","window_minutes":5,
             "feature_overrides":{"metric_risk_score":0.82,"overall_risk_score":0.82,
             "error_ratio":0.15,"error_line_count":50.0}}' 2>/dev/null)
    [[ -z "$RESULT" ]] && { error "AI service non disponible"; exit 1; }
    INCIDENT=$(echo "$RESULT" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['incident'])")
    TYPE=$(echo "$RESULT"     | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('incident_type','?'))")
    CONF=$(echo "$RESULT"     | python3 -c "import json,sys; d=json.load(sys.stdin); print(f\"{d.get('confidence',0):.2f}\")")
    if [[ "$INCIDENT" == "True" ]]; then
        success "DÉTECTÉ — type=$TYPE confidence=$CONF"
    else
        warn "Non détecté — type=$TYPE confidence=$CONF"
    fi
}

# ── CRASH LOOP ────────────────────────────────────────────────────────────────
demo_crash() {
    info "Simulation crash loop (suppression pod demo-app)..."
    POD=$(get_demo_pod)
    [[ -z "$POD" ]] && { error "demo-app pod non trouvé"; exit 1; }
    ssh -o StrictHostKeyChecking=no kube@$MASTER \
        "kubectl -n $NS delete pod $POD --grace-period=0" 2>/dev/null
    success "Pod $POD supprimé — K8s recrée (restart_count +1)"
    warn "Détection AI dans ~2 minutes"
}

# ── CLEANUP ───────────────────────────────────────────────────────────────────
demo_clean() {
    info "Nettoyage du chaos..."
    POD=$(get_demo_pod)
    if [[ -n "$POD" ]]; then
        ssh -o StrictHostKeyChecking=no kube@$MASTER \
            "kubectl -n $NS exec $POD -- pkill -9 sh 2>/dev/null || true"
        success "CPU stress stoppé sur $POD"
    fi
    echo ""
    info "Incidents actifs restants:"
    ssh -o StrictHostKeyChecking=no kube@$MASTER \
        "kubectl -n $NS exec postgres-4 -- psql -U postgres pfe_db -t -c \
        \"SELECT '  #'||id||' ['||status||'] '||app FROM incident \
         WHERE status IN ('NEW','OPEN','IN_PROGRESS','WAITING_FOR_APPROVAL') ORDER BY id DESC;\"" 2>/dev/null | \
        grep -v "^$" || echo "  (aucun — propre!)"
}

# ── DISPATCH ──────────────────────────────────────────────────────────────────
CMD="${1:-status}"
case "$CMD" in
    cpu)    demo_cpu    ;;
    memory) demo_memory ;;
    disk)   demo_disk   ;;
    crash)  demo_crash  ;;
    clean)  demo_clean  ;;
    status) demo_status ;;
    *)
        echo "Usage: $0 [status|cpu|memory|disk|crash|clean]"
        echo ""
        echo "  status  — état: pods, incidents actifs, URLs"
        echo "  cpu     — injecte CPU stress (détection ~3-5min)"
        echo "  memory  — teste détection mémoire via simulation AI"
        echo "  disk    — teste détection erreurs disque via simulation AI"
        echo "  crash   — simule crash loop (supprime pod demo-app)"
        echo "  clean   — stoppe le chaos, liste incidents restants"
        exit 1 ;;
esac
