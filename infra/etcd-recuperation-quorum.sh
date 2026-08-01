#!/usr/bin/env bash
# =============================================================================
# HELIOS — Récupération après perte de quorum etcd
# =============================================================================
# À utiliser quand il ne reste qu'UN SEUL nœud sur les 3 (2 masters perdus).
#
# RAPPEL DU PROBLÈME
#   etcd (Raft) exige une MAJORITÉ pour accepter une écriture : 2 nœuds sur 3.
#   Avec 1 seul nœud vivant, le cluster est FIGÉ — même les lectures échouent
#   (« etcdserver: request timed out »), car l'apiserver a besoin d'un leader.
#   Ce n'est pas un réglage Kubernetes : c'est une propriété du consensus, qui
#   empêche deux nœuds isolés d'accepter des écritures contradictoires.
#
#   Ce script reconstitue etcd en cluster À UN SEUL MEMBRE : le quorum devient
#   1, les écritures repassent, et le nœud survivant (qui est aussi worker)
#   fait tourner l'application complète.
#
# ⚠️  RISQUE DE SPLIT-BRAIN — LIRE AVANT D'EXÉCUTER
#   Après `recuperer`, les 2 autres nœuds NE DOIVENT PAS redémarrer avec leur
#   ancien /var/lib/etcd : ils croiraient encore appartenir à l'ancien cluster
#   et on aurait deux « vérités » incompatibles. Ils doivent être réintégrés
#   avec `rejoindre`, qui purge leur data-dir avant de les rattacher.
#   => Ne lancez `recuperer` que si les 2 autres sont RÉELLEMENT hors service.
#
# PROCÉDURE VALIDÉE EN CONDITIONS RÉELLES (31/07/2026, cluster HELIOS)
#   Perte 2/3 simulée -> écritures KO -> recuperer -> cluster opérationnel sur
#   1 nœud (pod ordonnancé et Running sur k8s-master, 1576 clés préservées)
#   -> rejoindre k8s-worker2 -> rejoindre k8s-worker1 -> 3/3 sains, quorum
#   rétabli, plateforme accessible en 200 sur les 3 nœuds.
#
# USAGE (à lancer EN SSH SUR LE NŒUD SURVIVANT, jamais depuis Windows)
#   sudo ./etcd-recuperation-quorum.sh sauvegarder
#   sudo ./etcd-recuperation-quorum.sh recuperer
#   sudo ./etcd-recuperation-quorum.sh rejoindre k8s-worker2 192.168.148.155
#   sudo ./etcd-recuperation-quorum.sh rejoindre k8s-worker1 192.168.148.158
#   sudo ./etcd-recuperation-quorum.sh etat
# =============================================================================
set -euo pipefail

PKI=/etc/kubernetes/pki/etcd
MANIFESTE=/etc/kubernetes/manifests/etcd.yaml
DATA_DIR=/var/lib/etcd
BACKUPS=/var/backups/etcd

ETCDCTL="ETCDCTL_API=3 etcdctl \
  --endpoints=https://127.0.0.1:2379 \
  --cacert=$PKI/ca.crt --cert=$PKI/server.crt --key=$PKI/server.key \
  --command-timeout=10s"

# le script tourne en root (sudo) : root n'a pas de ~/.kube/config, on pointe
# explicitement le kubeconfig admin, sinon les kubectl du script rendent du vide.
export KUBECONFIG=/etc/kubernetes/admin.conf

log() { echo "[$(date +%H:%M:%S)] $*"; }
err() { echo "[$(date +%H:%M:%S)] ERREUR: $*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || err "à lancer en root (sudo)."

# -----------------------------------------------------------------------------
sauvegarder() {
  mkdir -p "$BACKUPS"
  local f="$BACKUPS/snap-$(date +%Y%m%d-%H%M%S).db"
  log "snapshot etcd -> $f"
  eval "$ETCDCTL snapshot save $f" >/dev/null 2>&1 || err "snapshot impossible (etcd répond-il ?)"
  echo "$f" > "$BACKUPS/DERNIER_SNAPSHOT"
  ls -lh "$f"
  log "OK. Ce snapshot permet une restauration complète en dernier recours."
}

# -----------------------------------------------------------------------------
recuperer() {
  log "=== RECONSTITUTION EN CLUSTER MONO-MEMBRE ==="
  log "Vérifiez que les 2 AUTRES nœuds sont réellement hors service."
  log "Interruption dans 10 s (Ctrl-C pour annuler)..."; sleep 10

  log "[1/5] arrêt kubelet + etcd"
  systemctl stop kubelet
  for id in $(crictl ps -q --name etcd 2>/dev/null); do crictl stop "$id" >/dev/null 2>&1 || true; done
  sleep 5

  log "[2/5] copie de sécurité du data-dir -> ${DATA_DIR}.avant-force"
  rm -rf "${DATA_DIR}.avant-force"
  cp -a "$DATA_DIR" "${DATA_DIR}.avant-force"

  log "[3/5] ajout de --force-new-cluster au manifeste"
  cp "$MANIFESTE" /etc/kubernetes/etcd.yaml.original
  grep -q -- "--force-new-cluster" "$MANIFESTE" || \
    sed -i "s|^\( *\)- --data-dir=${DATA_DIR}|\1- --data-dir=${DATA_DIR}\n\1- --force-new-cluster|" "$MANIFESTE"

  log "[4/5] redémarrage kubelet — etcd repart seul membre"
  systemctl start kubelet
  local ok=0
  for i in $(seq 1 20); do
    if eval "$ETCDCTL endpoint status" >/dev/null 2>&1; then ok=1; log "etcd répond (~$((i*10))s)"; break; fi
    sleep 10
  done
  [ "$ok" -eq 1 ] || err "etcd n'est pas reparti — voir: crictl logs \$(crictl ps -aq --name etcd | head -1)"

  log "[5/5] retrait du flag (sinon CHAQUE redémarrage reforcerait un nouveau cluster)"
  sed -i "/- --force-new-cluster/d" "$MANIFESTE"
  sleep 45
  for i in $(seq 1 12); do
    eval "$ETCDCTL endpoint status" >/dev/null 2>&1 && break; sleep 10
  done

  log "=== TERMINÉ — le cluster tourne sur ce seul nœud ==="
  eval "$ETCDCTL member list -w table"
  kubectl get nodes 2>/dev/null || true
}

# -----------------------------------------------------------------------------
# À lancer DEPUIS LE NŒUD SAIN. Purge le data-dir du nœud à réintégrer par SSH,
# reconfigure son manifeste, puis démarre son kubelet.
rejoindre() {
  local nom="${1:-}" ip="${2:-}"
  [ -n "$nom" ] && [ -n "$ip" ] || err "usage: rejoindre <nom-noeud> <ip>"

  log "=== RÉINTÉGRATION DE $nom ($ip) ==="

  # ORDRE CRITIQUE : arrêter le kubelet du revenant AVANT tout le reste.
  # Une VM qui redémarre relance son kubelet toute seule, donc son ancien etcd,
  # qui porte l'ancien cluster-id -> il tenterait de rejoindre l'ancien cluster
  # et provoquerait exactement le split-brain qu'on veut éviter.
  log "[1/5] arrêt du kubelet/etcd sur $nom (avant toute chose)"
  ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8 "kube@${ip}" \
    "echo kube | sudo -S -p '' bash -c 'systemctl stop kubelet 2>/dev/null; \
     for id in \$(crictl ps -q --name etcd 2>/dev/null); do crictl stop \$id >/dev/null 2>&1; done; \
     for id in \$(crictl ps -q --name kube-apiserver 2>/dev/null); do crictl stop \$id >/dev/null 2>&1; done'" \
    || err "$nom injoignable en SSH — est-il vraiment redémarré ?"
  sleep 5

  # On identifie TOUJOURS un membre par son URL DE PEER, jamais par son nom :
  # un membre ajouté mais pas encore démarré (learner) a un NOM VIDE dans
  # `member list`. Filtrer sur le nom le rendrait invisible.
  local url="https://${ip}:2380"
  local ancien
  ancien=$(eval "$ETCDCTL member list" 2>/dev/null | awk -F', ' -v u="$url" '$4==u {print $1}' | head -1)
  if [ -n "$ancien" ]; then
    log "[2/5] retrait de l'ancienne identité de $nom (id=$ancien)"
    eval "$ETCDCTL member remove $ancien" | head -1
    sleep 3
  else
    log "[2/5] $nom n'est pas membre — rien à retirer"
  fi

  # ---- POURQUOI --learner EST OBLIGATOIRE ICI ----------------------------------
  # Ajouter un membre NORMAL à un cluster mono-membre fait passer le quorum de 1 à 2
  # alors qu'un seul membre est démarré => le cluster se fige INSTANTANÉMENT, le
  # `member add` lui-même échoue, et plus rien ne répond (constaté en test réel).
  # Un LEARNER réplique les données SANS compter dans le quorum : on l'ajoute sans
  # risque, il se synchronise, puis on le promeut membre votant.
  log "[3/5] déclaration du membre en LEARNER (ne casse pas le quorum)"
  eval "$ETCDCTL member add $nom --peer-urls=https://${ip}:2380 --learner" | head -2

  # --initial-cluster doit lister les membres DÉJÀ démarrés **+ le nœud qui rejoint**.
  # Le nœud qui rejoint n'y figure pas automatiquement : en tant que learner non
  # démarré son nom est vide dans `member list`. S'il manque, son etcd refuse de
  # démarrer avec « couldn't find local name in the initial cluster configuration ».
  local cluster
  cluster=$(eval "$ETCDCTL member list" \
    | awk -F', ' '{gsub(/ /,"",$3); gsub(/ /,"",$4); if ($3!="") printf "%s=%s,", $3, $4}')
  cluster="${cluster}${nom}=${url}"
  log "    initial-cluster = $cluster"

  log "[4/5] purge de l'ancien data-dir sur $nom (obligatoire : ancien cluster-id)"
  ssh -o StrictHostKeyChecking=no "kube@${ip}" \
    "echo kube | sudo -S -p '' bash -c 'rm -rf ${DATA_DIR}.ancien; mv ${DATA_DIR} ${DATA_DIR}.ancien 2>/dev/null; mkdir -p ${DATA_DIR}; chmod 700 ${DATA_DIR}'"

  log "[5/5] reconfiguration du manifeste etcd de $nom + démarrage"
  ssh -o StrictHostKeyChecking=no "kube@${ip}" \
    "echo kube | sudo -S -p '' bash -c '
       F=/etc/kubernetes/manifests-hors-service/etcd.yaml
       [ -f \$F ] || F=/etc/kubernetes/manifests/etcd.yaml
       sed -i \"s|^\\( *\\)- --initial-cluster=.*|\\1- --initial-cluster=${cluster}|\" \$F
       sed -i \"s|^\\( *\\)- --initial-cluster-state=.*|\\1- --initial-cluster-state=existing|\" \$F
       [ -f /etc/kubernetes/manifests-hors-service/etcd.yaml ] && mv /etc/kubernetes/manifests-hors-service/*.yaml /etc/kubernetes/manifests/ || true
       systemctl start kubelet'"

  log "    attente de la synchronisation du learner"
  local id="" ok=0
  for i in $(seq 1 24); do
    # ", started," et pas "started" : "unstarted" contient le mot et fausserait le compte
    local nb
    nb=$(eval "$ETCDCTL member list" 2>/dev/null | grep -c ", started," || true)
    id=$(eval "$ETCDCTL member list" 2>/dev/null | awk -F', ' -v u="$url" '$4==u {print $1}' | head -1)
    log "    membres démarrés = $nb | id learner = ${id:-?}"
    if [ -n "$id" ] && [ "$nb" -ge 1 ]; then
      # promote échoue tant que le learner n'a pas rattrapé son retard : on retente.
      if eval "$ETCDCTL member promote $id" >/dev/null 2>&1; then
        log "    $nom PROMU membre votant"; ok=1; break
      fi
    fi
    sleep 10
  done
  [ "$ok" -eq 1 ] || log "    ATTENTION: $nom reste learner (non votant) — relancer 'rejoindre' plus tard"

  log "    vérification finale"
  eval "$ETCDCTL endpoint health --cluster" 2>&1 | tail -5
  eval "$ETCDCTL member list -w table"
}

# -----------------------------------------------------------------------------
etat() {
  echo "=== MEMBRES ==="  ; eval "$ETCDCTL member list -w table"
  echo "=== SANTÉ ==="    ; eval "$ETCDCTL endpoint health --cluster -w table" 2>&1 | tail -8
  echo "=== NŒUDS K8S ===";  kubectl get nodes -o wide --no-headers 2>/dev/null | awk '{print $1, $2, $6}'
  echo "=== CONTROL-PLANE ==="
  kubectl -n kube-system get pods --no-headers 2>/dev/null \
    | grep -E "etcd-|apiserver|scheduler|controller-manager" | awk '{print $1, $3}' | sort
}

case "${1:-}" in
  sauvegarder) sauvegarder ;;
  recuperer)   recuperer ;;
  rejoindre)   rejoindre "${2:-}" "${3:-}" ;;
  etat)        etat ;;
  *) sed -n '2,45p' "$0"; exit 1 ;;
esac
