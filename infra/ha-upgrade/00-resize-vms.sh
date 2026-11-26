#!/usr/bin/env bash
# =============================================================================
# HELIOS — Phase 0 : Redimensionner les VMs pour le HA
# =============================================================================
# EXECUTER SUR L'HOTE VMWARE (pas dans une VM).
# Arrete toutes les VMs, modifie la RAM, puis les redémarre.
#
# Allocation AVANT (17,2 Go total) :
#   master=2560  worker1=3072  worker2=2560  monitoring=2048  aiCamunda=6144  lb2=768
#
# Allocation APRES (17,4 Go total) :
#   master=3584  worker1=3584  worker2=3584  monitoring=1536  aiCamunda=4608  lb2=512
#
# Justification :
#   - 3 noeuds K8s a 3,5 Go : etcd (~500Mo) + API Server (~256Mo) + workloads (~2Go) + OS
#   - monitoring 1,5 Go : Prometheus + Grafana + Loki (serré mais viable, Prometheus
#     delegue la retention longue a Thanos/MinIO en prod)
#   - aiCamunda 4,5 Go : Camunda/Zeebe (~1Go) + Ollama 1.5B model (~1,5Go) + AI service
#   - lb2 512 Mo : HAProxy seul (< 50 Mo en runtime)
# =============================================================================
set -euo pipefail

VMRUN="vmrun"
VM_DIR="${VM_DIR:-$HOME/Documents/Virtual Machines}"

echo "=== Phase 0 : Redimensionnement RAM des VMs ==="
echo ""
echo "ATTENTION : Toutes les VMs seront arretees puis redemarrees."
echo "Assurez-vous d'avoir des snapshots VMware AVANT de continuer."
echo ""
read -p "Continuer ? (oui/non) : " CONFIRM
[[ "$CONFIRM" != "oui" ]] && echo "Annule." && exit 0

# Nouvelle allocation RAM (en Mo)
declare -A NEW_RAM=(
    ["k8s-master"]=3584
    ["k8s-worker1"]=3584
    ["k8s-worker2"]=3584
    ["k8s-monitoring"]=1536
    ["k8s-aiCamunda"]=4608
    ["k8s-lb2"]=512
)

for VM_NAME in "${!NEW_RAM[@]}"; do
    VMX="$VM_DIR/$VM_NAME/$VM_NAME.vmx"
    RAM="${NEW_RAM[$VM_NAME]}"

    if [ ! -f "$VMX" ]; then
        echo "[SKIP] $VMX introuvable"
        continue
    fi

    echo ""
    echo "--- $VM_NAME : ${RAM} Mo ---"

    # Arreter la VM si elle tourne
    if $VMRUN list 2>/dev/null | grep -qi "$VM_NAME"; then
        echo "  Arret de $VM_NAME..."
        $VMRUN stop "$VMX" soft 2>/dev/null || $VMRUN stop "$VMX" hard 2>/dev/null || true
        sleep 5
    fi

    # Backup du VMX
    cp "$VMX" "${VMX}.bak-ha-$(date +%Y%m%d)"

    # Modifier la RAM
    if grep -q '^memsize' "$VMX"; then
        sed -i "s/^memsize = .*/memsize = \"$RAM\"/" "$VMX"
    else
        echo "memsize = \"$RAM\"" >> "$VMX"
    fi

    echo "  RAM mise a jour : ${RAM} Mo"
done

echo ""
echo "=== Redemarrage des VMs ==="
for VM_NAME in k8s-monitoring k8s-lb2 k8s-master k8s-worker1 k8s-worker2 k8s-aiCamunda; do
    VMX="$VM_DIR/$VM_NAME/$VM_NAME.vmx"
    [ -f "$VMX" ] || continue
    echo "  Demarrage $VM_NAME..."
    $VMRUN start "$VMX" nogui 2>/dev/null || echo "  [WARN] Echec demarrage $VM_NAME"
    sleep 10
done

echo ""
echo "=== Phase 0 terminee ==="
echo "Attendez 2-3 minutes que toutes les VMs soient pretes, puis executez 01-prepare-master.sh"
