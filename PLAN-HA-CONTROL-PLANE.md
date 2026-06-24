# Haute disponibilité du control-plane (master) — analyse & plan

## 1. Le constat : le master est un SPOF

Le cluster a aujourd'hui **1 master + 2 workers**. Si la VM master tombe :
- l'API server tombe → plus de scheduling, plus de `kubectl`
- HELIOS ne peut plus agir sur le cluster (détection/remédiation K8s)
- (la **base de données survit** : CNPG est répliqué sur les 2 workers)

Objectif demandé : *« si master tombe, un worker prend le relais ; priorité worker1, puis worker2 »*.

## 2. La réalité Kubernetes (important pour la soutenance)

1. **Un worker ne devient PAS master "à la demande".** Un nœud est *soit* control-plane *soit* worker. La seule façon de survivre à la perte du master est un **control-plane HA configuré à l'avance** (multi-master).
2. **etcd impose un quorum majoritaire et un nombre IMPAIR de membres :**

   | Masters | Pannes tolérées |
   |--------:|:----------------|
   | 1 (actuel) | 0 |
   | **2** | **0 (pire que 1 !)** |
   | **3** | **1** |
   | 5 | 2 |

   → **2 masters est néfaste.** Il faut **3 nœuds control-plane** pour survivre à 1 panne.

## 3. Diagnostic de faisabilité (mesuré le 24/06/2026)

| Nœud | RAM totale | RAM dispo | Alerte |
|------|-----------:|----------:|--------|
| master | 1,6 Go | **347 Mo** | apiserver redémarré **121×**, controller **345×**, scheduler **342×** (OOM-thrashing) |
| worker1 | 1,9 Go | 452 Mo | — |
| worker2 | 2,4 Go | 1,0 Go | — |

- Pas de `controlPlaneEndpoint`, etcd à **1 membre**, pas de keepalived/haproxy/kube-vip.
- **Verdict : le vrai HA 3-masters est infaisable sur ce sizing.** etcd+apiserver demandent ~1–1,5 Go/nœud ; les workers ne les ont pas, et le master OOM-thrash déjà. Forcer = perte de quorum etcd = reconstruction.

## 4. Solution CIBLE (production) — Option A

**3 nœuds control-plane (stacked etcd) + IP virtuelle (VIP) keepalived en priorités.**

```
keepalived VRRP :  master=priorité 100  >  worker1=90  >  worker2=80
VIP = ex. 192.168.148.150  (= controlPlaneEndpoint, dans les certSANs de l'apiserver)
```

- La **VIP** (endpoint de l'API) flotte vers le nœud vivant de plus haute priorité : master → worker1 → worker2. **C'est exactement le modèle de priorité demandé.**
- etcd à 3 membres tolère **la perte d'1 nœud**.
- On retire le taint `node-role.kubernetes.io/control-plane:NoSchedule` → HELIOS tourne sur n'importe quel nœud.

**Prérequis matériel (bloquant aujourd'hui) :** chaque VM ≥ **4 Go** (idéalement 6). L'hôte étant sur-engagé (~24 Go pour 5 VMs, dont la VM IA ~12 Go), il faut **libérer de la RAM** (réduire/éteindre la VM IA) avant.

**Procédure (résumé, à exécuter avec snapshots) :**
1. Snapshots VMware des 3 VMs + `etcdctl snapshot save`.
2. Poser la VIP : keepalived (priorités) + haproxy (ou kube-vip) sur les 3 nœuds.
3. Régénérer les certs apiserver avec la VIP dans les `certSANs` ; définir `controlPlaneEndpoint = VIP:6443`.
4. `kubeadm join --control-plane` sur worker1 puis worker2 (ajout des membres etcd).
5. Re-pointer kubelets/kube-proxy/kubeconfig sur la VIP.
6. Retirer le taint control-plane ; vérifier `etcdctl member list` = 3 *started*, `kubectl get nodes` = 3 control-plane.
7. Test : couper le master → la VIP bascule sur worker1, le cluster reste up.

**Limite assumée :** avec 3 nœuds, etcd ne tolère qu'**1 panne à la fois**. « master + worker1 down simultanément » casse le quorum (1/3) → il faudrait **5 nœuds**.

## 5. Solution ACTUELLE (réalisable, sûre) — Option B ✅ implémentée

Faute de RAM pour le vrai HA, le **watchdog hôte** redémarre la VM master quand elle tombe (comme il le fait déjà pour les workers) — `backend/backend/infra/scripts/vm_watchdog.py`.

**Ce qui a été ajouté (master-aware) :**
- Détection du master par **sonde TCP directe** (API 6443 / SSH 22), **indépendante de kubectl** — car si le master est down, l'API est down et kubectl ne peut pas répondre.
- **Aucun faux redémarrage des workers** quand l'API est injoignable (on ne juge les workers via kubectl que si le master est vivant).
- `vmrun start k8s-master` après `grace_period` → récupération **autonome ~1–2 min**.
- Incident **VM_DOWN: k8s-master** remonté au backend via **n'importe quel NodePort de worker** (donc visible dans HELIOS même master down) → RESOLVED au retour.

**Lancer / recharger sur l'hôte :**
```
python backend/backend/infra/scripts/vm_watchdog.py
```
(le `vm_watchdog_config.json` auto-découvre les `.vmx` ; vérifier les chemins.)

**Test de démo :**
1. Couper la VM master (VMware power-off).
2. Le watchdog la détecte injoignable > grace → `vmrun start k8s-master`.
3. ~1–2 min plus tard : master Ready, API revient, incident `VM_DOWN: k8s-master` → RESOLVED.
4. Les workers ne sont PAS touchés pendant l'indisponibilité de l'API.

## 6. Quoi dire au jury

> « Le control-plane est un SPOF. La solution correcte est un HA 3-masters (etcd quorum impair) + VIP keepalived à priorités, qui réalise exactement le basculement master→worker1→worker2. Mais le sizing actuel des VMs (1,6–2,4 Go, master déjà en OOM-thrashing) ne le supporte pas : ce serait une perte de quorum etcd. J'ai donc (a) documenté l'archi cible + prérequis matériel, et (b) implémenté une mitigation réaliste : le watchdog hôte redémarre la VM master de façon autonome (~1–2 min), avec détection directe hors-cluster et sans impacter les workers. C'est un choix d'ingénierie assumé : ne pas casser un système qui marche pour une archi que le matériel ne peut pas soutenir. »
