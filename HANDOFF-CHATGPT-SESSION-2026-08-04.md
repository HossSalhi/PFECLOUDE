# HELIOS — Passation de contexte pour ChatGPT

**Session du 04/08/2026 · PFE Orange Tunisie**

> Ce document résume l'intégralité du travail effectué. Il est destiné à être
> collé dans ChatGPT pour qu'il reprenne le travail avec le contexte complet,
> sans refaire les erreurs déjà commises et corrigées.

---

# PARTIE 0 — Instructions pour ChatGPT

Tu reprends un projet de fin d'études : **HELIOS**, une infrastructure
Kubernetes auto-réparatrice (détection → diagnostic IA → remédiation
orchestrée) déployée sur 6 machines virtuelles VMware.

**Trois règles impératives :**

1. **Ne remets pas en cause les décisions d'architecture validées** listées en
   Partie 2. Elles ont été mesurées ou imposées par l'encadrant. En
   particulier : **pas de VIP, pas de Keepalived, pas de MetalLB comme chemin
   principal**.
2. **Ne propose pas de refaire du fine-tuning « pour voir »** ni de chercher
   des datasets publics pour entraîner. Ces deux voies ont été mesurées
   perdantes (Partie 4). Les chiffres sont dans le dépôt.
3. **Si tu n'es pas sûr d'un fait technique, dis-le.** L'utilisateur a
   explicitement demandé « NON VÉRIFIÉ » plutôt que des informations
   inventées. Cette exigence reste valable.

---

# PARTIE 1 — L'infrastructure réelle (état vérifié)

## 1.1 Les 6 VMs

| VM | Nom | IP | Rôle réel |
|---|---|---|---|
| VM1 | k8s-master | .153 | Control-plane + Worker + membre etcd |
| VM2 | k8s-worker1 | .158 | Control-plane + Worker + membre etcd |
| VM3 | k8s-worker2 | .155 | Control-plane + Worker + membre etcd |
| VM4 | k8s-monitoring | .160 | Monitoring hors cluster + HAProxy + **membre etcd** |
| VM5 | k8s-aiCamunda | .161 | Camunda/Zeebe + ML + LLM + ChromaDB (**pas** membre etcd) |
| VM6 | k8s-lb2 | .162 | HAProxy actif + **membre etcd** |

Réseau : `192.168.148.0/24` (VMware NAT). Cluster kubeadm 1.29, Calico CNI,
pod CIDR `192.168.0.0/16`.

## 1.2 Points d'architecture déjà en place et VÉRIFIÉS

| Élément | État |
|---|---|
| **Cluster HA 3 control-planes** | ✅ Les 3 nœuds sont control-plane + worker (les noms « worker » sont trompeurs) |
| **Aucun VIP, aucun Keepalived** | ✅ Le VIP `.150` a été supprimé sur demande de l'encadrant |
| **Pas de SPOF sur `.160`** | ✅ Chaque `kubelet.conf` pointe vers **son propre API server local** (`.153→.153:6443`, etc.) |
| **HAProxy actif/actif** | ✅ `.160` **et** `.162` en parallèle, bascule côté client par DNS (2 A-records pour `helios.orange.tn`) |
| **etcd à 5 membres** | ✅ `.153` `.155` `.158` `.160` `.162` — quorum 3, **tolère 2 pannes** |
| **Gardien de quorum** | ✅ `helios-quorum-guard` (systemd), 5 garde-fous anti-split-brain |
| **Monitoring 100 % hors cluster** | ✅ 0 pod de monitoring ; Prometheus/Grafana/Loki en systemd sur `.160` |
| **kube-state-metrics externalisé** | ✅ Binaire systemd sur `.160`, scrape `127.0.0.1:8080` |
| **Chiffrement etcd** | ✅ `aescbc` avec rotation de clé effectuée |
| **Pod Security Standards** | ✅ `baseline` enforce, `restricted` warn/audit |
| **CNPG PostgreSQL HA** | ✅ 2 instances, réplication synchrone, anti-affinity `required` |
| **Falco eBPF** | ✅ DaemonSet + webhook vers le backend |
| **34 types d'incidents détectés** | ✅ Règles déterministes dans `camunda-direct/detecteur/detecter.py` |

## 1.3 Chaîne de trafic applicatif

```
Navigateur → helios.orange.tn (DNS → .160 ET .162)
          → HAProxy (actif/actif)
          → NodePort ingress (:31756 http / :31411 https)
          → ingress-nginx → Service → Pods
```

**MetalLB est installé mais n'est PAS le chemin principal** — l'annonce L2
(ARP) n'est pas routable depuis l'hôte Windows sur ce réseau VMware NAT.
C'est un choix délibéré et testé.

---

# PARTIE 2 — Décisions validées, à ne pas remettre en cause

| Décision | Justification |
|---|---|
| **Pas de VIP / Keepalived** | Demande explicite de l'encadrant : « un VIP n'est pas une notion Kubernetes ». Le SPOF est résolu autrement (API server local par nœud). |
| **HAProxy → NodePort plutôt que MetalLB** | MetalLB L2 non routable sur VMware NAT. HAProxy actif/actif + DNS donne une HA réelle et testée. |
| **Monitoring hors cluster** | Demande de l'encadrant, et défendable : le monitoring doit survivre à la panne de ce qu'il surveille. |
| **NetworkPolicies absentes de `pfe`** | `pfe` est le namespace-laboratoire de chaos : le détecteur y **injecte** des NetworkPolicies pour simuler des pannes. Le modèle zero-trust est fourni pour `helios-prod`. |
| **etcd à 5 membres** | `.160` et `.162` ajoutés en `--learner` puis `promote`. Fait passer la tolérance de 1 à 2 pannes sans ajouter de nœud K8s. |
| **qwen2.5:3b + RAG comme LLM principal** | Mesuré à **91 %** d'action catalogue exacte. Le fine-tune régressait à 0 %. |

---

# PARTIE 3 — Travail effectué durant la session

## 3.1 Audit vidéos TechWorld with Nana

Analyse des 23 vidéos du dossier `video kube` et comparaison avec le projet.

**Livrable :** `AUDIT-VIDEOS-NANA-vs-HELIOS.docx`
**Score : 82/100** — 16 concepts sur 21 conformes.

## 3.2 Corrections de sécurité appliquées

| Fichier | Correction |
|---|---|
| `backend/backend/infra/k8s/backend-rbac.yaml` | 🔴 **Retiré `pods: create`** du Role `backend-remediator`. Ce verbe servait à créer un pod privilégié éphémère avec `nsenter` sur l'hôte = **accès root sur le nœud**. Escalade de privilèges complète. |

## 3.3 Monitoring des 6 VMs — créé, pas encore déployé

| Fichier | Contenu |
|---|---|
| `infra/monitoring/prometheus-6vms.yml` | Scrape des 6 VMs + apiserver + **5 membres etcd** + scheduler + controller-manager + kubelet + cAdvisor + blackbox |
| `infra/monitoring/rules-infrastructure.yml` | **66 règles d'alerte** avec labels `severity`, `helios_type`, `helios_target`, `auto_remediate` |
| `infra/monitoring/alertmanager-camunda.yml` | Routage event-driven, groupement, **inhibition en cascade** (21 alertes → 1 incident) |
| `infra/monitoring/promtail-vms-hors-cluster.yml` | journald, syslog, kern.log (OOM-killer), auth.log |
| `infra/11-install-monitoring-6vms.sh` | Installe node_exporter (`--collector.systemd`) + Promtail + blackbox |
| `infra/09-fix-monitoring-vm.sh` | Installe **Alertmanager** + Registry Docker sur `.160` |

**Vérifications effectuées sur ces fichiers :**
- ✅ YAML valide (parsing testé)
- ✅ Les 64 règles ont toutes `for:`, `severity`, `helios_type`, `helios_target`, `auto_remediate`, `summary`
- ✅ Seuils de quorum etcd cohérents avec 5 membres (`== 3` at-risk, `< 3` lost)
- ✅ Aucun type `auto_remediate: true` sans action au catalogue
- ✅ Aucun conflit entre `auto_remediate` et le routage Alertmanager
- ✅ Les 37 incidents listés dans le PDF du projet sont couverts

## 3.4 Catalogue d'actions de remédiation

**Fichier :** `infra/remediation-action-catalog.yaml`

14 actions, 9 portes de validation, 4 niveaux de risque, 18 motifs interdits.

Principe : **le LLM ne génère jamais de commande, seulement un `action_id`**
contraint à une énumération fermée.

## 3.5 Documents d'architecture et de stratégie

| Fichier | Contenu |
|---|---|
| `docs/ARCHITECTURE-HELIOS-PRO.md` | Architecture complète 13 parties : audit, HA, LB, monitoring, alerting, Camunda, remédiation, sécurité, scénarios de panne, ports, plan |
| `docs/RUNBOOK-ETCD-QUORUM.md` | Procédure DR perte de quorum, 4 scénarios de récupération |
| `docs/STRATEGIE-IA-HELIOS.md` | Stratégie ML/LLM, option A/B/C |
| `docs/STRATEGIE-DATASETS-MODELES-34-INCIDENTS.md` | Datasets, modèles, méthodologie |
| `docs/ETUDE-ML-LLM-34-CLASSES.md` | Étude 26 sections : classifieur 34 classes, modèles comparés, fine-tuning |
| `docs/DATASET-FINETUNING-10K-HELIOS.md` | Dimensionnement du dataset SFT, config QLoRA, protocole |
| `docs/DATASETS-REELS-ET-INJECTION-34.md` | ⭐ **Playbook d'injection des 34 incidents** + datasets vérifiés |

---

# PARTIE 4 — Faits vérifiés, avec preuves

Ces points ont été établis par inspection directe du code et des données.
**Ne pas les contredire sans nouvelle mesure.**

## 4.1 🔴 Le modèle ML actuel a une fuite de label

**Fichier :** `backend/backend/ai-service/datasets/aiops_training_dataset.csv`

| Feature | `incident=False` | `incident=True` | Chevauchement |
|---|---|---|---|
| `feature_overall_risk_score` | 0,012 – 0,062 | 0,095 – 0,759 | **aucun** |
| `feature_metric_risk_score` | 0,009 – 0,038 | 0,053 – 0,532 | **aucun** |

Séparation parfaite = le label est calculé depuis les features. Le
`"accuracy": 1.0` de `model_artifacts/model_metrics.json` **ne mesure rien**.

> ⚠️ **Ce chiffre ne doit pas figurer dans le rapport de PFE.** Un jury
> technique le repérera.

**Correction requise :** retirer `feature_overall_risk_score`,
`feature_log_risk_score`, `feature_metric_risk_score` du jeu d'entraînement.

## 4.2 Le ML actuel ne classifie PAS le type d'incident

Sorties réelles du modèle : `incident_label` (binaire), `severity_label`
(4 classes), `action_label` (6 classes grossières).

La classification en **type** est faite par `detecter.py` avec des **règles
déterministes** — 34 types, vérité terrain issue des motifs kubelet.

## 4.3 Le fine-tuning a été mesuré perdant, deux fois

**Commit `24f0346` :**
> « L'investigation PFE (ablation base×FT×RAG×CRAG, artifact f4beb39b) a mesuré
> que qwen2.5:3b + RAG nomme l'action catalogue EXACTE **91 %** du temps, alors
> que le fine-tuné helios-120-3b **régresse (0 %)** : bon diagnostic mais
> actions vagues, format ignoré. »

**Commit `fb7f4a2`** identifie la cause : *« dataset REASONED verbeux → actions
vagues »*. Un pipeline corrigé (SFT crisp + DPO) a été construit, avec un
**déploiement gaté à 0,93**.

**Le RAG apporte la vraie valeur : fidélité 0,73 → 0,91.**

## 4.4 Les données synthétiques ne transfèrent pas

**Fichier :** `camunda-direct/detecteur/collecteur_ml_host.py`, ligne 8 :
> « le 120-synthetique avait triché (**F1 0.974 mais transfert reel 0.00**) »

⚠️ **Conséquence directe** : construire un dataset de 10 000 exemples par
augmentation depuis ~1 360 injections réelles (86 % synthétique) reproduirait
cet échec. **Il faut produire des incidents réels.**

## 4.5 Le catalogue mappe type → résolution de façon déterministe

**Fichier :** `backend/backend/ai-service/app/models/incident_catalog.py`

- 129 entrées au format `type → {category, resolution, auto}`
- **30 des 34** types détectés par règles y ont une résolution **unique**
- **18** sont `auto: True`

> **Conséquence importante pour la soutenance** : pour ces 30 types, une table
> de correspondance suffit. La valeur de l'IA se concentre sur les **~13 types
> où la cause est ambiguë** (POD_CRASH_LOOP, DEPLOYMENT_UNAVAILABLE, etc.).
>
> C'est un résultat honnête et défendable, pas une faiblesse. Il faut
> l'assumer explicitement devant le jury.

## 4.6 Datasets publics — vérifiés par consultation directe

| Dataset | Licence | Statut | Verdict |
|---|---|---|---|
| **RCAEval** — [github.com/phamquiluan/RCAEval](https://github.com/phamquiluan/RCAEval) | ✅ **MIT** (vérifié) | 🟡 semi-réel, 735 cas K8s | ⚠️ Métriques **par service**, pas par nœud → schéma incompatible |
| **ITBench** — [github.com/itbench-hub/ITBench](https://github.com/itbench-hub/ITBench) | ✅ **Apache-2.0** (vérifié) | ⚠️ **Ce n'est PAS un dataset** | C'est un **générateur de scénarios** Ansible — utile pour produire des données |
| **LogHub** — [github.com/logpai/loghub](https://github.com/logpai/loghub) | **NON VÉRIFIÉ** | 🟢 réel, 19 jeux, 77 Go | Logs seuls, aucune métrique → parseur Drain |
| **camel-ai/aiops** | Apache-2.0 | 🔴 synthétique (généré par LLM) | ❌ Aucune utilité |

**Bilan de couverture des 34 classes HELIOS :**

```
Directement utilisables :              0
Correspondance partielle (schéma ≠) : 13
Aucune donnée publique :              21
```

## 4.7 Sur la taille du dataset de fine-tuning

**LIMA (Meta AI)** — [arxiv.org/pdf/2305.11206](https://arxiv.org/pdf/2305.11206) :
- 1 000 exemples curés → compétitif avec GPT-4
- ⭐ **Le modèle sur 1 000 exemples bat le même modèle sur 52 000**
- « diversity is much more important than quantity »

**Conclusion pour HELIOS :** 10 000 est le bon ordre de grandeur (34 types ×
~4 causes × ~3 contextes × ~2 sévérités ≈ 800 combinaisons × ~12 exemples),
mais **30 000 est contre-productif**.

---

# PARTIE 5 — Erreurs que j'ai commises et corrigées

À signaler pour que tu ne les répètes pas.

| Erreur | Correction |
|---|---|
| J'ai affirmé que le cluster n'était **pas HA**, que `.160` était un SPOF et qu'il fallait retirer Keepalived | **Faux.** J'avais audité les scripts `infra/ha-upgrade/` (des plans jamais nettoyés après exécution) au lieu de l'état déployé documenté dans `infra/README.md`. Tout était déjà fait. |
| J'ai dit **3 membres etcd → tolère 1 panne** | **Faux.** Le cluster a **5 membres** → quorum 3 → **tolère 2 pannes**. |
| J'ai écrit un script `10-local-apiserver-lb.sh` (HAProxy local par nœud) | **Redondant.** Chaque kubelet pointe déjà vers son API server local. Script neutralisé, note explicative dans `infra/10-local-apiserver-lb.OBSOLETE.md`. |
| J'ai recommandé d'**exclure les 3 classes etcd** du ML | **Trop prudent.** Solution trouvée : **cluster etcd isolé en conteneurs**, métriques réelles, zéro risque. Les 34 classes sont atteignables. |
| J'ai classé **ITBench comme un dataset** | **Faux.** C'est une boîte à outils d'injection Ansible. Plus utile, mais autrement. |
| J'ai sous-estimé **RCAEval** (« ~3 classes partielles ») | En réalité ~13 correspondances partielles, licence MIT vérifiée, mais schéma de features incompatible. |

---

# PARTIE 6 — Ce qui reste à faire

## 6.1 🔴 Bloquant — la chaîne d'alerte n'est pas branchée

**Vérifié dans le code :**

1. **`camunda-direct/bridge/bridge.py` n'a AUCUN endpoint `/alertmanager`.**
   Endpoints existants : `POST /exec`, `POST /action`, `POST /detecter`,
   `POST /trace`, `GET /sante`, `GET /actions`.
   → La config Alertmanager créée pointe vers une porte qui n'existe pas.

2. **`camunda-direct/camunda-full-cycle.bpmn` démarre sur un Timer** :
   ```xml
   <bpmn:startEvent id="Timer" name="Cycle 60s">
     <bpmn:timeCycle>R/PT60S</bpmn:timeCycle>
   ```
   → Polling SSH toutes les 60 s, au lieu d'être poussé par webhook.

**Travail à faire :**

| # | Tâche | Fichier |
|---:|---|---|
| 1 | Ajouter `POST /alertmanager` : parse le payload Alertmanager, extrait `groupKey`/`helios_type`/`helios_target`, publie un message Zeebe corrélé (`AlerteRecue`, `correlationKey = groupKey`). Gérer `status: resolved` → message `IncidentResolu`. Auth HTTP Basic. | `bridge.py` |
| 2 | Remplacer le `Timer` par un **Message Start Event** `AlerteRecue` | `camunda-full-cycle.bpmn` |
| 3 | Supprimer la tâche `Detecter` (SSH → Prometheus) — Alertmanager fournit déjà le contexte | idem |

## 6.2 Déploiement (après 6.1)

| # | Action | VM |
|---:|---|---|
| 4 | `bash infra/09-fix-monitoring-vm.sh` (Alertmanager + Registry) | `.160` |
| 5 | Copier `prometheus-6vms.yml` → `/etc/prometheus/prometheus.yml` | `.160` |
| 6 | Copier `rules-infrastructure.yml` → `/etc/prometheus/rules/` | `.160` |
| 7 | Copier `alertmanager-camunda.yml` → `/etc/alertmanager/alertmanager.yml` | `.160` |
| 8 | `bash infra/11-install-monitoring-6vms.sh <nom-vm>` | les 6 |
| 9 | Reporter le token généré dans `HELIOS_ALERTMANAGER_TOKEN` | `.161` |

**Noms attendus pour le script 11 :** `k8s-master`, `k8s-worker1`,
`k8s-worker2`, `monitoring`, `aicamunda`, `lb2`.

## 6.3 ⛔ Scripts à NE PAS exécuter

| Fichier | Raison |
|---|---|
| `infra/10-local-apiserver-lb.sh` | Redondant — le script s'arrête de lui-même avec un message |
| `infra/ha-upgrade/01`→`03` | Migration HA **déjà faite** |
| `infra/ha-upgrade/04-install-keepalived.sh` | Keepalived **déjà retiré** |
| `infra/08-metallb-ingress-pro.sh` | ⚠️ Changerait le chemin de trafic actuel qui fonctionne |

## 6.4 Chantier IA

| # | Tâche | Durée |
|---:|---|---|
| 1 | Retirer les 3 features fuitées, ré-entraîner, split temporel | 3 h |
| 2 | Automatiser l'injection des 34 incidents (playbook fourni) | 2 j |
| 3 | Monter le lab etcd isolé (3 conteneurs) | 2 h |
| 4 | Injection 24/7 en parallèle sur 3 nœuds | 4 j (passif) |
| 5 | Rédiger les 34 fiches RAG | 2 j |
| 6 | Entraîner LightGBM 34 classes | 1 j |
| 7 | Construire le dataset SFT + validation | 3 j |
| 8 | QLoRA SFT + DPO sur Kaggle | 1 sem. |
| 9 | Protocole expérimental 6 configurations | 2 j |

---

# PARTIE 7 — Architecture IA cible

```
Prometheus/Loki → Context Builder (~70 features BRUTES)
                       │
        ┌──────────────┴──────────────┐
     RÈGLE                    ML (LightGBM, 34 classes)
  (vérité terrain)            (second avis + probabilités)
        └──────────────┬──────────────┘
                  ARBITRAGE
                       │
              RAG (ChromaDB + CRAG)
                       │
       LLM qwen2.5:3b (cause + action_id contraint)
                       │
           ACTION CATALOG (9 portes)
                       │
        CAMUNDA → Bridge → Exécution → Vérification
```

## Répartition des responsabilités

| Couche | Fait | Ne fait PAS |
|---|---|---|
| **Règles** | Type d'incident (34, vérité terrain) | Deviner un type inconnu |
| **ML** | Second avis, probabilités, anomalie, sévérité | Recevoir le verdict de la règle en entrée (⚠️ circulaire) |
| **RAG** | Symptômes, causes, runbooks, historique | — |
| **LLM** | ⭐ **Discriminer la cause en lisant les logs** + `action_id` | Générer une commande shell |
| **Catalogue** | 14 actions autorisées, 9 portes | Être contourné |
| **Camunda** | Orchestration, retry, rollback, approbation | Décider quoi faire |

## Le point décisif : condition de non-circularité du ML

> **Le ML doit être entraîné sur les signaux BRUTS uniquement, sans jamais
> recevoir le verdict de la règle ni aucune feature qui en dérive.**

Sinon → F1 = 1,0 illusoire (c'est le problème actuel).
Avec cette condition → F1 réel 0,80–0,88, et le ML apporte quatre choses :
généralisation au-delà des seuils durs, arbitrage entre règles concurrentes,
second avis pour la défense en profondeur, livrable scientifique évaluable.

## Choix de modèles arrêtés

```
ML  : LightGBM (baseline RandomForest, alternative CatBoost si surapprentissage)
      + IsolationForest pour l'anomalie hors catalogue
LLM : qwen2.5:3b-instruct + RAG + décodage contraint  (91 % mesuré)
      Fine-tuning UNIQUEMENT sur le format, déployé si > 0,93
```

Contrainte matérielle `.161` : 3 vCPU, 6 Go RAM partagés. Budget Ollama ~3 Go
→ un modèle 7B ne tient pas.

---

# PARTIE 8 — Dimensionnement des datasets

| Dataset | Volume | Délai | Source |
|---|---:|---|---|
| **ML** | 10 200 lignes × 70 features | 4 j d'injection | 3 400 injections × 3 phases |
| **LLM SFT** | 13 370 exemples | +3 j de mise en forme | mêmes injections |
| **DPO** | 2 500 paires | +2 j | dérivées du SFT |
| **RAG** | 34 fiches | 2 j de rédaction | expertise |

## Comment atteindre 10 000 exemples 100 % réels

```
Cycle automatisé                          ≈ 5 min
3 nœuds en parallèle, 24 h/24             = 864 incidents/jour
3 phases par injection (t+30s/3min/8min)  = 3 exemples légitimes
──────────────────────────────────────────────────────────────
3 400 injections × 3 phases = 10 200 exemples RÉELS = 4 jours
```

> ⚠️ **Limite à ne pas franchir** : ne jamais échantillonner toutes les 15 s.
> 20 points sur une même injection ne sont pas 20 exemples — c'est de la
> duplication déguisée. **3 phases maximum**, espacées de plusieurs minutes.

## Règle absolue sur la génération assistée par LLM

> `root_cause_id` et `action_id` ne viennent **jamais** d'un LLM. Ils viennent
> de la cause que tu as toi-même injectée (vérité terrain). Un LLM peut
> reformuler `diagnosis` ou `why_this_action` — jamais décider.

---

# PARTIE 9 — Inventaire des fichiers

## Créés

```
docs/ARCHITECTURE-HELIOS-PRO.md                    architecture complète
docs/RUNBOOK-ETCD-QUORUM.md                        procédure DR
docs/STRATEGIE-IA-HELIOS.md                        stratégie ML/LLM
docs/STRATEGIE-DATASETS-MODELES-34-INCIDENTS.md    datasets et modèles
docs/ETUDE-ML-LLM-34-CLASSES.md                    étude 26 sections
docs/DATASET-FINETUNING-10K-HELIOS.md              dimensionnement SFT
docs/DATASETS-REELS-ET-INJECTION-34.md             ⭐ playbook d'injection
AUDIT-VIDEOS-NANA-vs-HELIOS.docx                   audit 23 vidéos (82/100)
PROMPT-CHATGPT-HELIOS.md                           prompt de reprise

infra/monitoring/prometheus-6vms.yml               scrape 6 VMs + 5 etcd
infra/monitoring/rules-infrastructure.yml          66 règles d'alerte
infra/monitoring/alertmanager-camunda.yml          routage + inhibition
infra/monitoring/promtail-vms-hors-cluster.yml     logs système
infra/remediation-action-catalog.yaml              14 actions, 9 portes
infra/haproxy-controlplane-only.cfg                HAProxy control-plane
infra/09-fix-monitoring-vm.sh                      Alertmanager + Registry
infra/11-install-monitoring-6vms.sh                exporters sur 6 VMs
infra/08-metallb-ingress-pro.sh                    ⛔ ne pas exécuter
infra/10-local-apiserver-lb.sh                     ⛔ neutralisé (obsolète)
infra/10-local-apiserver-lb.OBSOLETE.md            explication
```

## Modifiés

```
backend/backend/infra/k8s/backend-rbac.yaml   🔴 retiré pods: create
infra/haproxy-apps-lb.cfg                     marqué OBSOLETE
infra/metallb-pool.yaml                       commentaires
infra/helm/helios/values.yaml                 frontend → ClusterIP
infra/security.md                             section TLS
infra/ha-upgrade/README.md                    architecture + phases
```

## Fichier de référence sur l'état réel

> ⭐ **`infra/README.md` est la source de vérité** de ce qui est réellement
> déployé. Le consulter avant toute affirmation sur l'infrastructure.

---

# PARTIE 10 — Angle soutenance

Les « échecs » mesurés sont le meilleur matériau du rapport.

| Affirmation défendable | Pourquoi c'est fort |
|---|---|
| « J'ai mesuré F1 = 0,974 en validation et **0,00 en réel**. J'ai identifié la cause : fuite de label et données synthétiques. » | Rigueur expérimentale |
| « J'ai fait une ablation base × fine-tune × RAG × CRAG. Le fine-tuning régressait. J'ai choisi le modèle de base + RAG **sur preuve**. » | Décision fondée sur la mesure |
| « Mon déploiement est gaté : un modèle ne remplace le précédent que s'il marque > 0,93. » | Pratique industrielle |
| « Le LLM ne peut pas générer de commande : décodage contraint au niveau du token. » | Sécurité par conception |
| « 30 des 34 incidents ont une résolution déterministe — j'utilise une table. L'IA intervient sur les 13 cas ambigus. » | ⭐ Maturité d'architecte |
| « etcd à 5 membres : les VMs de plateforme servent de témoins de quorum, la tolérance passe de 1 à 2 pannes. » | Originalité technique |

## À retirer du rapport

🔴 Le `"accuracy": 1.0` de `model_metrics.json` — il vient d'une fuite de
label et sera repéré par un jury technique.

---

# PARTIE 11 — Démonstration recommandée

| Étape | Contenu | Durée |
|---|---|---|
| 1 | Architecture 6 VMs, HA, etcd 5 membres | 3 min |
| 2 | Chaîne Prometheus → Alertmanager → Camunda | 3 min |
| 3 | ⭐ `POD_CRASH_LOOP` cause **OOM** → `INCREASE_MEMORY_LIMIT` | 4 min |
| 4 | ⭐ `POD_CRASH_LOOP` cause **CONFIG** → `ROLLBACK_DEPLOYMENT` | 4 min |
| 5 | Matrice de confusion 34×34, F1 macro | 3 min |
| 6 | ⭐ **Table vs IA** : où l'IA gagne, où elle n'apporte rien | 3 min |
| 7 | Tentative d'injection de prompt bloquée | 2 min |
| 8 | Limites et perspectives | 2 min |

> **Les étapes 3 et 4 sont le cœur.** Même incident, même détection, causes
> différentes, **actions différentes**. C'est la preuve visuelle qu'il y a un
> raisonnement et pas une table.

---

*Passation — HELIOS · Orange Tunisie · PFE 2026 · session du 04/08/2026*
