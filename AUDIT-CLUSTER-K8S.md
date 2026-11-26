# RAPPORT D'AUDIT — Cluster Kubernetes PFE HELIOS

**Date :** 4 août 2026
**Auteur :** Audit automatisé
**Projet :** Infrastructure Auto-Réparatrice Kubernetes — Orange Tunisie

---

## RÉSUMÉ EXÉCUTIF

Le projet HELIOS est **remarquablement avancé** pour un PFE. L'architecture couvre un spectre très large : Kubernetes multi-nœuds, monitoring complet (Prometheus + Grafana + Loki), orchestration BPMN (Camunda/Zeebe), IA (ML XGBoost + LLM fine-tuné + RAG ChromaDB), sécurité (Falco, etcd encryption, RBAC, Pod Security Standards, TLS), et load balancing (HAProxy + Ingress). Cependant, **plusieurs écarts existent entre le PDF de spécification et l'implémentation réelle**, et certains concepts des cours TechWorld with Nana ne sont pas pleinement implémentés.

**Score global : 78/100** — Projet professionnel avec des manques ciblés.

---

## 1. INVENTAIRE DES 6 VMs

| VM | Nom | IP | vCPU | RAM | Rôle |
|---|---|---|---|---|---|
| VM1 | k8s-master | 192.168.148.153 | 2 | 2,5 Go | Control Plane (unique) |
| VM2 | k8s-worker1 | 192.168.148.158 | 2 | 3 Go | Worker |
| VM3 | k8s-worker2 | 192.168.148.155 | 2 | 2,5 Go | Worker |
| VM4 | k8s-monitoring | 192.168.148.160 | 1 | 2 Go | Prometheus, Grafana, Loki, HAProxy |
| VM5 | k8s-aiCamunda | 192.168.148.161 | 3 | 6 Go | Camunda/Zeebe, AI Service, Ollama |
| VM6 | k8s-lb2 | 192.168.148.162 | 1 | 768 Mo | HAProxy backup |

**Total RAM allouée : ~17 Go** sur un hôte de 23,8 Go.

---

## 2. COMPARAISON VIDÉOS NANA vs PROJET

### 2.1 Concepts couverts (✅ Présent dans le projet)

| # | Vidéo / Concept Nana | Implémentation HELIOS | Qualité |
|---|---|---|---|
| 1 | What is Kubernetes | Cluster kubeadm v1.29.15 opérationnel | ✅ Excellent |
| 2 | Components (Pods, Services, Secrets, ConfigMap) | Deployment backend/frontend, backend-config ConfigMap, backend-secret Secret, services ClusterIP/NodePort | ✅ Excellent |
| 3 | Architecture (API Server, etcd, kubelet, scheduler) | Stacked etcd, API Server, 3 nœuds avec kubelets | ✅ Bon |
| 4 | HA / Scalability / Disaster Recovery | HPA, PDB, pod anti-affinity, topology spread, tolerations 60s, CNPG failover | ✅ Très bon |
| 5 | kubectl commands | Usage extensif dans 100+ scripts Python/Shell | ✅ Excellent |
| 6 | YAML (Deployment + Service) | Manifestes complets avec resources, probes, security context | ✅ Excellent — niveau production |
| 7 | Complete App Deployment | Backend (Spring Boot) + Frontend (Angular/nginx) + DB (CNPG PostgreSQL) | ✅ Excellent |
| 8 | Namespaces | pfe, monitoring, kube-system, cnpg-system, falco, ingress-nginx, metallb-system | ✅ Excellent |
| 9 | Ingress | ingress-nginx avec helios-ingress, annotations SSE, TLS termination | ✅ Excellent |
| 10 | Helm | Chart Helm complet dans infra/helm/helios/ (values.yaml, templates/) | ✅ Bon |
| 11 | Volumes (PV, PVC, StorageClass) | local-path-storage, CNPG PVCs 5Gi, emptyDir pour nginx | ✅ Bon |
| 12 | ConfigMap/Secret as Volumes | frontend-nginx-conf monté comme volume, helios-tls monté pour TLS | ✅ Bon |
| 13 | Networking (Pods & Containers) | Calico CNI (pod CIDR 192.168.0.0/16), service CIDR 10.96.0.0/12 | ✅ Bon |
| 14 | Services (ClusterIP, NodePort, LoadBalancer, Headless) | ClusterIP (backend-service), NodePort (frontend 30880, backend 30808), MetalLB pool configuré | ✅ Bon |
| 15 | StatefulSet vs Deployment | CNPG PostgreSQL utilise un StatefulSet (2 instances, primary + standby) | ✅ Bon |
| 16 | Private Registry / Pull Image | Images chargées manuellement comme tar sur les nœuds (air-gap) | ⚠️ Fonctionnel mais pas pro |
| 17 | Prometheus Monitoring (Part 1 & 2) | Prometheus sur VM .160, ServiceMonitor, PrometheusRules, kube-state-metrics, node-exporter | ✅ Excellent |
| 18 | Kubernetes Operator | CNPG Operator (postgresql), Falco DaemonSet | ✅ Bon |
| 19 | Security Best Practices | etcd encryption at rest, Pod Security Standards, RBAC least-privilege, TLS, Falco eBPF, secret rotation | ✅ Excellent |
| 20 | Managed K8s / Cloud deployment | N/A (bare-metal VMware) — pas applicable | ➖ Non applicable |

### 2.2 Concepts avec des lacunes (⚠️)

| Concept Nana | Ce qui manque | Impact |
|---|---|---|
| **LoadBalancer Service** | MetalLB configuré mais non fonctionnel depuis l'extérieur (NAT VMware bloque L2 ARP) | Moyen — l'accès passe par HAProxy+NodePort à la place |
| **Private Docker Registry** | Pas de registry (Harbor, Docker Registry). Images copiées comme .tar via scp + ctr import | Important — non professionnel pour un cluster de prod |
| **Helm avancé** | Chart Helm défini mais les déploiements réels utilisent des scripts Python + kubectl apply | Moyen — Helm pas utilisé comme workflow principal |

---

## 3. AUDIT DÉTAILLÉ PAR DOMAINE

### 3.1 HAUTE DISPONIBILITÉ (HA)

#### 3.1.1 HA du Control Plane — ❌ CRITIQUE

**PDF dit :** « 3 nœuds, chaque VM joue le rôle de Control Plane + Worker »
**Réalité :** 1 seul master (.153). Les workers (.155, .158) sont UNIQUEMENT workers.

**Preuve :** `kubeadm-min.yaml` montre `controlPlaneEndpoint: 192.168.148.153:6443` — un seul nœud. Le fichier `PLAN-HA-CONTROL-PLANE.md` confirme explicitement : « le master est un SPOF » et « le vrai HA est IMPOSSIBLE sur 23,8 Go sans casser HELIOS ».

**Conséquence :** Si le master tombe, le cluster entier est inaccessible (plus d'API Server, plus de scheduling, plus de kubectl). Le watchdog hôte (`vm_watchdog.py`) redémarre la VM, mais ce n'est PAS du HA — c'est de la récupération automatique (temps de coupure : 2-5 minutes).

**Pour un cluster professionnel, il faut :**
- 3 nœuds control plane avec stacked etcd (quorum 2/3)
- Virtual IP (Keepalived/VRRP ou kube-vip) comme controlPlaneEndpoint
- Minimum 4 Go RAM/nœud pour etcd + API Server
- **Bloqué par le sizing RAM actuel**

#### 3.1.2 HA du HAProxy — ⚠️ DIVERGENCE AVEC LE PDF

**PDF dit :** HAProxy Active/Passive avec Keepalived + Virtual IP (VRRP). « VM Monitoring = Active, LB2 = Passive, bascule quand le HAProxy principal n'est plus disponible ».

**Réalité :** Actif/Actif avec round-robin DNS (résolution helios.orange.tn vers .160 ET .162). **PAS de Keepalived, PAS de Virtual IP.** Le commentaire dans `haproxy-apps-lb.cfg` est explicite : « keepalived a été volontairement écarté (choix : pas de VIP) ».

**Analyse :** Le choix actif/actif DNS fonctionne (le navigateur bascule si un nœud ne répond pas), mais ce n'est PAS ce que le PDF décrit. Pour la soutenance, il faut soit :
- Implémenter Keepalived + VIP (comme décrit dans le PDF)
- OU modifier le PDF pour refléter le choix actif/actif DNS (et justifier)

#### 3.1.3 HA des Workers — ✅ EXCELLENT

L'implémentation est très professionnelle :
- **Pod Anti-Affinity** : backend et frontend répartis sur les 2 workers
- **Topology Spread Constraints** : spread par zone (zone-a / zone-b)
- **Tolerations** : éviction en 60s (vs 5 min par défaut) depuis un nœud NotReady
- **PDB** : minAvailable=1 pour backend, frontend, postgres
- **CNPG** : PostgreSQL 2 instances avec failover automatique (~57s mesuré)
- **HPA** : autoscaling CPU 70% / mémoire 80%, 2-4 replicas
- **Runbook node-failure** : documenté et validé par tests réels

#### 3.1.4 HA de la Base de Données — ✅ EXCELLENT

CNPG PostgreSQL avec :
- 2 instances (primary + standby) sur des nœuds différents (`podAntiAffinityType: required`)
- Failover automatique en ~57s
- Replication slots HA
- Synchronous replication (`maxSyncReplicas: 1`)
- Backups S3 vers MinIO (WAL + data, compression gzip, rétention 7j)
- PDB enabled
- TLS 1.3 min/max

---

### 3.2 LOAD BALANCING

#### 3.2.1 HAProxy Control Plane — ✅ BON

HAProxy sur .160 distribue le trafic API (port 6443) vers les 3 nœuds en round-robin avec health checks (`/healthz`, check-ssl). C'est correct.

**Mais :** Le `kubeadm-min.yaml` montre `controlPlaneEndpoint: 192.168.148.153:6443` (le master direct), pas `192.168.148.160:6443` (HAProxy). La section sécurité mentionne que `~/.kube/config` a été basculé vers .160:6443, mais le kubeadm init originel pointe sur .153. Vérifier que les kubelets pointent bien vers .160.

#### 3.2.2 HAProxy Apps — ✅ BON

Chaîne complète et correcte :
```
Client → HAProxy (.160/.162) :80/:443
  → NodePort ingress-nginx (31756/31411) sur les 3 nœuds
    → Ingress Controller
      → Service frontend (ClusterIP)
        → Pods frontend
```

Health checks HTTP (GET / avec Host: helios.orange.tn), failover automatique si un nœud est down.

#### 3.2.3 Ingress — ✅ BON

Ingress-nginx correctement configuré avec :
- IngressClass `nginx`
- Host `helios.orange.tn`
- TLS avec Secret `helios-tls`
- Annotations SSE (proxy-buffering off, timeout 3600s)
- Routage unique vers le frontend (le frontend proxifie /api/ vers le backend)

#### 3.2.4 MetalLB — ⚠️ PARTIEL

Configuré (pool 192.168.148.200-210, L2Advertisement) mais **non fonctionnel de l'extérieur** à cause du NAT VMware. L'accès réel passe par HAProxy + NodePort.

---

### 3.3 SÉCURITÉ

#### 3.3.1 Ce qui est implémenté — ✅ EXCELLENT (pour un PFE)

| Mesure | Détail | Statut |
|---|---|---|
| Chiffrement etcd au repos | aescbc avec rotation de clé (key1 → key2) | ✅ Appliqué |
| Pod Security Standards | enforce=baseline, warn/audit=restricted sur ns pfe | ✅ Appliqué |
| RBAC least-privilege | ServiceAccount backend-remediator avec Role/ClusterRole ciblés | ✅ Appliqué |
| TLS/HTTPS | Certificat auto-signé, terminaison TLS par ingress-nginx | ✅ Appliqué |
| Falco | DaemonSet eBPF, détection crypto-mining/shell/escalade | ✅ Appliqué |
| Security Strict Mode | Backend refuse de démarrer avec des secrets par défaut | ✅ Appliqué |
| Secrets non-default | JWT_SECRET, SUPER_ADMIN_PASSWORD régénérés | ✅ Appliqué |
| runAsNonRoot | Backend (uid 1000), frontend (uid 101) | ✅ Appliqué |
| drop ALL capabilities | Backend et frontend | ✅ Appliqué |
| allowPrivilegeEscalation: false | Backend et frontend | ✅ Appliqué |

#### 3.3.2 Ce qui manque

| Mesure | Importance | Détail |
|---|---|---|
| **NetworkPolicies actives** | Haute | Définies dans `network-policies.yaml` mais NON appliquées dans le namespace `pfe` (choix assumé pour le chaos engineering) |
| **Certificat Let's Encrypt / CA** | Moyenne | Auto-signé = avertissement navigateur |
| **anonymous-auth=false** | Basse | Non fait (risque de casser les probes) |
| **Audit logging API Server** | Moyenne | Pas de policy d'audit Kubernetes |
| **Image scanning** | Basse | Pas de Trivy/Grype pour scanner les images |
| **Pod Security Admission enforce=restricted** | Basse | Seulement warn/audit (les apps chaos tournent en root) |

---

### 3.4 MONITORING & OBSERVABILITÉ

#### 3.4.1 Stack Monitoring — ✅ EXCELLENT

- **Prometheus** sur VM .160 : collecte des métriques cluster via federation (Prometheus in-cluster → Prometheus externe)
- **Grafana** : dashboards personnalisés (grafana-incidents-host.json)
- **Loki** : centralisation des logs
- **kube-state-metrics** : métriques d'état Kubernetes
- **node-exporter** : métriques hôte (CPU, RAM, disk, réseau)
- **metrics-server** : métriques temps réel (kubectl top, HPA)
- **ServiceMonitor** : scraping automatique du backend via Prometheus Operator
- **PrometheusRules** : alertes critiques (AI Service down, backend down, stockage Prometheus)

#### 3.4.2 Ce qui manque

| Élément | Importance | Détail |
|---|---|---|
| **Alertmanager** | Haute | Pas de configuration Alertmanager trouvée — les PrometheusRules définissent des alertes mais pas de routing (email, Slack, PagerDuty) |
| **Promtail/Agent** | Moyenne | Pas de configuration Promtail visible dans le projet (comment Loki collecte les logs ?) |
| **Rétention Prometheus** | Moyenne | Script `configure-loki-retention.sh` existe mais pas de config de rétention Prometheus explicite |
| **Monitoring de la VM monitoring** | Moyenne | Qui surveille le surveillant ? Si .160 tombe, plus de monitoring |

---

### 3.5 APPLICATION & DÉPLOIEMENT

#### 3.5.1 Qualité des manifestes Kubernetes — ✅ EXCELLENT

Les manifestes sont de qualité production :
- **Resources requests/limits** sur tous les containers
- **Probes** : readiness, liveness, ET startup probe (backend)
- **Rolling update** avec maxSurge/maxUnavailable adaptés
- **RevisionHistoryLimit: 2** (nettoyage des anciens ReplicaSets)
- **Resource Quota** et **LimitRange** sur le namespace
- **Kustomize** pour l'organisation des manifestes
- **Labels** cohérents (app, aiops.io/monitor, helios.io/monitor)
- **Annotations Prometheus** pour le scraping

#### 3.5.2 CI/CD — ⚠️ PARTIEL

- **GitHub Actions** : 4 workflows définis (backend-ci, frontend-ci, ai-ci, model-train)
- **Mais** : les déploiements réels sont faits via ~50 scripts Python manuels (`deploy_backend_XXX.py`)
- **Pas de GitOps** : pas d'ArgoCD ni Flux
- **Pas de container registry** : images .tar copiées manuellement

---

### 3.6 ORCHESTRATION CAMUNDA / IA

#### 3.6.1 Workflow BPMN — ✅ TRÈS BON

Le workflow `camunda-full-cycle.bpmn` est complet et professionnel :
1. Timer 60s → Détection SSH sur .160
2. Décision (anomalie + monitoring fiable ?)
3. IA (.161:8000/predict) → classification + action recommandée
4. Routage avec garde-fous :
   - Namespaces système protégés (kube-system, cnpg-system, etc.)
   - Incohérence détection vs IA → escalade
   - NO_INCIDENT → fin saine
5. Exécution via SSH bridge

#### 3.6.2 Détecteur — ✅ EXCELLENT

16 types d'incidents détectés, avec priorité ordonnée :
- Control plane : ETCD_NO_LEADER, ETCD_SLOW_FSYNC, ETCD_LEADER_FLAPPING
- Nœuds : NODE_NOT_READY, NODE_DISK_PRESSURE, NODE_PID_PRESSURE, NODE_NETWORK_UNAVAILABLE, NODE_CORDONED
- Hôte : CPU, RAM, disque (via node-exporter)
- Pods : CrashLoopBackOff, Pending, OOMKilled, etc.

#### 3.6.3 AI Service — ✅ BON

- FastAPI avec ML (XGBoost) + LLM (Ollama) + RAG (ChromaDB)
- Middleware sécurité (API key, trace ID)
- AutonomousMonitor intégré
- Tests unitaires présents

---

## 4. CHECKLIST DES CONCEPTS NANA — DÉTAIL

### 4.1 Concepts couverts et leur implémentation

| # | Concept | Fichier(s) preuve | Score |
|---|---|---|---|
| 1 | Pods | backend-deployment.yaml, frontend-deployment.yaml | 10/10 |
| 2 | Deployments | backend-deployment.yaml (rolling update, revisionHistoryLimit) | 10/10 |
| 3 | Services (ClusterIP) | backend-service.yaml | 10/10 |
| 4 | Services (NodePort) | frontend-service.yaml (30880), backend-nodeport.yaml (30808) | 10/10 |
| 5 | Services (LoadBalancer) | metallb-pool.yaml (configuré, non fonctionnel externe) | 6/10 |
| 6 | ConfigMap | backend-configmap.yaml, frontend-nginx-configmap.yaml | 10/10 |
| 7 | Secret | backend-secret.yaml, helios-tls, postgres-app | 10/10 |
| 8 | Namespaces | pfe, monitoring, kube-system, cnpg-system, falco, ingress-nginx | 10/10 |
| 9 | Ingress | ingress-helios.yaml, Helm template ingress.yaml | 10/10 |
| 10 | Volumes (PV/PVC) | postgres-cluster.yaml (5Gi), local-path-storage.yaml | 8/10 |
| 11 | ConfigMap as Volume | frontend-nginx-conf monté dans frontend | 10/10 |
| 12 | Secret as Volume | helios-tls monté comme /etc/nginx/tls | 10/10 |
| 13 | Helm | infra/helm/helios/ (Chart.yaml, values.yaml, templates/) | 8/10 |
| 14 | StatefulSet | CNPG PostgreSQL (2 instances, HA) | 9/10 |
| 15 | Prometheus | VM .160 + in-cluster, ServiceMonitor, PrometheusRules | 10/10 |
| 16 | Kubernetes Operator | CNPG Operator (postgresql), Falco | 9/10 |
| 17 | Security | etcd encryption, PSS, RBAC, TLS, Falco, runAsNonRoot | 10/10 |
| 18 | Networking | Calico CNI, network-policies.yaml (non appliqué) | 7/10 |
| 19 | HPA | backend-hpa.yaml (CPU 70%, memory 80%, 2-4 replicas) | 10/10 |
| 20 | PDB | backend, frontend, postgres, chromadb, loki PDBs | 10/10 |

### 4.2 Concepts PAS dans les vidéos Nana mais PRÉSENTS (bonus)

| Concept avancé | Fichier preuve |
|---|---|
| Topology Spread Constraints | backend-deployment.yaml |
| Startup Probe | backend-deployment.yaml |
| Resource Quota + LimitRange | resource-quota.yaml |
| Kustomize | kustomization.yaml |
| CNPG (CloudNativePG) | postgres-cluster.yaml |
| Falco (sécurité runtime eBPF) | falco-values.yaml |
| Chaos Engineering | scripts/helios-tests/chaos/*.yaml (10 scénarios) |
| etcd encryption + rotation | infra/security.md |
| BPMN orchestration | camunda-full-cycle.bpmn |
| ML/LLM pipeline | ai-service/, experiments/, datasets/ |

---

## 5. PROBLÈMES À CORRIGER (par priorité)

### 🔴 PRIORITÉ HAUTE (pour la soutenance)

1. **Cohérence PDF ↔ Réalité pour le Control Plane**
   - Le PDF dit « 3 masters ». La réalité est 1 master.
   - **Action :** Modifier le PDF pour refléter la réalité (1 master + 2 workers) et expliquer POURQUOI le HA 3-masters n'est pas faisable (contrainte RAM). Le PLAN-HA-CONTROL-PLANE.md contient déjà cette analyse — l'intégrer au rapport.

2. **Cohérence PDF ↔ Réalité pour HAProxy HA**
   - Le PDF dit Active/Passive avec Keepalived + VIP.
   - La réalité est Active/Active DNS sans Keepalived.
   - **Action :** Soit implémenter Keepalived (simple sur 2 VMs), soit modifier le PDF.
   - **Si implémentation :** installer keepalived sur .160 et .162, configurer VRRP avec VIP .150, priority 100 sur .160 / 90 sur .162. C'est faisable en ~30 minutes.

3. **Alertmanager manquant**
   - Des PrometheusRules existent mais pas de routing d'alertes.
   - **Action :** Configurer Alertmanager sur .160 avec au minimum un receiver email (le relais SMTP est déjà en place).

### 🟡 PRIORITÉ MOYENNE (amélioration professionnelle)

4. **Container Registry**
   - Pas de registry. Images en .tar copiées manuellement.
   - **Action :** Installer un Docker Registry ou Harbor sur .160 ou .161. Les nœuds tirent depuis le registry au lieu de charger des tars.

5. **Promtail / Log Agent**
   - Pas de configuration visible pour la collecte de logs vers Loki.
   - **Action :** Vérifier que Promtail ou un agent log est bien déployé. Si non, installer Promtail comme DaemonSet.

6. **Audit Logging Kubernetes**
   - Pas de policy d'audit pour l'API Server.
   - **Action :** Créer un fichier `audit-policy.yaml` et l'activer sur l'API Server pour tracer les actions sensibles.

7. **Backup disaster recovery test**
   - Les backups existent (etcd + CNPG) mais pas de test de restauration documenté.
   - **Action :** Documenter et tester un scénario de restore complet.

### 🟢 PRIORITÉ BASSE (nice to have)

8. **GitOps (ArgoCD/Flux)** — Optionnel pour un PFE.
9. **Image scanning (Trivy)** — Bonus sécurité.
10. **Cert-manager + Let's Encrypt** — Remplacer l'auto-signé (compliqué dans un lab).
11. **DaemonSet explicite** — Documenter les DaemonSets présents (node-exporter, Calico, Falco).

---

## 6. ARCHITECTURE RÉSEAU — SYNTHÈSE

```
                        ┌─────────────────────────────────────┐
                        │           CLIENT (navigateur)        │
                        └──────────┬──────────┬───────────────┘
                                   │          │
                          DNS round-robin : helios.orange.tn
                                   │          │
                    ┌──────────────▼──┐   ┌──▼──────────────┐
                    │ VM4 .160        │   │ VM6 .162        │
                    │ HAProxy (actif) │   │ HAProxy (actif) │
                    │ Prometheus      │   │                 │
                    │ Grafana         │   │                 │
                    │ Loki            │   │                 │
                    │ MinIO           │   │                 │
                    └──────┬──────────┘   └──────┬──────────┘
                           │                     │
                    ┌──────▼─────────────────────▼──────┐
                    │        NodePort 31756/31411        │
                    │                                    │
              ┌─────▼─────┐  ┌──────────┐  ┌──────────▼┐
              │ VM1 .153  │  │ VM2 .158 │  │ VM3 .155  │
              │ Master    │  │ Worker1  │  │ Worker2   │
              │ etcd      │  │ backend  │  │ backend   │
              │ API Server│  │ frontend │  │ frontend  │
              │           │  │ postgres │  │ postgres  │
              └───────────┘  └──────────┘  └───────────┘
                    │                            │
                    └────────────────────────────┘
                         Cluster Kubernetes
                                ▲
                                │ SSH (remédiation)
                    ┌───────────┴───────────┐
                    │ VM5 .161              │
                    │ Camunda / Zeebe       │
                    │ AI Service (FastAPI)  │
                    │ Ollama (LLM)          │
                    │ ChromaDB (RAG)        │
                    └───────────────────────┘
```

### Flux réseau détaillé

| Source | Destination | Port | Protocole | Données |
|---|---|---|---|---|
| Client | HAProxy .160/.162 | 80, 443 | HTTP/TCP | Trafic web applicatif |
| HAProxy | Nœuds K8s .153/.155/.158 | 31756, 31411 | HTTP/TCP | NodePort ingress-nginx |
| HAProxy .160 | Nœuds K8s | 6443 | TCP (TLS) | API Kubernetes (control plane LB) |
| Prometheus .160 | Nœuds K8s | 9100, 10250 | HTTP | Métriques node-exporter, kubelet |
| Prometheus .160 | Prometheus in-cluster | (via NodePort) | HTTP | Federation métriques |
| Loki .160 | Nœuds K8s | (Promtail) | HTTP | Logs containers/pods |
| Camunda .161 | Bridge .161 (localhost) | 8099 | HTTP | Déclenchement détection/action |
| Bridge .161 | VM monitoring .160 | 22 | SSH | Exécution détecteur |
| Bridge .161 | Nœuds K8s .153/.155/.158 | 22 | SSH | Exécution kubectl (remédiation) |
| AI Service .161 | Prometheus .160 | 9090 | HTTP | Collecte métriques pour analyse |
| AI Service .161 | Loki .160 | 3100 | HTTP | Collecte logs pour analyse |
| CNPG | MinIO .160 | 9000 | HTTP | Backups PostgreSQL |

---

## 7. CE QUI REND LE PROJET PROFESSIONNEL (POINTS FORTS)

1. **Détection multi-niveaux** : 16+ types d'incidents couvrant control plane, nœuds, pods, hôte
2. **Remédiation orchestrée** : workflow BPMN complet avec garde-fous (namespaces protégés, validation IA)
3. **HA workers** : pod anti-affinity, PDB, tolerations 60s, topology spread — configuration production
4. **Sécurité defense-in-depth** : etcd encryption + rotation, RBAC, PSS, Falco, TLS, runAsNonRoot
5. **CNPG PostgreSQL HA** : failover automatique testé et validé (~57s)
6. **Chaos engineering** : 10 scénarios de chaos définis et testés
7. **Documentation** : runbooks, guides de test, catalogues d'incidents
8. **Versioning** : 150+ versions du backend avec changelog détaillé dans les annotations
9. **Observabilité complète** : Prometheus + Grafana + Loki + ServiceMonitor + PrometheusRules + node-exporter + kube-state-metrics

---

## 8. DÉCISION REQUISE

Voici les actions que **toi seul** peux décider :

| # | Action | Effort | Impact soutenance |
|---|---|---|---|
| 1 | Implémenter Keepalived + VIP pour HAProxy HA | ~1h | Élevé — aligne le projet avec le PDF |
| 2 | Corriger le PDF (1 master ≠ 3 masters) | ~15 min | Critique — évite la contradiction |
| 3 | Installer Alertmanager | ~30 min | Moyen — complète le monitoring |
| 4 | Installer un Docker Registry | ~1h | Moyen — plus professionnel |
| 5 | Vérifier/installer Promtail | ~30 min | Moyen — complète la stack logs |
| 6 | Ajouter audit-policy.yaml | ~20 min | Bas — bonus sécurité |
| 7 | Tester un restore etcd + CNPG | ~1h | Moyen — prouve le disaster recovery |

**Recommandation :** Actions 1 et 2 sont les plus critiques pour la cohérence du projet lors de la soutenance.
