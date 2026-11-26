#!/usr/bin/env bash
# =============================================================================
# HELIOS — Verification complete du cluster HA
# =============================================================================
# EXECUTER SUR N'IMPORTE QUEL CONTROL-PLANE en root.
# Produit un rapport de sante complet.
# =============================================================================
set -uo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'
PASS=0
FAIL=0
WARN=0

check() {
    local desc="$1" result="$2"
    if [ "$result" = "OK" ]; then
        echo -e "  ${GREEN}[OK]${NC} $desc"
        ((PASS++))
    elif [ "$result" = "WARN" ]; then
        echo -e "  ${YELLOW}[WARN]${NC} $desc"
        ((WARN++))
    else
        echo -e "  ${RED}[FAIL]${NC} $desc"
        ((FAIL++))
    fi
}

echo "============================================================="
echo "       HELIOS — RAPPORT DE VERIFICATION COMPLET"
echo "       $(date)"
echo "============================================================="
echo ""

# ── 1. NOEUDS ─────────────────────────────────────────────────────────────────
echo "=== 1. NOEUDS ==="
NODES_TOTAL=$(kubectl get nodes --no-headers 2>/dev/null | wc -l)
NODES_READY=$(kubectl get nodes --no-headers 2>/dev/null | grep -c " Ready")
CP_NODES=$(kubectl get nodes -l node-role.kubernetes.io/control-plane --no-headers 2>/dev/null | wc -l)

check "Nombre de noeuds : $NODES_TOTAL" "$([ "$NODES_TOTAL" -ge 3 ] && echo OK || echo FAIL)"
check "Noeuds Ready : $NODES_READY/$NODES_TOTAL" "$([ "$NODES_READY" -eq "$NODES_TOTAL" ] && echo OK || echo FAIL)"
check "Control-plane : $CP_NODES" "$([ "$CP_NODES" -ge 3 ] && echo OK || ([ "$CP_NODES" -ge 2 ] && echo WARN || echo FAIL))"

# Verifier label node-type=worker
WORKER_LABEL=$(kubectl get nodes -l node-type=worker --no-headers 2>/dev/null | wc -l)
check "Label node-type=worker : $WORKER_LABEL noeuds" "$([ "$WORKER_LABEL" -ge 3 ] && echo OK || echo WARN)"

# Verifier taint control-plane
TAINTED=$(kubectl get nodes -o json 2>/dev/null | python3 -c "
import json,sys
d=json.load(sys.stdin)
tainted=0
for n in d['items']:
    for t in n.get('spec',{}).get('taints',[]):
        if t.get('key')=='node-role.kubernetes.io/control-plane':
            tainted+=1
print(tainted)
" 2>/dev/null || echo "?")
check "Taint control-plane retire : $TAINTED noeuds tainted" "$([ "$TAINTED" = "0" ] && echo OK || echo WARN)"

kubectl get nodes -o wide 2>/dev/null
echo ""

# ── 2. ETCD ───────────────────────────────────────────────────────────────────
echo "=== 2. ETCD ==="
if command -v etcdctl &>/dev/null; then
    ETCD_OPTS="--endpoints=https://127.0.0.1:2379 --cacert=/etc/kubernetes/pki/etcd/ca.crt --cert=/etc/kubernetes/pki/etcd/server.crt --key=/etc/kubernetes/pki/etcd/server.key"
    ETCD_MEMBERS=$(ETCDCTL_API=3 etcdctl member list $ETCD_OPTS 2>/dev/null | grep -c "started" || echo 0)
    check "Membres etcd started : $ETCD_MEMBERS/3" "$([ "$ETCD_MEMBERS" -ge 3 ] && echo OK || ([ "$ETCD_MEMBERS" -ge 2 ] && echo WARN || echo FAIL))"

    # Leader
    ETCD_LEADER=$(ETCDCTL_API=3 etcdctl endpoint status $ETCD_OPTS --write-out=json 2>/dev/null | python3 -c "
import json,sys
d=json.loads(sys.stdin.read())
for e in d:
    if e.get('Status',{}).get('header',{}).get('member_id') == e.get('Status',{}).get('leader'):
        print('ce noeud')
        break
else:
    print('autre noeud')
" 2>/dev/null || echo "?")
    check "Leader etcd : $ETCD_LEADER" "OK"

    ETCDCTL_API=3 etcdctl member list $ETCD_OPTS --write-out=table 2>/dev/null || true
else
    check "etcdctl disponible" "FAIL"
fi
echo ""

# ── 3. PODS SYSTEME ───────────────────────────────────────────────────────────
echo "=== 3. COMPOSANTS CONTROL-PLANE ==="
for COMP in kube-apiserver kube-controller-manager kube-scheduler etcd; do
    COUNT=$(kubectl -n kube-system get pods -l component=$COMP --no-headers 2>/dev/null | grep -c "Running" || echo 0)
    check "$COMP instances Running : $COUNT" "$([ "$COUNT" -ge 3 ] && echo OK || ([ "$COUNT" -ge 2 ] && echo WARN || echo FAIL))"
done

# Leader elections
for LEASE in kube-scheduler kube-controller-manager; do
    HOLDER=$(kubectl -n kube-system get lease $LEASE -o jsonpath='{.spec.holderIdentity}' 2>/dev/null || echo "N/A")
    check "$LEASE leader : $HOLDER" "$([ "$HOLDER" != "N/A" ] && echo OK || echo FAIL)"
done
echo ""

# ── 4. HAProxy ────────────────────────────────────────────────────────────────
echo "=== 4. HAPROXY ==="
for IP in 192.168.148.160 192.168.148.162; do
    STATUS=$(curl -s -o /dev/null -w "%{http_code}" "http://${IP}:8404/" 2>/dev/null || echo "000")
    check "HAProxy stats $IP : HTTP $STATUS" "$([ "$STATUS" = "200" ] && echo OK || echo WARN)"
done

# API via HAProxy
API_STATUS=$(curl -sk -o /dev/null -w "%{http_code}" "https://192.168.148.160:6443/healthz" 2>/dev/null || echo "000")
check "API via HAProxy .160:6443 : HTTP $API_STATUS" "$([ "$API_STATUS" = "200" ] && echo OK || echo FAIL)"
echo ""

# ── 5. KEEPALIVED / VIP ──────────────────────────────────────────────────────
echo "=== 5. KEEPALIVED / VIP ==="
VIP_REACHABLE=$(ping -c 1 -W 2 192.168.148.150 &>/dev/null && echo "oui" || echo "non")
check "VIP 192.168.148.150 joignable : $VIP_REACHABLE" "$([ "$VIP_REACHABLE" = "oui" ] && echo OK || echo WARN)"
echo ""

# ── 6. MONITORING ─────────────────────────────────────────────────────────────
echo "=== 6. MONITORING ==="
for SVC in "Prometheus:192.168.148.160:9090/-/ready" "Grafana:192.168.148.160:3000/api/health" "Loki:192.168.148.160:3100/ready" "Alertmanager:192.168.148.160:9093/-/healthy"; do
    NAME="${SVC%%:*}"
    URL="http://${SVC#*:}"
    STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$URL" 2>/dev/null || echo "000")
    check "$NAME : HTTP $STATUS" "$([ "$STATUS" = "200" ] && echo OK || echo WARN)"
done

# Promtail DaemonSet
PROMTAIL=$(kubectl -n monitoring get ds promtail --no-headers 2>/dev/null | awk '{print $4}' || echo "0")
check "Promtail pods Ready : $PROMTAIL" "$([ "$PROMTAIL" -ge 3 ] && echo OK || ([ "$PROMTAIL" -ge 1 ] && echo WARN || echo FAIL))"
echo ""

# ── 7. APPLICATIONS ──────────────────────────────────────────────────────────
echo "=== 7. APPLICATIONS HELIOS ==="
for DEPLOY in backend frontend; do
    READY=$(kubectl -n pfe get deploy $DEPLOY -o jsonpath='{.status.readyReplicas}' 2>/dev/null || echo 0)
    DESIRED=$(kubectl -n pfe get deploy $DEPLOY -o jsonpath='{.spec.replicas}' 2>/dev/null || echo 0)
    check "$DEPLOY : $READY/$DESIRED Ready" "$([ "$READY" -ge "$DESIRED" ] && echo OK || echo FAIL)"
done

# PostgreSQL CNPG
PG_READY=$(kubectl -n pfe get cluster postgres -o jsonpath='{.status.readyInstances}' 2>/dev/null || echo 0)
check "PostgreSQL CNPG : $PG_READY instances Ready" "$([ "$PG_READY" -ge 2 ] && echo OK || echo WARN)"

# HPA
HPA_EXISTS=$(kubectl -n pfe get hpa backend-hpa --no-headers 2>/dev/null | wc -l || echo 0)
check "HPA backend : $([ "$HPA_EXISTS" -ge 1 ] && echo actif || echo absent)" "$([ "$HPA_EXISTS" -ge 1 ] && echo OK || echo WARN)"
echo ""

# ── 8. SECURITE ───────────────────────────────────────────────────────────────
echo "=== 8. SECURITE ==="
# etcd encryption
if command -v etcdctl &>/dev/null; then
    ENC=$(ETCDCTL_API=3 etcdctl get /registry/secrets/pfe/backend-secret $ETCD_OPTS --print-value-only 2>/dev/null | head -c 20 || echo "")
    check "etcd encryption au repos" "$(echo "$ENC" | grep -q "k8s:enc:" && echo OK || echo WARN)"
fi

# PSS
PSS=$(kubectl get ns pfe -o jsonpath='{.metadata.labels.pod-security\.kubernetes\.io/enforce}' 2>/dev/null || echo "none")
check "Pod Security Standards (pfe) : $PSS" "$([ "$PSS" = "baseline" ] && echo OK || echo WARN)"

# Audit logging
AUDIT=$(ls /var/log/kubernetes/audit/audit.log 2>/dev/null && echo "present" || echo "absent")
check "Audit logging : $AUDIT" "$([ "$AUDIT" = "present" ] && echo OK || echo WARN)"

# Falco
FALCO=$(kubectl -n falco get ds falco --no-headers 2>/dev/null | awk '{print $4}' || echo "0")
check "Falco DaemonSet : $FALCO pods" "$([ "$FALCO" -ge 1 ] && echo OK || echo WARN)"
echo ""

# ── 9. INGRESS + TLS ─────────────────────────────────────────────────────────
echo "=== 9. INGRESS ==="
INGRESS=$(kubectl -n ingress-nginx get pods --no-headers 2>/dev/null | grep -c "Running" || echo 0)
check "Ingress-nginx pods : $INGRESS" "$([ "$INGRESS" -ge 1 ] && echo OK || echo FAIL)"

TLS=$(kubectl -n pfe get secret helios-tls --no-headers 2>/dev/null | wc -l || echo 0)
check "Secret TLS helios-tls : $([ "$TLS" -ge 1 ] && echo present || echo absent)" "$([ "$TLS" -ge 1 ] && echo OK || echo WARN)"

APP_HTTP=$(curl -s -o /dev/null -w "%{http_code}" -H "Host: helios.orange.tn" "http://192.168.148.160/" 2>/dev/null || echo "000")
check "App via HAProxy HTTP : $APP_HTTP" "$(echo "$APP_HTTP" | grep -qE "200|301|302" && echo OK || echo WARN)"
echo ""

# ── RESUME ────────────────────────────────────────────────────────────────────
echo "============================================================="
echo -e "  RESULTAT : ${GREEN}$PASS OK${NC}  ${YELLOW}$WARN WARN${NC}  ${RED}$FAIL FAIL${NC}"
echo "============================================================="

if [ "$FAIL" -eq 0 ] && [ "$WARN" -le 2 ]; then
    echo -e "  ${GREEN}CLUSTER HA PROFESSIONNEL — PRET POUR LA SOUTENANCE${NC}"
elif [ "$FAIL" -eq 0 ]; then
    echo -e "  ${YELLOW}CLUSTER FONCTIONNEL — Quelques points a ameliorer${NC}"
else
    echo -e "  ${RED}PROBLEMES DETECTES — Corriger avant la soutenance${NC}"
fi
