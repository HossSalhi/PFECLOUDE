# HELIOS — Guide de démo soutenance (tests naturels, sans assistance)

> Tous les `kubectl` / PowerShell se lancent sur l'**hôte Windows**.
> Règle d'or : **un seul worker à la fois, jamais les deux ensemble.**

---

## 0) Pré-check (À FAIRE 1 FOIS, avant l'encadrant)

```powershell
kubectl get nodes                 # -> 3 nœuds "Ready"
kubectl get pods -n cnpg-system   # -> opérateur "Running"  (CRITIQUE : sinon la DB tombe au test node)
kubectl get cluster -n pfe        # -> "Cluster in healthy state"
# Watchdog (filet de sécurité) actif ?
(Get-ScheduledTask -TaskName "HELIOS-NodeAutoheal").State          # -> Running
# Agent qui rallume les VM (appelé par Camunda) ?
(Invoke-WebRequest http://192.168.148.1:9999/ -UseBasicParsing).StatusCode   # -> 200
```
Liste d'incidents propre au départ : page **Incidents** → tout doit être `RESOLVED` (ou vide).

Variables utiles :
```powershell
$vmrun = "C:\Program Files (x86)\VMware\VMware Workstation\vmrun.exe"
$w1    = "C:\Users\houss\Documents\Virtual Machines\k8s-worker1\k8s-worker1.vmx"
$w2    = "C:\Users\houss\Documents\Virtual Machines\k8s-worker2\k8s-worker2.vmx"
```

---

## 🎬 TEST 1 — Panne d'un worker (LE clou du spectacle)

**Ce que ça démontre :** détection IA → décision → **Camunda exécute le power-on de la VM** → HA DB → auto-résolution. 100% automatique.

### L'action (devant l'encadrant)
Dans **VMware** : clic droit sur `k8s-worker1` → **Power → Power Off** *(arrêt brutal = vraie panne)*.
> Tu peux aussi : `& $vmrun stop $w1 hard`

### Ce que tu MONTRES à l'écran (dans cet ordre)
1. **VMware** : la VM `k8s-worker1` s'éteint, puis **se rallume toute seule** ~1-2 min après. 👈 « C'est HELIOS qui la rallume, je n'ai rien fait. »
2. **HELIOS → Incidents** (~1 min après) : un incident **`NODE_NOT_READY` / `Infrastructure DOWN: k8s-nodes/k8s-worker1`** apparaît, sévérité **CRITICAL**, source **AI**.
3. **Clique l'incident** → page détail, montre :
   - **Explication IA** : *« the node hosting the workload is unreachable… escalating triggers node failover… [ML + fine-tuned specialist consensus] »* + **Confiance IA** (barre %).
   - **Actions de Remédiation** : action **`RESTART_VM`**, **MODE = AUTO**, statut passe **`COMPLETED`** → *« VM restart initiated via watchdog ».*  👈 **« C'est Camunda qui a exécuté le rallumage. »**
   - **Historique & Timeline** : `OPEN` (détection) → `IN_PROGRESS` (RESTART_VM via Camunda) → `RESOLVED` (nœud revenu Ready).
4. **Email** : un mail HIGH arrive sur `houssamsalhi972@gmail.com`.

### Vérif technique (optionnel, en parallèle)
```powershell
kubectl get nodes              # k8s-worker1 repasse "Ready"
kubectl get cluster -n pfe     # la DB a basculé puis "Cluster in healthy state"
```

### Phrase clé
> « Je coupe physiquement le serveur. HELIOS le détecte avec son IA, **Camunda orchestre et exécute le redémarrage de la VM** via un agent hôte, la base bascule en haute dispo, et l'incident se résout tout seul. »

---

## 🎬 TEST 2 — Le 2e worker (montre la fiabilité)

**Seulement quand worker1 est revenu `Ready`** (jamais les deux à la fois).
VMware → clic droit `k8s-worker2` → **Power → Power Off**. Mêmes observations qu'au Test 1.
> Point fort : « Ça marche pour **n'importe quel** worker, même celui qui héberge le composant leader — HELIOS force une ré-élection pour ne jamais rater la détection. »

### Si une réplique DB reste bloquée (rare, >2 min en `0/1`)
```powershell
kubectl delete pod -n pfe postgres-2     # rejoin propre, redevient "healthy"
```

---

## 🎬 TEST 3 — Crash d'une application cliente (résilience + périmètre)

**Ce que ça démontre :** HELIOS surveille les **apps clientes** (podinfo), **pas sa propre stack**, et le cluster s'auto-répare.

```powershell
kubectl get pods -n pfe -l app=podinfo
kubectl delete pod -n pfe <un-pod-podinfo>     # = crash simulé
```
- **Montre** : Kubernetes recrée le pod en ~10 s, l'autre réplique sert **sans coupure**.
- **HELIOS → Incidents** : un incident sur **podinfo** peut apparaître (détection AI), puis se résout.
> Phrase clé : « HELIOS ne se surveille pas lui-même — il surveille l'infra et les **applications déployées**. »

---

## 🎯 Ce qu'il faut RÉPONDRE si l'encadrant demande « c'est vraiment de l'IA ? »
- **Oui** : modèle **ML entraîné** (1200 échantillons, **416 features** live : CPU, mémoire, latence p95, restarts, embeddings de logs) + **LLM fine-tuné** (LoRA sur Qwen2.5-0.5B, `aiops-sre-ft-v2`) + MLOps (drift, ré-entraînement).
- **Architecture hybride honnête** : *l'IA propose (ML 70% + LLM), une politique déterministe valide (allowlists, seuils de sécurité), Camunda exécute.* C'est exactement comme les AIOps en production — on ne laisse jamais un LLM exécuter librement une action destructrice.
- Preuve live : page **Décisions IA** + l'explication + la **confiance %** sur chaque incident.

---

## 🎬 TEST 4 — Incident applicatif avec VALIDATION HUMAINE (montre l'autre chemin Camunda)

**Ce que ça démontre :** détection d'une app cassée → **`DEPLOYMENT_UNAVAILABLE`** en **attente d'approbation** → tu valides → **Camunda exécute** (human-in-the-loop). Contraste avec le Test 1 (auto).

```powershell
# déploie une app volontairement cassée (image inexistante) dans le namespace surveillé
kubectl create deployment demo-ko --image=nginx:n-existe-pas-123 -n pfe
```
- **HELIOS → Incidents** (~1 min) : incident **`DEPLOYMENT_UNAVAILABLE`** sur `demo-ko`, **HIGH**, statut **`WAITING_FOR_APPROVAL`**.
- **Clique l'incident** → montre **Explication IA** + boutons **Démarrer / Résoudre** → *« ici l'IA détecte mais demande une validation humaine avant d'agir — c'est le mode gouverné. »*
- Clique **Démarrer** → Camunda lance le workflow (tâche dans **Tâches Humaines**).

**Nettoyage :** `kubectl delete deployment demo-ko -n pfe` (l'incident passe `RESOLVED`).

> Phrase clé : **« HELIOS a 2 modes : AUTO pour les actions sûres (rallumer un nœud), et VALIDATION HUMAINE pour les actions sensibles — c'est exactement la gouvernance d'un AIOps de production. »**

---

## 🎬 TEST 5 — Haute dispo de la BASE DE DONNÉES (très impressionnant)

**Ce que ça démontre :** quand le worker qui héberge le **primary PostgreSQL** tombe, **CloudNativePG promeut automatiquement la réplique** → la base ne tombe jamais.

```powershell
# 1) repère le worker qui héberge le primary
$primary = kubectl get cluster postgres -n pfe -o jsonpath='{.status.currentPrimary}'
kubectl get pod -n pfe $primary -o wide   # regarde la colonne NODE (k8s-worker1 ou 2)
```
- **Coupe CE worker-là** dans VMware (Power Off).
- **Montre** : `kubectl get cluster -n pfe` → le **PRIMARY bascule** sur l'autre pod, statut repasse **"Cluster in healthy state"**. La base reste disponible (l'app continue de répondre).
> Phrase clé : « Même si je coupe le serveur de base de données, **le failover est automatique** — zéro perte de service. »

---

## 🎬 TEST 6 — Notification email automatique

Pendant n'importe quel test **HIGH/CRITICAL**, montre la boîte **`houssamsalhi972@gmail.com`** : un email part automatiquement (titre, sévérité, lien dashboard).
> « Chaque incident critique alerte l'équipe par email, sans intervention. »

---

## 🎬 TEST 7 — Indisponibilité par mise à l'échelle (AVAILABILITY_DROP)

```powershell
kubectl scale deployment podinfo --replicas=0 -n pfe     # l'app n'a plus de réplique
# ... HELIOS détecte l'indisponibilité ...
kubectl scale deployment podinfo --replicas=2 -n pfe     # on rétablit
```
> Montre la détection de **disponibilité** + l'auto-résolution au retour des répliques.

---

## 🎬 TEST 8 — Gestion de plusieurs incidents en cascade

Une seule panne de worker génère **plusieurs incidents** (le nœud + les apps qu'il hébergeait). Montre que **HELIOS les détecte et les traite TOUS** (page Incidents qui se remplit puis se vide).
> « Un incident réel a des effets en cascade — HELIOS corrèle et résout l'ensemble. »

---

## 🎬 TEST 9 — Prouver que l'IA est RÉELLE (le test « anti-bluff »)

**Ce que ça démontre :** l'IA n'est pas un `if/else` — c'est un **modèle ML qui calcule sur 416 features live** + un LLM. À faire pendant qu'un incident est actif (ou sur une app saine).

### a) Dans l'UI (visuel, pour tout le monde)
- Page **Décisions IA** → montre, pour chaque incident : le **type prédit**, la **confiance %**, l'**action recommandée**, l'**explication en langage naturel** (LLM).

### b) Inférence brute en direct (pour le jury technique)
```powershell
$body = '{"namespace":"pfe","app":"podinfo","window_minutes":5}'
Invoke-RestMethod -Uri http://192.168.148.161:8000/predict -Method Post -Body $body -ContentType "application/json" | ConvertTo-Json -Depth 4
```
**Montre dans la sortie :**
- `evidence` → **`model_score`** (le ML), `rule_score`, `metric_score`, `log_score` → preuve de l'**ensemble** (ML 70% + règles 30%).
- `features` → **416 valeurs réelles** : `cpu_usage_cores`, `memory_working_set_bytes`, `http_latency_p95_seconds`, `pod_restarts_total`, `log_embedding_000…` → le modèle lit l'état réel du cluster.
- `confidence` + `execution_mode` (`RECOMMEND_ONLY` si < seuil 0.70) → la **décision gated**.

> Phrase clé : « Ce n'est pas une règle codée — c'est un modèle ML entraîné qui évalue **416 signaux temps réel**, et un LLM fine-tuné qui explique. La décision n'est exécutée que si la confiance dépasse un seuil. »

### c) Bonus MLOps (si on te pousse)
```powershell
Invoke-RestMethod http://192.168.148.161:8000/mlops/status | ConvertTo-Json -Depth 3
Invoke-RestMethod http://192.168.148.161:8000/mlops/drift  | ConvertTo-Json -Depth 3
```
→ montre le **registre de modèles, la détection de dérive (drift), le ré-entraînement** = un vrai cycle MLOps.

---

## 🧪 Ce que tu PEUX vs NE PEUX PAS déclencher naturellement (à assumer)
| Déclenchable en live (air-gap) ✅ | Classé par le modèle, dur à déclencher ici ❌ |
|---|---|
| `NODE_NOT_READY` (panne worker) | `DB_DEADLOCK`, `DB_REPLICATION_LAG`, `DB_SLOW_QUERIES` |
| `DEPLOYMENT_UNAVAILABLE` / `AVAILABILITY_DROP` | `TLS_CERTIFICATE_EXPIRY`, `SECRET_ROTATION_FAILURE` |
| `VM_DOWN` + **failover DB** | `NETWORK_PARTITION`, `NETWORK_PACKET_LOSS`, `DNS_FAILURE` |
| Crash / reschedule de pod | `CPU_SATURATION`, `MEMORY_LEAK`, `POD_OOM_KILLED` (besoin d'images non en cache + stress) |

→ **Honnêteté = crédibilité.** Tu démontres ~5-6 types en live, tu montres la taxonomie des 52 pour la largeur.

---

## 📚 La taxonomie : HELIOS classe **52 types d'incidents** (à montrer pour la largeur)

Le modèle IA est **entraîné à reconnaître 52 types**, regroupés en familles. Montre cette liste (ou la page **Décisions IA**) pour prouver l'étendue :

| Famille | Types (exemples) |
|---|---|
| **Nœud / Infra** | `NODE_NOT_READY`, `NODE_HIGH_CPU`, `NODE_HIGH_MEMORY`, `NODE_DISK_FULL`, `NODE_DISK_IO_SATURATION`, `NODE_KUBELET_DOWN`, `NODE_CLOCK_SKEW`, `NODE_HARDWARE_FAILURE`, `VM_DOWN` |
| **Pods / Apps** | `POD_CRASH_LOOP`, `POD_OOM_KILLED`, `POD_IMAGE_PULL_BACKOFF`, `POD_PENDING_INSUFFICIENT_RESOURCES`, `POD_LIVENESS_FAILURE`, `POD_READINESS_FAILURE`, `POD_STUCK_TERMINATING`, `RESTART_LOOP`, `MEMORY_LEAK`, `MEMORY_STRESS`, `CPU_SATURATION`, `THREAD_POOL_EXHAUSTION`, `CIRCUIT_BREAKER_OPEN`, `APPLICATION_ERROR_STORM`, `AVAILABILITY_DROP`, `GENERAL_DEGRADATION` |
| **Base de données** | `DB_CONNECTION_POOL_EXHAUSTION`, `DB_CONNECTION_REFUSED`, `DB_DEADLOCK`, `DB_DISK_FULL`, `DB_HIGH_WRITE_LOAD`, `DB_REPLICATION_LAG`, `DB_SLOW_QUERIES`, `DATABASE_CONNECTIVITY` |
| **Réseau / Sécurité** | `NETWORK_DNS_FAILURE`, `NETWORK_PACKET_LOSS`, `NETWORK_PARTITION`, `NETWORK_LATENCY_BETWEEN_NODES`, `SERVICE_DISCOVERY_FAILURE`, `INGRESS_BACKEND_DOWN`, `EXTERNAL_API_DEPENDENCY_DOWN`, `TLS_CERTIFICATE_EXPIRY`, `SECRET_ROTATION_FAILURE`, `AUTH_BRUTE_FORCE_ATTEMPT`, `UNAUTHORIZED_API_ACCESS_SPIKE` |
| **Stockage / Observabilité** | `PVC_PENDING`, `PVC_USAGE_HIGH`, `PV_FILESYSTEM_CORRUPTION`, `METRICS_SCRAPE_FAILURE`, `LOKI_INGESTION_LAG`, `PROMETHEUS_OUT_OF_MEMORY`, `UNKNOWN_ANOMALY` |

### À DIRE honnêtement si l'encadrant demande « tu peux tous les déclencher ? »
> « Le modèle **classe** les 52 types (entraîné sur données de chaos + synthétiques). En **lab air-gap**, je reproduis naturellement un **sous-ensemble représentatif** (panne nœud, indisponibilité de déploiement, crash de pod). Les autres relèvent de la **capacité de classification** du modèle — visible sur la page Décisions IA et dans la taxonomie. »

C'est **plus crédible** que de prétendre tout déclencher : un jury valorise l'honnêteté + la compréhension des limites.

---

## ♻️ Remettre à zéro entre deux démos
```powershell
# attendre 3/3 Ready + opérateur Running avant de recommencer
kubectl get nodes ; kubectl get pods -n cnpg-system
```
Les incidents passent en `RESOLVED` automatiquement quand les nœuds reviennent.

## ⚠️ Rappels
- **Un worker à la fois.** Opérateur CNPG `Running` **avant** chaque test node.
- **Ne rallume jamais la VM toi-même** : laisse HELIOS/Camunda le faire (sinon la démo ne prouve rien).
- Ne pas augmenter la RAM de la VM `.161`.
