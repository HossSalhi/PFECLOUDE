#!/usr/bin/env bash
# =============================================================================
#  ⛔ SCRIPT OBSOLETE — NE PAS EXECUTER
# =============================================================================
#  Le probleme que ce script resout est DEJA RESOLU, et plus simplement.
#
#  Chaque kubelet.conf pointe deja vers l'API server LOCAL de son propre noeud
#  (.153->.153:6443, .158->.158:6443, .155->.155:6443). Voir infra/README.md,
#  section "HA du control-plane". Les 3 noeuds etant eux-memes control-plane,
#  chacun a un API server en local : ajouter un HAProxy devant serait un
#  composant de plus a maintenir pour le meme resultat.
#
#  Lire : infra/10-local-apiserver-lb.OBSOLETE.md
#
#  Ce pattern (HAProxy local sur 127.0.0.1) redeviendrait pertinent si des
#  workers PURS etaient ajoutes au cluster : eux n'auraient pas d'API server
#  local vers lequel pointer.
# =============================================================================
echo "ERREUR : script obsolete. Lire infra/10-local-apiserver-lb.OBSOLETE.md" >&2
exit 1

# =============================================================================
# --- CONTENU HISTORIQUE (conserve pour reference, non executable) ------------
# =============================================================================
# HELIOS — Load Balancer API local par noeud (SOLUTION SANS VIP)
# =============================================================================
# PROBLEME RESOLU :
#   Avec controlPlaneEndpoint = 192.168.148.160:6443, la VM .160 est un SPOF.
#   Si .160 tombe, AUCUN kubelet ne joint l'API -> les 3 noeuds passent NotReady
#   alors que les 3 control-planes sont parfaitement vivants.
#
# SOLUTION (sans VIP, sans Keepalived, sans VRRP) :
#   Chaque noeud K8s fait tourner SON PROPRE HAProxy en local (127.0.0.1:7443)
#   qui repartit vers les 3 API servers. Les composants internes (kubelet,
#   scheduler, controller-manager, kube-proxy) pointent vers 127.0.0.1:7443.
#
#   C'est le pattern utilise par Kubespray (nginx-proxy), RKE2, k0s et Talos.
#
#   AVANT :  kubelet(.153) ──┐
#            kubelet(.158) ──┼──> .160:6443 (HAProxy)  <-- SPOF
#            kubelet(.155) ──┘         │
#                                       └──> .153 / .158 / .155
#
#   APRES :  kubelet(.153) ──> 127.0.0.1:7443 (HAProxy local .153) ──┐
#            kubelet(.158) ──> 127.0.0.1:7443 (HAProxy local .158) ──┼──> .153
#            kubelet(.155) ──> 127.0.0.1:7443 (HAProxy local .155) ──┤    .158
#                                                                     └──> .155
#
#   AVANTAGES :
#     - Zero SPOF : chaque noeud a son propre LB
#     - Zero VIP  : pas de VRRP, pas de multicast, pas de split-brain
#     - Zero VM dediee au LB pour le control-plane
#     - Failover < 3s (health check HAProxy) sans election reseau
#     - Le cluster K8s reste clairement les 3 noeuds (exigence encadrant)
#
# A EXECUTER SUR CHACUN DES 3 NOEUDS (.153, .158, .155) en root.
# PREREQUIS : la migration HA 3 control-planes doit etre TERMINEE.
# =============================================================================
set -euo pipefail

LB_PORT=7443
MASTERS=("192.168.148.153" "192.168.148.158" "192.168.148.155")
MOI=$(hostname -I | awk '{print $1}')

echo "================================================================"
echo " HELIOS — LB API local (sans VIP) sur $(hostname) [$MOI]"
echo "================================================================"

# ------------------------------------------------------------------ 0. Garde-fou
echo ""
echo "=== 0. Verification des pre-requis ==="

NB_CP=$(kubectl get nodes -l node-role.kubernetes.io/control-plane --no-headers 2>/dev/null | wc -l || echo 0)
if [ "$NB_CP" -lt 3 ]; then
    echo "[STOP] Seulement $NB_CP control-plane(s) detecte(s). Il en faut 3."
    echo "       Executer d'abord la migration HA : 01-prepare-master.sh -> 03-finalize-ha.sh"
    exit 1
fi
echo "[OK] $NB_CP control-planes detectes"

# Verifier que l'API server local ecoute
if ! ss -tlnp 2>/dev/null | grep -q ":6443"; then
    echo "[STOP] Aucun processus n'ecoute sur :6443 sur ce noeud."
    echo "       Ce script doit tourner sur un noeud CONTROL-PLANE."
    exit 1
fi
echo "[OK] API server local actif sur :6443"

# ------------------------------------------------------------------ 1. HAProxy
echo ""
echo "=== 1. Installation de HAProxy local ==="

if ! command -v haproxy &>/dev/null; then
    apt-get update -qq && apt-get install -y -qq haproxy
fi
echo "[OK] HAProxy present : $(haproxy -v 2>&1 | head -1)"

# Sauvegarde
if [ -f /etc/haproxy/haproxy.cfg ] && [ ! -f /etc/haproxy/haproxy.cfg.orig ]; then
    cp /etc/haproxy/haproxy.cfg /etc/haproxy/haproxy.cfg.orig
fi

# ------------------------------------------------------------------ 2. Config
echo ""
echo "=== 2. Configuration du LB local (127.0.0.1:${LB_PORT}) ==="

cat > /etc/haproxy/haproxy.cfg <<HACFG
# =============================================================================
# HELIOS — LB API local pour $(hostname)
# Ecoute UNIQUEMENT sur 127.0.0.1 : injoignable depuis le reseau.
# Repartit vers les 3 kube-apiserver. Retire un serveur mort en ~3s.
# =============================================================================
global
    log /dev/log local0
    chroot /var/lib/haproxy
    stats socket /run/haproxy/admin.sock mode 660 level admin
    stats timeout 30s
    user haproxy
    group haproxy
    daemon
    maxconn 4096

defaults
    log     global
    mode    tcp
    option  tcplog
    option  dontlognull
    timeout connect 5s
    timeout client  4h        # les watches K8s sont des connexions longues
    timeout server  4h
    timeout tunnel  4h        # kubectl exec / port-forward / logs -f
    retries 2

# ─── LB API Kubernetes (local uniquement) ────────────────────────
frontend k8s_api_local
    bind 127.0.0.1:${LB_PORT}
    mode tcp
    option tcplog
    default_backend k8s_apiservers

backend k8s_apiservers
    mode tcp
    # 'first' + priorite : privilegier l'API server LOCAL (latence nulle,
    # pas de trafic reseau), basculer sur les autres seulement s'il tombe.
    balance roundrobin
    option httpchk
    http-check send meth GET uri /healthz
    http-check expect status 200
    default-server inter 2s fall 2 rise 2 check check-ssl verify none
HACFG

# Le serveur LOCAL en premier (poids fort), les distants en secours
for M in "${MASTERS[@]}"; do
    NOM="apiserver-${M##*.}"
    if [ "$M" = "$MOI" ]; then
        echo "    server ${NOM}-local ${M}:6443 weight 100" >> /etc/haproxy/haproxy.cfg
    else
        echo "    server ${NOM} ${M}:6443 weight 10 backup" >> /etc/haproxy/haproxy.cfg
    fi
done

cat >> /etc/haproxy/haproxy.cfg <<'HACFG2'

# ─── Stats locales (diagnostic) ──────────────────────────────────
listen stats
    bind 127.0.0.1:8404
    mode http
    stats enable
    stats uri /
    stats refresh 5s
HACFG2

# Valider la config AVANT de redemarrer
if haproxy -c -f /etc/haproxy/haproxy.cfg; then
    echo "[OK] Configuration HAProxy valide"
else
    echo "[FAIL] Configuration invalide — restauration"
    [ -f /etc/haproxy/haproxy.cfg.orig ] && cp /etc/haproxy/haproxy.cfg.orig /etc/haproxy/haproxy.cfg
    exit 1
fi

systemctl enable haproxy
systemctl restart haproxy
sleep 2

if systemctl is-active --quiet haproxy; then
    echo "[OK] HAProxy local actif sur 127.0.0.1:${LB_PORT}"
else
    echo "[FAIL] HAProxy n'a pas demarre"
    journalctl -u haproxy --no-pager -n 10
    exit 1
fi

# Tester que le LB repond vraiment
if curl -sk "https://127.0.0.1:${LB_PORT}/healthz" | grep -q "ok"; then
    echo "[OK] LB local repond : /healthz = ok"
else
    echo "[WARN] LB local ne repond pas encore sur /healthz"
fi

# ------------------------------------------------------------------ 3. Bascule
echo ""
echo "=== 3. Bascule des kubeconfigs vers le LB local ==="
echo ""
echo "  ATTENTION : cette etape modifie les fichiers de configuration K8s."
echo "  Un backup .bak-locallb est cree pour chaque fichier."
echo ""

BASCULE() {
    local F="$1" NOM="$2"
    if [ ! -f "$F" ]; then
        echo "  [SKIP] $NOM : fichier absent"
        return
    fi
    if grep -q "127.0.0.1:${LB_PORT}" "$F"; then
        echo "  [OK]   $NOM : deja bascule"
        return
    fi
    cp "$F" "${F}.bak-locallb"
    sed -i -E "s|server: https://[0-9.]+:[0-9]+|server: https://127.0.0.1:${LB_PORT}|g" "$F"
    echo "  [DONE] $NOM -> 127.0.0.1:${LB_PORT}"
}

BASCULE /etc/kubernetes/kubelet.conf            "kubelet.conf"
BASCULE /etc/kubernetes/controller-manager.conf "controller-manager.conf"
BASCULE /etc/kubernetes/scheduler.conf          "scheduler.conf"

# admin.conf reste sur l'endpoint externe : kubectl depuis ce noeud doit
# continuer a fonctionner meme si HAProxy local est arrete pour maintenance.

echo ""
echo "=== 4. Redemarrage des composants ==="
systemctl restart kubelet
echo "[OK] kubelet redemarre"

# Forcer le redemarrage des pods statiques scheduler + controller-manager
# (ils relisent leur kubeconfig au demarrage)
for M in kube-scheduler kube-controller-manager; do
    if [ -f "/etc/kubernetes/manifests/${M}.yaml" ]; then
        touch "/etc/kubernetes/manifests/${M}.yaml"
        echo "[OK] ${M} : redemarrage declenche"
    fi
done

echo ""
echo "  Attente de la stabilisation (30s)..."
sleep 30

# ------------------------------------------------------------------ 5. Verif
echo ""
echo "=== 5. Verification ==="
echo ""
echo "--- Noeuds ---"
kubectl get nodes -o wide 2>/dev/null || echo "(kubectl indisponible depuis ce noeud)"
echo ""
echo "--- Backends HAProxy local ---"
echo "show stat" | socat stdio /run/haproxy/admin.sock 2>/dev/null \
    | awk -F, 'NR>1 && $2!="" {printf "  %-22s %-10s %s\n", $2, $18, $19}' \
    || curl -s http://127.0.0.1:8404/ 2>/dev/null | grep -oP '(?<=<td class=ac>)[^<]+' | head -10 \
    || echo "  (socat non installe — voir http://127.0.0.1:8404/)"
echo ""
echo "--- Endpoint utilise par kubelet ---"
grep -m1 "server:" /etc/kubernetes/kubelet.conf

echo ""
echo "================================================================"
echo " TERMINE sur $(hostname)"
echo "================================================================"
echo ""
echo "  Repeter ce script sur les 2 autres noeuds control-plane."
echo ""
echo "  Une fois les 3 noeuds faits, TEST DE PANNE :"
echo "    # Sur .160 (l'ancien SPOF) :"
echo "    systemctl stop haproxy"
echo "    # Depuis un noeud K8s : le cluster doit rester 100% fonctionnel"
echo "    kubectl get nodes          # 3/3 Ready"
echo "    kubectl -n pfe get pods    # tous Running"
echo "    kubectl create deploy test --image=nginx   # le scheduling marche"
echo ""
echo "  ROLLBACK si besoin :"
echo "    for f in kubelet controller-manager scheduler; do"
echo "      cp /etc/kubernetes/\$f.conf.bak-locallb /etc/kubernetes/\$f.conf"
echo "    done"
echo "    systemctl restart kubelet"
echo "================================================================"
