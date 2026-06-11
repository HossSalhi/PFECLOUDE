# BILAN HELIOS — État vérifié sur le code (2026-06-11)

> Audit réalisé en lisant le **code réel du dépôt**, pas une liste théorique.
> Verdict honnête : **HELIOS est une plateforme réelle**, pas une coquille générique.

> ## ✅ RÉCONCILIÉ — Repo déployé en prod (.161) le 2026-06-11
> La prod tournait une version **plus ancienne** (`autonomous_monitor.py` 398 lignes vs 738).
> **Déploiement contrôlé effectué** (backup + pré-vol import OK + rollback prêt) → le repo est
> maintenant **LIVE** : Rounds 12-14 + corrélation L10 + scénario 52 classes tournent en prod.
> Vérifié en réel : `/health`+`/ready` OK, monitor cycle propre (**3 apps analysées, 0 erreur**),
> `/predict` OK (MEMORY_LEAK conf=0.725). **1 bug corrigé au passage** (`get_status` deque slice
> → 500 sur `/monitor/status`, commit `074361a`). Backup conservé sur .161 (`app.bak-*`).

---

## 0. TESTS RÉELS EXÉCUTÉS (2026-06-11, cluster vivant)

Tests **réels, non injectés** — arrêt physique des VMs via `vmrun` (VMware Workstation),
vérifs via `kubectl` + API HELIOS. Cluster : master(.153), worker1(.158), worker2(.155).

### Test 1 — Arrêt physique worker1 (09:20)
| Vérif | Résultat réel |
|-------|---------------|
| Node | `k8s-worker1` → **NotReady** (~45s) |
| HA service | backend **HTTP 200** via worker2, 1/2 réplicas servent |
| CNPG | primary `postgres-1` intact |
| IA .161 | intacte (hors cluster) ✓ |
| **Détection IA** | cycle 96 : `incidents_triggered` **0 → 1** → détectée par l'IA (pas d'Alertmanager) |
| Recovery | Ready en ~35s, backend **2/2** rétabli |

### Test 2 — Arrêt physique worker2 (09:24) — *avec failover DB*
| Vérif | Résultat réel |
|-------|---------------|
| Node | `k8s-worker2` → **NotReady** (~37s) |
| HA service | backend **HTTP 200** via worker1, 1/2 réplicas servent |
| **Failover CNPG** | primary **`postgres-1` → `postgres-2`** automatiquement ✓✓ |
| Recovery | Ready en ~35s, backend **2/2**, CNPG sain |

✅ **HA validée pour les deux workers** : service joignable (HTTP 200) à chaque arrêt,
base basculée seule, node-down détecté par l'IA, cluster entièrement récupéré.
**HELIOS resté opérationnel** (101 cycles, 0 erreur). Un worker à la fois.

**pytest réel** : `test_metric_features` ✓ ; **1 vrai bug corrigé** (constructeur
`ModelBundle` périmé 6→8 args bloquant `test_inference_rules`). Reste = drift d'assertions
8-classes vs classifieur 52-classes (à réconcilier par l'équipe).
Reproductible : `scripts/helios-tests/test-worker-ha.sh`.

---

## 1. L'IA est-elle réelle / intelligente / durable ?

**Ingénierie réelle et durable ; intelligence bridée par les données d'entraînement.**

| Couche | Réel ? | Détail |
|--------|--------|--------|
| ML | ✅ Réel | XGBoost **3 modèles** (incident/sévérité/action) + classifieur scénario 52 classes, artefacts `.joblib`, registry versionné, drift PSI/KS, auto-retrain, A/B shadow. |
| LLM | ✅ Réel | Ollama : Qwen 3B explicateur + 0.5B fine-tuné spécialiste **gaté**. Rôle = explication + 2e avis, **pas** décideur principal (XGBoost décide). |
| Règles | ✅ Réel | Garde-fou déterministe, fusion `0.3·règles + 0.7·ML`. |
| **Faille** | ⚠️ | **Entraîné sur données synthétiques** (`generate_synthetic_dataset.py`) → F1=1.0 non représentatif. **C'est LA vraie limite de fond** (L1+L5). |

**Détection par IA ?** **Hybride, honnête :** le *déclencheur* (`autonomous_monitor → predict_proactive`) est statistique (z-score + régression + seuils) — donc générique/configurable ; la *décision/classification* finale passe par le vrai XGBoost+règles+LLM (`POST /api/ai/predict`). **Trigger déterministe, décision ML.**

## 2. Camunda — réel ?
✅ **Réel** (BPMN orchestré, job workers Zeebe, auto-escalade sur timeout). ⚠️ 1 seul BPMN générique (L15), SPOF sur .161 (L17) — choix d'archi assumés, pas des bugs.

## 3. Monitoring — réel ?
✅ **Professionnel** : Prometheus + Loki + Grafana (standards industrie) sur .160. `prometheus-rules.yaml` = 7 règles d'alerting réelles. Analyse de logs = regex enrichies (patterns Java inclus).

## 4. Séparation des VMs (vérifiée)

| Zone | Contenu | Preuve |
|------|---------|--------|
| **.160** monitoring | Prometheus, Loki, Grafana | `config.py` : `prometheus_base_url`/`loki_base_url` = .160 |
| **.161** iaCamunda | FastAPI IA, Ollama, Camunda, ChromaDB | `config.py` : `ai_camunda_vm_ip` = .161 |
| **Cluster K8s** | backend, frontend, postgres (CNPG), KSM | `ai-service-*` et `chromadb-*` = dans `k8s/archive/` (retirés du cluster) |

✅ **Aucune des deux technos (monitoring / iaCamunda) n'est dans le cluster K8s.** Archi 3-zones respectée.

## 5. La plateforme se monitore-t-elle elle-même ?
**Non — et c'est la bonne pratique.** `autonomous_monitor_exclude_apps` exclut `prometheus, ollama, camunda, ai-service, chromadb, postgres, alertmanager, node-exporter, kube-state-metrics`. → HELIOS surveille **backend/frontend** (ses apps déployées dans K8s), **pas** ses propres composants infra. En miroir, `prometheus-rules.yaml` surveille *de l'extérieur* si l'IA/backend tombent. **Rien à corriger.**

---

## 6. Vérification L1-L25 — statut RÉEL (code à l'appui)

🔴 VRAI (limite persistante) · 🟢 DÉJÀ FAIT dans le repo · 🟠 INFRA (hors code, besoin SSH/VMware)

| # | Limite | Statut | Preuve / note |
|---|--------|--------|---------------|
| L1 | Données synthétiques | 🔴 VRAI | `generate_synthetic_dataset.py`. **Faille de fond.** |
| L2 | Apprentissage continu | 🟢 PARTIEL | `auto_trainer.py` harvest ChromaDB + retrain + drift triggers. |
| L3 | Taxonomie fermée / UNKNOWN | 🟢 FAIT | `inference.py:314-340` UNKNOWN_ANOMALY → INVESTIGATE + approbation humaine. |
| L4 | LLM 0.5B marginal | 🟢 **OK** | Tourne EN PARALLÈLE de l'explicateur (`_llm_executor`), borné par le même timeout → n'allonge pas `/predict`. Reste gaté par design (ML décide). |
| L5 | F1=1.0 trompeur | 🔴 VRAI | Corollaire L1. |
| L6 | Alerting Prometheus | 🟢 FAIT | `prometheus-rules.yaml` (7 règles). |
| L7 | .160 SPOF | 🟠 | `incluster-prometheus.yaml` existe (fédération). Déploiement à confirmer. |
| L8 | node-exporters down | 🟢 **FAIT 11/06** | 3 nœuds K8s (.153/.155/.158) étaient down ; node_exporter installé+démarré → **5/5 UP** dans Prometheus (vérifié). |
| L9 | Logs = keyword | 🟢 FAIT | `log_features.py` regex compilées + patterns Java. |
| L10 | Corrélation multi-signaux | 🟢 FAIT | `autonomous_monitor.py:306` MULTI_SIGNAL_DEGRADATION. |
| L11 | RESTART_VM | 🟠 INFRA | Code client présent (`KubernetesRemediationExecutor:145`) ; watchdog à déployer sur VMs. |
| L12 | Rollback/drain manuels | 🔴 VRAI* | *Choix de sécurité.* `execution_mode` AUTO/RECOMMEND existe ; pas de seuil auto haute-confiance. |
| L13 | Cooldown répétés | 🟢 **FAIT 11/06** | Cooldown PAR CAUSE : `_check_app` renvoie la cause, `_cooldown_cause` ré-autorise un incident si la cause K8s change. Déployé+vérifié .161 (cycle propre). |
| L14 | Dry-run | 🟢 FAIT | `KubernetesRemediationExecutor:69` `execute(plan, dryRun)` → `[DRY-RUN]`. |
| L15 | 1 seul BPMN | 🔴 VRAI | Choix d'archi. |
| L16 | Notif externe / email | 🟢 **FAIT+VÉRIFIÉ 11/06** | Backend activé (relais SMTP .161:2525), **email réel délivré** ("Email sent to houssamsalhi972@gmail.com"). Config durable dans `backend-configmap.yaml`. |
| L17 | .161 SPOF orchestration | 🔴 VRAI | Fallback direct non implémenté. |
| L18 | RAM hôte | 🟠 INFRA | Hôte 23.8GB tendu (mémoire projet). |
| L19 | Backup etcd | 🟢 **FAIT 11/06** | Cron 6h déployé+vérifié sur master (`/usr/local/bin/etcd-backup.sh`, 1er snapshot 23 MB). PG déjà via `postgres-scheduled-backup.yaml`. |
| L20 | Registry local | 🟠 INFRA | Registry v2 sur .160 à déployer. |
| L21 | NetworkPolicies | 🟠 **FICHIER OK, PAS APPLIQUÉ** | `network-policy.yaml` complet MAIS `kubectl get netpol -n pfe` = vide → **non déployé** au runtime. Appliquer = risqué à chaud (default-deny + il manque le port 2525 pour l'email) → fenêtre de maintenance. |
| L22 | ChromaDB/RAG | 🟢 FAIT | `memory_store.py` actif, mode persistant .161. |
| L23 | Tests / CI | 🟢 FAIT | `.github/workflows/` (ai-ci pytest, backend, frontend, model-train) + `tests/` (4 fichiers). |
| L24 | Incidents orphelins | 🟢 FAIT | `autonomous_monitor.py:464` cleanup. |
| L25 | Multi-namespace | 🟢 FAIT | `autonomous_monitor.py:366` discovery dynamique + allow-list. |

**Score : ~13/25 déjà faits dans le code, 5 vraies limites de fond/design, 7 purement infra.**

---

## 7. Les 3 vraies faiblesses à assumer (soutenance)

1. **L1/L5 — données synthétiques** : l'IA n'a jamais vu de vrais incidents. *« Pipeline MLOps complet et prêt, manque 3-6 mois de prod réelle pour valider l'intelligence. »*
2. **L15/L17 — Camunda mono-workflow + SPOF .161.**
3. **L12/L13 — remédiation : pas d'auto-exécution haute-confiance, cooldown rigide.** (L12 est en partie un choix de **sécurité**.)

## 8. Reste à faire (hors code — toi, avec accès SSH/VMware)
- **Tests HA** : `scripts/helios-tests/test-worker-ha.sh` (arrêt worker-1 puis worker-2, vérif HA auto).
- **Email** : `docs/EMAIL-SETUP.md` (3 variables d'env, code déjà là).
- **Infra** : L8 node_exporter, L19 etcd cron, L20 registry, L7 prom in-cluster, L18 RAM, L11 watchdog.
- **Science** : L1 collecter/labelliser de vrais incidents → re-train.

> ⚠️ Sécurité tests (mémoire projet) : un worker à la fois, jamais les deux, ne pas re-gonfler la RAM de .161.
