# Prompt à copier-coller dans ChatGPT

---

Tu reprends un projet de fin d'études : **HELIOS**, une infrastructure Kubernetes auto-réparatrice pour Orange Tunisie, déployée sur 6 machines virtuelles VMware. La chaîne est : Observabilité → Détection → Classification ML → RAG/CRAG → LLM → Catalogue d'actions → Camunda → Remédiation → Vérification.

Une session d'audit vient de se terminer. Elle a produit trois résultats que tu dois connaître **avant de proposer quoi que ce soit**, parce qu'ils invalident plusieurs approches évidentes.

---

# 1. Contexte infrastructure (déjà validé, ne pas remettre en cause)

| VM | IP | Rôle |
|---|---|---|
| k8s-master | .153 | Control-plane + Worker + membre etcd |
| k8s-worker1 | .158 | Control-plane + Worker + membre etcd |
| k8s-worker2 | .155 | Control-plane + Worker + membre etcd |
| k8s-monitoring | .160 | Prometheus/Grafana/Loki hors cluster + HAProxy + membre etcd |
| k8s-aiCamunda | .161 | Camunda/Zeebe + ML + LLM (Ollama) + ChromaDB |
| k8s-lb2 | .162 | HAProxy actif + membre etcd |

**Décisions d'architecture figées, imposées par l'encadrant ou mesurées :**

- **Pas de VIP, pas de Keepalived** — supprimés (« un VIP n'est pas une notion Kubernetes »)
- **etcd à 5 membres** (les 3 nœuds K8s + `.160` + `.162`) → quorum 3, tolère 2 pannes
- Chaque `kubelet.conf` pointe vers **son propre API server local** → pas de SPOF sur `.160`
- **HAProxy actif/actif** `.160` + `.162`, bascule client par DNS (2 A-records)
- **MetalLB installé mais pas le chemin principal** (annonce L2 non routable sur VMware NAT)
- **Monitoring 100 % hors cluster** (systemd sur `.160`, zéro pod de monitoring)
- **LLM : qwen2.5:3b + RAG**, mesuré à **91 %** d'action catalogue exacte

Ne propose ni VIP, ni Keepalived, ni migration vers MetalLB, ni monitoring in-cluster.

---

# 2. Audit du dépôt — ce qui existe réellement

## 2.1 Les 34 types d'incidents

`camunda-direct/detecteur/detecter.py`, bloc `REGLES = [...]` — 34 tuples
`(TYPE, requête PromQL ou LogQL, source, extracteur)`. **L'ordre encode la priorité** : la première règle qui matche gagne. Les seuils sont calibrés sur le matériel réel (ex. `NODE_HIGH_CPU` à 96 % parce que le repos est à ~90 %).

## 2.2 Outils d'injection — 16 incidents déjà injectables

Deux outils complets existent dans le dépôt :

**`scripts/inject-one.sh`** (bash, 16 types) contient un tableau `declare -A FIX` qui mappe chaque type à l'action HELIOS attendue — **c'est déjà une vérité terrain type → action** :

```
crashloop  imagepull  oom      cpu     service   network
dns        disk       http5xx  latency pending   init
config     unavailable         db      hpa
```

**`scripts/chaos/helios-chaos.ps1`** (PowerShell, 335 lignes, 14 tests) cible `podinfo` dans le namespace `pfe`. 100 % kubectl + busybox embarqué, air-gap safe.

> Détail important : la fonction `Schedule-SelfRestore` a été **volontairement désactivée**, avec ce commentaire dans le code : *« Avant, ceci enlevait la panne PENDANT la fenêtre de vérification de HELIOS → HELIOS "constatait" un rétablissement qu'il n'avait PAS provoqué = faux RESOLVED »*. C'est une correction d'intégrité expérimentale à **conserver**.

## 2.3 🔴 Le blocage principal : le collecteur

`camunda-direct/detecteur/collecteur_ml_host.py` produit
`/opt/pfe-ai-service/datasets/host_incidents_reels.csv` toutes les 30 s, avec **15 colonnes, toutes de niveau hôte** :

```
timestamp, node, host_cpu_pct, host_mem_pct, host_disk_pct, host_inodes_pct,
host_fd_pct, host_disk_io_pct, host_cpu_steal_pct, host_oom_kills_5m,
host_network_errors_ps, host_time_drift_seconds, etcd_fsync_p99_seconds,
etcd_has_leader, pvc_not_bound_count, label
```

**Aucune feature de niveau pod.** Le collecteur est donc structurellement incapable de distinguer `POD_CRASH_LOOP` de `POD_CONFIG_ERROR` ou `POD_IMAGE_PULL_BACKOFF`.

| | Nombre |
|---|---:|
| Incidents injectables (script existant) | **16** |
| Incidents **collectables** avec le schéma actuel | **15** |
| 🔴 Non collectables — feature manquante | **19** |

> **Conclusion : le blocage n'est pas l'injection, c'est la collecte.** On peut provoquer `POD_CRASH_LOOP` dès maintenant, mais rien d'exploitable n'est enregistré.

## 2.4 Deux catalogues d'actions divergents

| Fichier | Contenu | Format |
|---|---|---|
| `backend/backend/ai-service/app/models/incident_catalog.py` | 129 entrées | `resolution` en **texte libre** |
| `infra/remediation-action-catalog.yaml` | 14 actions + 9 portes de validation | `action_id` **énuméré** |

Le dataset LLM a besoin de l'`action_id` énuméré. Une table de correspondance `(type, cause) → action_id` reste à établir.

---

# 3. 🔴 Audit qualité — 7 problèmes bloquants mesurés

Tests exécutés sur les datasets réellement présents dans le dépôt. **Chiffres issus d'exécution, pas d'estimation.**

## 3.1 Dataset ML : `aiops_training_dataset.csv` (1 200 × 425)

### B1 — Target leakage : la colonne `scenario` EST le label

| Label | Scénarios déterminant le label à 100 % |
|---|---|
| `incident_label` | **28 / 28** |
| `severity_label` | **26 / 28** |
| `action_label` | **27 / 28** |

```
APP_ERROR_STORM   → RESTART_OR_SCALE                  (toujours)
DB_CONNECTIVITY   → CHECK_DB_CONNECTIVITY_AND_RESTART (toujours)
```

Les 416 features sont générées **à partir du scénario**, qui détermine lui-même l'étiquette. Il n'y a rien à apprendre dedans.

### B2 — Label leakage : features à séparation parfaite

| Label | Features à séparation parfaite |
|---|---:|
| `incident_label` | **4** |
| `severity_label` | **11** |
| `action_label` | **11** |

Exemples mesurés :

```
feature_overall_risk_score : False [0,0123..0,0617] | True [0,0948..0,7585]  → 0 chevauchement
feature_http_5xx_ratio     : False [0,0000..0,0000] | True [0,0034..0,8845] → 0 chevauchement
feature_metric_risk_score  : False [0,0092..0,0383] | True [0,0529..0,5320] → 0 chevauchement
```

`feature_http_5xx_ratio` vaut **exactement 0,0000** pour tous les cas sains — signature d'une génération programmée.

### B3 — Aucune information temporelle

`end_time` : **0 / 1200 parsables**, colonne entièrement NaN.
→ Split temporel impossible, features de tendance impossibles.

### B4 — Zéro diversité d'environnement

`namespace` = 1 valeur (`pfe`) · `app` = 1 valeur (`backend`)
→ Le modèle ne peut apprendre aucune invariance.

### B5 — Signature synthétique

**0 NaN sur 416 features et 1 200 lignes.** Une capture Prometheus réelle sur 6 VMs produit toujours des trous (exporter redémarré, scrape en timeout). `event_id` est un compteur séquentiel 1,2,3…

> **Ce dataset n'est pas réparable par nettoyage.** Le problème est structurel. À archiver, pas à corriger.

## 3.2 ⭐ Datasets LLM — la cause de la régression du fine-tuning

| Fichier | Exemples | Doublons exacts | **Sorties distinctes** |
|---|---:|---:|---:|
| `datasets/lora_v3_real.jsonl` | 1 124 | 75 (6,7 %) | 🔴 **67 (6 %)** |
| `datasets/lora_instructions_v2.jsonl` | 1 055 | 45 (4,3 %) | 🔴 **52 (5 %)** |
| `backend/backend/datasets/finetune_dataset.jsonl` | 1 110 | 🔴 **342 (30,8 %)** | — |

### Ce que ça explique

Le commit `24f0346` du projet constate :

> *« le fine-tuné helios-120-3b régresse (0 %) : bon diagnostic mais **actions vagues**, format ignoré »*

Le commit `fb7f4a2` supposait la cause : *« dataset REASONED verbeux »*.

**La cause réelle est mesurée :** 1 124 exemples pour seulement 67 réponses distinctes = chaque réponse répétée ~17 fois. Le modèle n'a pas appris à raisonner, il a appris à **classer dans 67 tiroirs**. D'où les actions génériques.

> Le fine-tuning n'était pas inadapté. **Le dataset l'était.**

**Seuil à respecter :** diversité de sortie **> 70 %**. Pour 13 000 exemples SFT, viser > 9 000 sorties distinctes.

## 3.3 Autres résultats

| Vérification | Résultat |
|---|---|
| Doublons exacts (ML) | 0 |
| Near-duplicates cos > 0,98 | 0 sur 719 400 paires |
| Labels contradictoires | 0 |
| Déséquilibre des classes | ratio 6,2× à 9,4× |
| Classe la plus petite | 55 exemples |

---

# 4. Décision sur le corpus — aucune fusion

## 4.1 Le constat

| | |
|---|---:|
| Classes HELIOS couvertes par un dataset public utilisable | **0 / 34** |
| Correspondance partielle (schéma incompatible) | 13 |
| Sans aucune donnée publique | **21** |

## 4.2 Pourquoi la fusion serait nuisible

1. **Les grandeurs diffèrent.** RCAEval mesure `cartservice_mem` (mémoire d'un conteneur applicatif). HELIOS mesure `node_memory_MemAvailable_bytes` (mémoire d'une VM). Aucune conversion ne les rend compatibles.
2. **Le décalage de domaine est le mécanisme de l'échec déjà mesuré** : `collecteur_ml_host.py` ligne 8 documente *« le 120-synthetique avait triché (F1 0.974 mais transfert reel 0.00) »*.
3. **Les seuils sont calibrés sur le matériel réel** (repos à 90 % de CPU).

## 4.3 Architecture retenue : un corpus, quatre rôles séparés

**Une seule source d'entraînement : le corpus HELIOS réel.**

| Source externe | Rôle | Traverse la frontière ? |
|---|---|---|
| **LogHub** (github.com/logpai/loghub) | Entraîner un parseur Drain | ⚙️ L'algorithme, jamais les données |
| **RCAEval** (MIT, 735 cas, github.com/phamquiluan/RCAEval) | Benchmark de comparaison | 🔴 Zéro transfert |
| **ITBench** (Apache-2.0, github.com/itbench-hub/ITBench) | Générateur de scénarios — **ce n'est pas un dataset** | 🔧 Outil |
| **OpsEval** (HF) | Évaluation initiale du LLM | 📊 Mesure |

Quatre corpus **physiquement séparés** : `helios_real/`, `helios_augmented/`, `public/`, `synthetic_legacy/`. Contrôle avant entraînement :
`assert set(df['source_corpus'].unique()) == {'helios_real'}`

**Écartés** : camel-ai/aiops (synthétique généré par LLM, 0 classe), CCF AIOps / AIOps2025 (redondants, accès non vérifié), kubernetes-stackoverflow (aucune télémétrie). **OpenRCA, RCA100, AnoMod : NON VÉRIFIÉ, ne pas les citer.**

---

# 5. Corpus cible — chiffres dérivés

```
Dataset ML   : 10 200 exemples · 34 classes · 58 features
               ← 3 400 injections × 3 phases (t+30s, t+3min, t+8min)
               ← 58 = 13 host + 5 nœud + 14 pod + 5 deploy
                      + 4 etcd + 9 logs + 5 temporel + 3 contexte
Corpus RAG   : 34 fiches → ~340 chunks · embeddings bge-m3 · ChromaDB
Dataset SFT  : 13 000 exemples + 2 500 paires DPO
Benchmark    : 3 datasets (RCAEval, ITBench, OpsEval)
Collecte     : 3 400 injections · ~13 jours
```

## 5.1 Calcul du temps de collecte

```
Cycle réel = 12,5 min   (les 3 phases imposent 8 min à elles seules)

Pod  (16 classes) : parallélisable sur 3 nœuds → 345 injections/jour
Host (15 classes) : 1 nœud à la fois           → 115 injections/jour
                    ⚠️ saturer 3 nœuds casserait le quorum etcd
etcd  (3 classes) : lab isolé, cycle 3 min     → 480 injections/jour

16×100 ÷ 345 =  4,6 j  |  15×100 ÷ 115 = 13,0 j  |  3×100 ÷ 480 = 0,6 j
Pod et host en parallèle → durée réelle ≈ 13 jours
```

## 5.2 Les 3 incidents etcd — méthode sûre

Ne jamais toucher l'etcd du cluster. Monter un **cluster etcd isolé en conteneurs** (3 membres, réseau Docker dédié, `--listen-metrics-urls` exposé). Les métriques produites sont **réelles** (mêmes binaires, mêmes compteurs Prometheus), le risque est nul.

---

# 6. Ce que tu dois faire

## 6.1 Priorité absolue — le collecteur

**Étendre `camunda-direct/detecteur/collecteur_ml_host.py` de 15 à 58 features**, plus les métadonnées indispensables :

```
Métadonnées à ajouter : timestamp ISO 8601, injection_id, phase,
                        namespace, app, deployment, pod,
                        label_cause, label_action_id, source_corpus,
                        detector_agreed

Features à ajouter (43) :
  host (3)    : host_load15_per_core, host_pids_pct, host_swap_pct
  nœud K8s (5): k8s_node_ready, k8s_node_disk_pressure, k8s_node_pid_pressure,
                k8s_node_network_unavailable, k8s_node_unschedulable
  pod (14)    : pod_restart_count, pod_restart_rate_1h,
                pod_last_exit_code            ⭐ 137=OOM · 1=config · 127=image
                pod_last_terminated_oomkilled ⭐
                pod_waiting_crashloop, pod_waiting_imagepull, pod_waiting_config,
                pod_phase_pending, pod_phase_failed, pod_scheduled_false,
                pod_init_container_ready, container_ready_ratio,
                container_mem_limit_ratio     ⭐
                container_cpu_throttled_pct
  deploy (5)  : deploy_replicas_desired/available/unavailable,
                endpoint_addresses_available, hpa_at_max
  etcd (1)    : etcd_leader_changes_1h
  logs (9)    : log_lines_total, log_error_count, log_error_ratio,
                hits_oom, hits_dns, hits_conn_refused,
                hits_timeout, hits_no_route, hits_exec_format
  temporel (5): cpu_delta_5m, mem_delta_5m, disk_delta_1h,
                restart_delta_10m, seconds_since_last_deploy
  contexte (3): nodes_ready_count, cluster_mem_pressure, concurrent_incident_count
```

**Règle critique :** quand un exporter est mort, écrire `NaN`, **jamais 0**. Un `0` signifierait « mesuré à zéro » — c'est faux et cela apprendrait une fausse frontière. LightGBM gère nativement les `NaN`.

## 6.2 Features formellement interdites

```
feature_overall_risk_score · feature_log_risk_score · feature_metric_risk_score
rule_label · incident_detected · rule_score · alert_fired · nbAnomalies
scenario · label_* · detector_agreed · raw_*
```

## 6.3 Porte qualité à intégrer avant tout entraînement

Une fonction qui lève une exception si le corpus est contaminé, testant :

1. **Séparation parfaite** — une feature dont les plages ne se chevauchent pas entre deux classes = fuite
2. **Target leakage** — une colonne qui détermine le label à 100 %
3. **Diversité d'environnement** — `namespace`, `app`, `node` non constants
4. **Timestamps** — présents et parsables à > 99 %
5. **`injection_id`** — présent (sinon split sans fuite impossible)
6. **Pureté du corpus** — `source_corpus == {'helios_real'}`

## 6.4 Ordre d'exécution

| # | Étape | Durée | Condition |
|---:|---|---|---|
| 1 | Archiver `aiops_training_dataset.csv` → `corpus/synthetic_legacy/` | 10 min | — |
| 2 | Étendre le collecteur (58 features + métadonnées) | 2 j | — |
| 3 | Table `mapping_actions.yaml` : (type, cause) → action_id | 0,5 j | — |
| 4 | Orchestrateur 3 phases + 7 critères de validité | 1,5 j | — |
| 5 | Intégrer la porte qualité | 0,5 j | — |
| 6 | **Pilote 5 incidents** | 0,5 j | ✅ porte qualité passée |
| 7 | Écrire les 14 scripts d'injection host | 2 j | — |
| 8 | Monter le lab etcd isolé | 0,5 j | — |
| 9 | Collecte massive automatisée | ~13 j | ✅ pilote validé |
| 10 | Audit qualité du corpus réel | 0,5 j | ✅ 0 bloquant |
| 11 | Entraînement | — | ✅ audit passé |

**Pilote proposé (5 incidents couvrant 5 catégories) :**
`POD_CRASH_LOOP` (pod, 6 causes) · `DEPLOYMENT_UNAVAILABLE` (deployment, fourre-tout) · `NODE_HIGH_MEMORY` (ressources) · `DNS_FAILURE` (réseau) · `NODE_CORDONED` (nœud)
→ 4 sur 5 utilisent des scripts existants.

## 6.5 Sept critères de validité d'un exemple

Un exemple n'entre dans le corpus que si :

1. Le détecteur a identifié **le bon type** (`type_detecte == type_injecte`)
2. La cause injectée est enregistrée (`label_cause` non vide)
3. Aucun `NaN` sur les features clés de la classe
4. Les 3 phases sont distinctes (similarité cosinus < 0,92)
5. Cluster sain à `t-120s`
6. Cluster revenu sain à `t+700s`
7. `concurrent_incident_count == 0` (sauf test multi-incidents)

---

# 7. Protocole expérimental à 8 configurations

| # | Configuration | Ce qu'elle isole |
|---:|---|---|
| **C0** | ⭐ **Table `type → action`** | **Baseline obligatoire** |
| C1 | Règle seule (`detecter.py`) | Vérité terrain de détection |
| C2 | ML brut (58 features) | Le ML apprend-il ? |
| C3 | ML + features temporelles | Apport du temporel |
| C4 | LLM seul, sans RAG | Connaissance pré-entraînée |
| C5 | LLM + RAG | Apport du RAG (réf. actuelle : 0,91) |
| C6 | LLM + CRAG | Apport de la correction de retrieval |
| C7 | ML + CRAG + LLM | Chaîne complète |
| C8 | + SFT *(conditionnel)* | Apport du fine-tuning |

> **C0 est la configuration que tout le monde oublie.** Sans elle, impossible de prouver que l'IA apporte quelque chose : sur les incidents à cause unique, une table atteint 100 % par construction.

## L'hypothèse centrale

> **H₀** : sur les incidents à cause unique, C7 ne bat pas C0.
> **H₁** : sur les incidents **multi-causes**, C7 bat C0 significativement.

C'est la seule hypothèse qui justifie le projet. Elle se teste sur :
`POD_CRASH_LOOP` (6 causes), `DEPLOYMENT_UNAVAILABLE` (6), `SERVICE_UNAVAILABLE` (3), `DNS_FAILURE` (3), `NODE_NOT_READY` (3).

## Critères de décision

```
Déployer le ML  si  F1 macro(C2) > 0,75  ET  généralisation > 0,70
Retenir le RAG  si  cause(C5) > cause(C4) + 0,10
Retenir le CRAG si  cause(C6) > cause(C5) + 0,05
Déployer le SFT si  action(C8) > 0,95  ET  cause(C8) > 0,85
                    ET  généralisation(C8) > 0,75
Sinon           →   conserver la configuration précédente
```

## Split sans fuite

**Par `injection_id` ET temporel, jamais par ligne.** Les 3 phases d'une injection partagent la cause, le pod et le contexte. Réserver au test 3 configurations jamais vues à l'entraînement : une application, un nœud, une cause.

---

# 8. Règles impératives

1. **Aucune donnée inventée.** Si une information n'est pas vérifiable dans le dépôt, écris **NON VÉRIFIÉ**.
2. **`root_cause_id` et `action_id` ne viennent JAMAIS d'un LLM.** Ils viennent de la cause injectée (vérité terrain). Un LLM peut reformuler `diagnosis`, `why_this_action`, `impact` — jamais décider.
3. **Aucune augmentation synthétique pour le ML.** 10 200 exemples réels sont atteignables en 13 jours d'injection automatisée.
4. **Ne pas fusionner de dataset public** dans le corpus d'entraînement.
5. **Ne pas modifier l'architecture HA validée** (VIP, Keepalived, etcd, HAProxy, MetalLB).
6. **Ne lancer aucun entraînement** avant que la porte qualité passe.
7. **Donner les chemins exacts** des fichiers utilisés.
8. **Expliquer avant de modifier** un fichier important.

---

# 9. Points NON VÉRIFIÉS

| Élément | Raison |
|---|---|
| `host_incidents_reels.csv` — volume et contenu | Hors dépôt, sur `.153/.155/.158` |
| Le collecteur tourne-t-il actuellement ? | Nécessite un accès SSH |
| Licence exacte de LogHub | Dépôt non consulté directement |
| Contenu de SREGym | Publication lue, données non consultées |
| OpenRCA, RCA100, AnoMod | Non consultés — ne pas les citer |
| `experiments/capture_real_chaos_v3.py` | Non inspecté |

---

# 10. Ce que je te demande maintenant

Commence par **le point 6.1 : étendre le collecteur**. C'est le seul chantier vraiment bloquant — sans lui, chaque injection produit une ligne inexploitable pour 19 classes sur 34.

Je vais te fournir le contenu actuel de `camunda-direct/detecteur/collecteur_ml_host.py` (environ 130 lignes). Attends ce fichier avant de proposer du code : base-toi sur sa structure réelle (fonctions `prom()`, `http()`, écriture CSV incrémentale, service systemd), ne réinvente pas l'architecture.

Réponds d'abord en confirmant que tu as compris le contexte, et liste les questions bloquantes que tu as — s'il y en a — avant que je colle le fichier.
