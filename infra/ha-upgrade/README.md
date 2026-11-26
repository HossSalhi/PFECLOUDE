# HELIOS — Migration vers HA Control Plane

## Architecture cible

```
    Client (navigateur)
            │
            │  http://helios.orange.tn
            ▼
    MetalLB IP .200 (K8s-natif, LoadBalancer)
            │
            ▼
    ingress-nginx (pod dans le cluster)
            │
            ▼
    Service frontend -> Pods frontend
    Service backend  -> Pods backend

   ┌────────────────┬─────────────────┐
   │                │                 │
VM .153          VM .158           VM .155
k8s-master       k8s-worker1       k8s-worker2
MASTER+WORKER    MASTER+WORKER     MASTER+WORKER
API+etcd+sched   API+etcd+sched   API+etcd+sched
backend pod      backend pod       backend pod
frontend pod     frontend pod      frontend pod
postgres         postgres
ingress-nginx    ingress-nginx     ingress-nginx
   │                │                 │
   └────────┬───────┴─────────────────┘
            │
            │  Control Plane LB (:6443 uniquement)
            ▼
    VM .160 (Monitoring)
    HAProxy (control-plane only)
    Prometheus + Grafana + Loki
    Alertmanager + Registry v2
            │
    VM .161 (aiCamunda)
    Camunda/Zeebe + AI + Ollama

    VM .162 (lb2) — reserve / standby
```

**Points cles :**
- MetalLB = LoadBalancer K8s-natif (pool .200-.210, mode L2/ARP)
- ingress-nginx en type LoadBalancer (plus de NodePort pour les apps)
- HAProxy sur .160 = UNIQUEMENT control-plane kubeadm (:6443)
- PAS de Keepalived / VIP (supprime — pas K8s-natif)
- 3 noeuds = control-plane + worker (chaque noeud fait tout)
- etcd 3 membres (quorum 2/3, tolere 1 panne)

## Pre-requis

- Snapshots VMware de TOUTES les VMs
- Snapshot etcd : `etcdctl snapshot save /tmp/etcd-pre-ha.db ...`
- PC hote : 24 Go RAM minimum

## RAM — Redistribution

| VM | Avant | Apres | Delta |
|---|---:|---:|---:|
| k8s-master | 2560 Mo | 3584 Mo | +1024 |
| k8s-worker1 | 3072 Mo | 3584 Mo | +512 |
| k8s-worker2 | 2560 Mo | 3584 Mo | +1024 |
| k8s-monitoring | 2048 Mo | 1536 Mo | -512 |
| k8s-aiCamunda | 6144 Mo | 4608 Mo | -1536 |
| k8s-lb2 | 768 Mo | 512 Mo | -256 |
| **Total** | **17152 Mo** | **17408 Mo** | **+256** |

## Procedure (ordre strict)

### Phase 0 — Redimensionner les VMs (sur l'hote)
```bash
./00-resize-vms.sh
```
Arrete toutes les VMs, modifie la RAM dans les VMX, redémarre.

### Phase 1 — Preparer le master (sur .153)
```bash
sudo ./01-prepare-master.sh
```
- Snapshot etcd
- Met a jour controlPlaneEndpoint → HAProxy .160:6443
- Regenere les certificats API Server (SANs : .153, .155, .158, .160, .150)
- Deploie l'audit policy
- Met a jour tous les kubeconfigs → .160:6443
- Upload les certificats et genere la commande de join

### Phase 2 — Joindre worker1 comme control-plane (sur .158)
```bash
# D'abord depuis le master : copier la commande de join
scp root@192.168.148.153:/tmp/ha-join-commands.sh /tmp/

# Parametrer et executer
export JOIN_TOKEN="<token>"
export CERT_KEY="<cert_key>"
export DISCOVERY_HASH="<hash>"
sudo ./02-join-worker-as-cp.sh
```
- Drain le noeud (evacue les pods)
- Reset kubeadm
- Re-join comme control-plane (etcd 2/3)
- **Attendre que le noeud soit Ready avant de continuer**

### Phase 2bis — Joindre worker2 comme control-plane (sur .155)
Meme procedure que Phase 2.
- Apres : etcd 3/3, cluster HA complet

### Phase 3 — Finaliser (sur .153)
```bash
sudo ./03-finalize-ha.sh
```
- Retire le taint control-plane de tous les noeuds
- Labellise tous les noeuds comme worker
- Verifie etcd 3/3
- Verifie les leader elections
- Deploie l'audit policy partout
- Redistribue les pods

### Phase 4 — MetalLB + Ingress LoadBalancer (sur le master, K8s-natif)
```bash
sudo ./08-metallb-ingress-pro.sh
```
Remplace HAProxy+Keepalived VIP par MetalLB (LoadBalancer K8s-natif).
ingress-nginx recoit une IP MetalLB (pool .200-.210).
Chaine : Client -> MetalLB IP -> ingress-nginx -> Service -> Pods.

> **Note** : Keepalived/VIP supprime. HAProxy reste uniquement pour le
> control-plane kubeadm (:6443). Voir `haproxy-controlplane-only.cfg`.

### Phase 5 — Fix VM Monitoring (sur .160)
```bash
sudo ./09-fix-monitoring-vm.sh
```
Installe Alertmanager + Registry Docker v2 + supprime Keepalived.

### Phase 6 — Promtail (sur le cluster)
```bash
kubectl apply -f promtail-daemonset.yaml
```
DaemonSet qui collecte les logs de TOUS les noeuds vers Loki.

## Phase 7 — Verification complete (sur un control-plane)
```bash
sudo ./06-verify-all.sh
```
Rapport automatise : 9 categories, score OK/WARN/FAIL.

### Test HA manuel (optionnel)
```bash
# Arreter un noeud pour verifier la resilience
vmrun stop "k8s-worker2"
# Attendre 60s, puis :
kubectl get nodes          # 2/3 Ready
kubectl -n pfe get pods    # pods replanifies
# etcdctl member list      # 2/3 started (quorum OK)
# Redemarrer apres le test
vmrun start "k8s-worker2"
```

## Fichiers livres

| Fichier | Quoi |
|---|---|
| `kubeadm-ha-config.yaml` | Config kubeadm HA reference |
| `audit-policy.yaml` | Policy d'audit Kubernetes |
| `00-resize-vms.sh` | Redimensionnement RAM VMware |
| `01-prepare-master.sh` | Preparation du master |
| `02-join-worker-as-cp.sh` | Join worker comme control-plane |
| `03-finalize-ha.sh` | Finalisation HA + labels |
| `04-install-keepalived.sh` | OBSOLETE — remplace par MetalLB |
| `05-install-alertmanager.sh` | Installation Alertmanager |
| `alertmanager.yml` | Config Alertmanager |
| `promtail-daemonset.yaml` | DaemonSet Promtail |
| `promtail-config.yml` | Config Promtail reference |
| `06-verify-all.sh` | Verification complete post-migration |
| `07-setup-registry.sh` | Registry prive local sur .160 |
| `../08-metallb-ingress-pro.sh` | MetalLB + ingress LoadBalancer (K8s-natif) |
| `../09-fix-monitoring-vm.sh` | Alertmanager + Registry + nettoyage .160 |
| `../haproxy-controlplane-only.cfg` | HAProxy control-plane uniquement |

## Rollback

Si quelque chose casse :

1. **etcd corrumpu** : restaurer le snapshot pre-migration
   ```bash
   ETCDCTL_API=3 etcdctl snapshot restore /tmp/etcd-pre-ha-*.db \
       --data-dir=/var/lib/etcd-restore
   systemctl stop kubelet
   mv /var/lib/etcd /var/lib/etcd-broken
   mv /var/lib/etcd-restore /var/lib/etcd
   systemctl start kubelet
   ```

2. **Worker ne rejoint pas** : reset et re-tenter
   ```bash
   kubeadm reset -f
   # Regenerer token sur le master si expire
   kubeadm token create --print-join-command
   kubeadm init phase upload-certs --upload-certs
   ```

3. **Tout casse** : restaurer les snapshots VMware
