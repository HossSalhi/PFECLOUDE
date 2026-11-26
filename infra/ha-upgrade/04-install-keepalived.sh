#!/usr/bin/env bash
# =============================================================================
# HELIOS — Phase 4 : Installer Keepalived pour HAProxy HA (Active/Passive)
# =============================================================================
# EXECUTER SUR .160 ET .162 en root.
#
# VIP : 192.168.148.150
# .160 = MASTER (priorite 100) — HAProxy + Prometheus + Grafana + Loki
# .162 = BACKUP (priorite 90)  — HAProxy seul
#
# Le trafic arrive sur la VIP (.150), Keepalived la maintient sur .160.
# Si .160 tombe, .162 prend la VIP en ~3s.
# =============================================================================
set -euo pipefail

THIS_IP=$(hostname -I | awk '{print $1}')
echo "=== Installation Keepalived sur $(hostname) ($THIS_IP) ==="

# ── 1. Installer keepalived ──────────────────────────────────────────────────
echo "[1/4] Installation de keepalived..."
apt-get update -qq && apt-get install -y -qq keepalived

# ── 2. Detecter l'interface reseau ────────────────────────────────────────────
IFACE=$(ip route get 192.168.148.1 | awk '{for(i=1;i<=NF;i++) if ($i=="dev") print $(i+1)}' | head -1)
echo "[2/4] Interface detectee : $IFACE"

# ── 3. Deployer la configuration ─────────────────────────────────────────────
echo "[3/4] Deploiement de la configuration..."

if [[ "$THIS_IP" == *"160"* ]]; then
    ROLE="MASTER"
    PRIORITY=100
    ROUTER_ID="HELIOS_LB_160"
elif [[ "$THIS_IP" == *"162"* ]]; then
    ROLE="BACKUP"
    PRIORITY=90
    ROUTER_ID="HELIOS_LB_162"
else
    echo "ERREUR: Ce script doit etre execute sur .160 ou .162"
    exit 1
fi

cat > /etc/keepalived/keepalived.conf << EOF
global_defs {
    router_id ${ROUTER_ID}
    enable_script_security
    script_user root
}

vrrp_script check_haproxy {
    script "/usr/bin/killall -0 haproxy"
    interval 2
    weight -30
    fall 3
    rise 2
}

vrrp_instance HELIOS_VIP {
    state ${ROLE}
    interface ${IFACE}
    virtual_router_id 51
    priority ${PRIORITY}
    advert_int 1

    authentication {
        auth_type PASS
        auth_pass H3l10sVIP
    }

    virtual_ipaddress {
        192.168.148.150/24
    }

    track_script {
        check_haproxy
    }

    notify_master "/bin/echo 'HELIOS VIP: $(hostname) est MASTER' | logger -t keepalived"
    notify_backup "/bin/echo 'HELIOS VIP: $(hostname) est BACKUP' | logger -t keepalived"
    notify_fault  "/bin/echo 'HELIOS VIP: $(hostname) est en FAULT' | logger -t keepalived"
}
EOF

echo "  Role : $ROLE (priorite $PRIORITY)"

# ── 4. Demarrer keepalived ────────────────────────────────────────────────────
echo "[4/4] Demarrage de keepalived..."
systemctl enable keepalived
systemctl restart keepalived
sleep 3

# Verifier
ip addr show "$IFACE" | grep "192.168.148.150" && \
    echo "  VIP 192.168.148.150 active sur $IFACE" || \
    echo "  VIP pas sur cette interface (normal si BACKUP)"

echo ""
echo "=== Keepalived installe ($ROLE) ==="
echo ""
echo "Test : arreter HAProxy sur .160 -> la VIP doit basculer sur .162 en ~3s"
echo "  Sur .160 : systemctl stop haproxy"
echo "  Sur .162 : ip addr show $IFACE | grep 150  (doit apparaitre)"
echo "  Puis : systemctl start haproxy  (sur .160, la VIP revient)"
