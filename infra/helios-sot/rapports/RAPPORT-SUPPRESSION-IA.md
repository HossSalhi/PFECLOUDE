# Rapport — Retrait de l'IA / ML / LLM / RAG du dépôt (11 nov. 2026)

Mission §9. `[MACHINE : hôte Windows] [MODE : MODIFIANT local]` — **dépôt uniquement**. Aucun déploiement, aucune image construite, aucune VM ni aucun service touché, **aucun commit** (HEAD `bc3265a` inchangé), `Incident.java` et les BPMN **intacts**, aucune migration de base modifiée. Aucun secret n'est reproduit ici.

## 0. Verdict

| Point | Verdict |
|---|---|
| Retrait des artefacts IA du dépôt (dossiers, scripts, manifestes, règles de supervision du dépôt) | **VALIDÉ** : 450 fichiers retirés, chacun **vérifié identique octet par octet** à l'archive de sécurité avant suppression |
| Retrait du code Java d'intégration IA (client, apprentissage, contexte LLM, contrôleurs `/api/ai` et fine-tuning, notifications) et refonte déterministe | **VALIDÉ** (11 nov. 14:43-14:45) : `./mvnw -B -o test-compile` **BUILD SUCCESS** (205 fichiers principaux + 49 de tests, 28 s) ; `./mvnw -B -o test` **432 tests, 0 échec, 0 erreur, 0 ignoré**, BUILD SUCCESS (1 min 12 s) = **exactement le compte attendu** (433 − 11 + 10). Voir §8 pour les conditions et l'effet constaté sur l'hôte |
| Frontend (page `fine-tuning`, SSE, service API, métadonnées VM) | **VALIDÉ pour la compilation** (11 nov. 14:53-14:54) : `ngc -p tsconfig.app.json --noEmit` (types + gabarits AOT) **RC 0** et `tsc -p tsconfig.spec.json --noEmit` **RC 0**. `ng test` (exécution des specs, navigateur) **non joué** ⇒ **NON VALIDÉ — PREUVE MANQUANTE** pour le comportement |
| Tests Python du dépôt | **VALIDÉ** : 29 fichiers unittest, **491 tests, 0 échec** (+ `test_p4_162.py` en mode script, tous « OK ») |
| `/predict` supprimé | **VALIDÉ dans le dépôt** (aucune route, aucun client, aucun service) ; la production exécute encore l'ancienne image `0.2.164-vm3auto` (service IA **déjà désactivé et arrêté** sur `.161`) |
| Déploiement du backend sans IA, retrait côté `.160`/Bridge | **NON FAIT — AUTORISATION REQUISE** (Docker/CI et production hors périmètre de cette phase) |

## 1. Sécurité du retrait
- **Archive** : `C:\Users\houss\helios-state\snapshots\ai-removal-20261106\` (**1 054 fichiers**, manifeste SHA-256 `…MANIFEST.sha256`, `sha256sum -c` = 0 écart) + bilan `ai-removal-bilan.txt` (retirés / modifiés). Elle contient les fichiers **non suivis** par git (jeux de données, corpus, scripts) : c'est leur **seule** copie.
- **Garde avant chaque suppression** : comparaison `cmp` du fichier de travail et de sa copie archivée ; si un seul fichier diffère ou manque, **rien n'est supprimé**. Deux fichiers non archivés ont ainsi été ajoutés à l'archive avant retrait (`scripts/_probe_predict.py`, `scripts/helios_dataset_audit.py`), puis `experiments/` (36 fichiers), `datasets/` (3) et `helios-validation-features-mvp.sh`, que mon inventaire initial avait **omis** et que la recherche de bibliothèques ML a révélés.
- **Écart de conformité à signaler** : l'archive contient des **copies de fichiers de secrets du dépôt** (noms seulement, valeurs non lues) : `backend/backend/infra/host-services/.env`, `…/connector-secrets.txt`, `backend/backend/infra/k8s/backend-secret.yaml`, `backend/backend/infra/vm-config/monitoring-minio.env`, `camunda-direct/bridge/bridge_token.txt`. Elle est dans le même profil utilisateur que le dépôt (mêmes droits), mais **à supprimer ou chiffrer après clôture**, et ces secrets sont à **renouveler** (catégorie D).

## 2. Inventaire « avant » et retraits
| Zone | Avant | Action |
|---|---|---|
| `backend/backend/ai-service/` (service FastAPI, modèles, jeux de données, scripts, tests) | 248 fichiers, 20 Mo | **retiré** |
| `scripts/llm-finetune/` (corpus SFT/DPO, notebooks) | 51 fichiers, 60 Mo | **retiré** |
| `scripts/train-120/` (entraînement ML/LoRA, jeux de données) | 39 fichiers, 11 Mo | **retiré** |
| `experiments/` (XGBoost, LoRA, loaders d'inférence) et `datasets/` | 36 fichiers | **retirés** |
| Scripts IA exclusifs (`_deploy_ai_*`, `_probe_ai_service`, `_probe_predict`, `_train_120`, `ai_*`, `deploy-ai-service-update`, `deploy-aiops-model`, `deploy-finetuned-model`, `deploy_ai_stack_161`, `deploy_helios_pro`, `helios_dataset_audit`, `orchestrator*`, `reconfigure-ai-telemetry`, `setup-tls-ai-service`, `backup-ai-artifacts`, `_deploy_catalog`, `_test_ia_down_camunda_detects`, `helios-ops/deploy-ai-service`, `backend/backend/infra/scripts/deploy-ai-service-vm.py`) | 27 fichiers | **retirés** |
| `camunda-direct/campagne/` (pilote de campagne ML), `detecteur/collecteur_ml_host.py` + `helios-collecteur-ml.service`, `mesure_limites.py`, `mesure_readiness.py` | 21 fichiers | **retirés** |
| Manifestes k8s IA (`ai-service-*.yaml`, `chromadb-*.yaml`, `monitoring/ai-service-servicemonitor.yaml`, README d'archive) | 11 fichiers | **retirés** |
| Java : `AIOpsClientService`, `AIPredictRequest`, `AILearnRequest`, `AIOpsIntegrationException`, `IncidentLearningService`, `IncidentLearning` (+ repository), `RemediationHistoryContextService`, `LlmRetrainNotificationService`, `FineTuningController`, `AIOpsController`, `RestClientConfig` ; tests `AIOpsClientServiceTest`, `AIOpsControllerTest` | 14 fichiers | **retirés** |
| Frontend : `features/fine-tuning/` (composant, gabarit, spec) | 3 fichiers | **retiré** |
| Script racine `helios-validation-features-mvp.sh` | 1 | **retiré** |

## 3. Fichiers MODIFIÉS (41 + quelques-uns hors archive)
- **Java (principal)** : `AIOpsOrchestrationService` (chaîne de classification **déterministe**, plus d'appel `isReachable`/`predict`/`learn`, suppression de `AI_UNAVAILABLE`, de l'extraction du « raisonnement LLM » et de la reclassification autoscale/CPU_STRESS ; **auto-résolution** désormais décidée par les marqueurs K8s restants + charge CPU, **fail-closed** si la lecture échoue), `OrchestrationService` (webhook : pipeline déterministe d'abord, repli sur la charge utile), `RecoveryVerificationService` + `RecoveryVerificationResult` (critère : workload sain ET aucun marqueur de panne K8s ; champ renommé `faultStillPresent`), `CamundaJobWorkerLifecycle` (plus de `learn`), `RemediationProperties`, `InfraHealthProperties`, `SecurityConfig` (règles `/api/ai/**`, `/api/finetuning/**`, `/api/mlops/**` retirées), `GlobalExceptionHandler`, `InfrastructureController`, `NotificationService`, `EmailNotificationService`.
- **Java (tests)** : `RecoveryVerificationServiceTest` (réécrit : 10 tests déterministes), 3 tests de workers Camunda (constructeur).
- **Configuration** : `application.properties` (clés `aiops.service/client/finetuning`, `llm.retrain`, `infra.health.(ai-service|ollama)-url`, seuils de confiance de vérification/auto-rollback retirés ; **`aiops.polling.*` conservées**), `application.properties` de test, `backend-configmap.yaml`, `kustomization.yaml`, `worker-ha-config.yaml`, `prometheus-rules.yaml`, `aiops-external-services.yaml` (il ne reste que Camunda).
- **Supervision (dépôt)** : `infra/monitoring/prometheus-6vms.yml` (job et sonde du service IA), `rules-infrastructure.yml` (alertes `AIServiceDown`, `AIServiceHighLatency`). **Les copies déployées sur `.160` ne sont pas modifiées** (D).
- **Frontend** : routes, module, `api.service.ts`, `sse.service.ts`, `infrastructure.component.ts` (entrées `ai-service`/`ollama`).
- **Scripts/docs** : `deploy-aiops.ps1`, `five-vm-cutover.ps1`, `deploy-images.ps1`, `install-monitoring-vm.sh`, `install-ai-camunda-vm.sh`, `repair-ai-camunda-vm.sh` (partie IA retirée, `bash -n` OK), READMEs, bandeaux d'obsolescence sur `run-auto-remediation-final-test.ps1` (attend `/api/ai/predict`) et `helios-post-recovery-check.sh` (phases 9c-11), note dans `infra/remediation-action-catalog.yaml`.
- **Hors périmètre IA mais corrigé en route** : `scripts/helios-ops/test_build_deploy_backend.py` (le faux `kubectl` ne gérait pas le `go-template` du filtre `new_pods` ajouté le 6 nov. : **4 échecs préexistants**, corrigés ; 5/5 OK).

## 4. Changement de COMPORTEMENT à la prochaine mise en production (à valider par l'utilisateur)
1. **Pannes d'applications Kubernetes** (OOM, crash loop, image, config, réseau, finalizer…) : classées et remédiées par les **marqueurs d'état K8s** et la politique déterministe. **Aujourd'hui en production** (IA arrêtée), elles sont **escaladées** avec le type `AI_UNAVAILABLE` : la nouvelle chaîne restaure la remédiation automatique de ces types (comportement validé avant l'ère de la « garde IA »). Décision à confirmer avant déploiement.
2. Les scénarios **CPU_STRESS / AUTOSCALING par application** (classés par le modèle retiré) ne sont plus classés ; la saturation CPU est traitée au niveau hôte (détecteur + Bridge).
3. **Vérification de récupération** : plus de score de confiance ni de prédiction ; marqueurs K8s uniquement. `verifyWithAutoRollback` (code mort, non appelé) et ses réglages sont retirés.
4. **Webhooks d'alerte** : classification déterministe d'abord ; sinon incident créé depuis la charge utile (source mappée), sans enrichissement IA.
5. **Endpoint `POST /api/ai/predict` supprimé** : le script `run-auto-remediation-final-test.ps1` (campagne d'essais de l'ère IA) ne peut plus aboutir ; bandeau d'obsolescence posé.

## 5. Preuve que `/predict` a disparu
- Dépôt : `grep` de `/api/ai`, `AIOpsClientService`, `AIPredictRequest`, `ollama`, `FineTun`, `/predict` dans le code Java, les propriétés et le frontend : **aucune occurrence** hors commentaires de retrait et résidus listés ci-dessous.
- Production (lecture seule, 11 nov.) : sur `.161`, `pfe-ai-service`, `ollama` et `helios-collecteur-ml` sont `disabled` **et** `inactive` ; aucun journal récent.

## 6. Résidus JUSTIFIÉS (liste blanche)
| Résidu | Pourquoi il reste |
|---|---|
| `AIPredictResponse`, `AIPredictEvidence`, `AIPredictPlanOption`, `AIOpsAnalyzeRequest/Response`, `AIOpsOrchestrationService`, `AIOpsPollingScheduler`, `AiopsPollingProperties` | **noms historiques** de porteurs de diagnostic et du scheduler de DÉTECTION (aucun appel IA) ; renommer = 24 fichiers sans gain fonctionnel |
| `IncidentSource.AI`, acteur `AI-SYSTEM`, seuils `remediation.*-confidence-threshold` | valeurs **stockées en base** / paramètres de politique portés par des constantes déterministes ; les modifier casserait les données et les tests |
| Type de job `ai-classify` | porté par le **BPMN, intouché** ; le worker est déjà déterministe |
| Page `ai-decisions` | **déterministe** (liste des décisions de remédiation + retour opérateur) ; nom historique |
| Table `incident_learning` | **conservée** (aucune migration destructrice) ; seule l'entité Java est retirée |
| `infra/helios-sot/bridge/*`, `baseline/*` (champs `ai_*`) | SoT = **copie du Bridge déployé** ; les retirer exige de redéployer le Bridge |
| `.github/workflows/ai-ci.yml`, `model-train.yml`, `backend/backend/docker-compose.yml` (service `ai-service`) | CI/CD et Docker **hors périmètre** ; ces fichiers sont **cassés** par le retrait (chemins absents) — signalé |
| `.gitignore` (règles ML), fichiers `.bak` d'`application.properties` et de classes Java | protègent contre un commit accidentel de binaires restaurés ; sauvegardes de travail non supprimées |
| Documentation (`docs/*`, `HANDOFF*`, `PROMPT-CHATGPT*`, `AUDIT-CLUSTER-K8S.md`, `HELIOS-presentation.html`, `camunda-direct/README.md` annoté) | historique du projet |

## 7. Ce qui reste à faire (et ne peut pas être fait sans autorisation)
1. **Production** : déployer le backend sans IA (rolling update, autorisation) ; recharger Prometheus et le détecteur de `.160` (alerte `AIServiceDown` toujours présente) ; décider du sort des fichiers IA de `.161` (`/opt/pfe-ai-service`, modèles Ollama) ; redéployer le Bridge sans les champs `ai_*` ; renouveler les secrets présents dans l'archive.
2. Supprimer ou chiffrer l'archive quand le retrait est validé.

## 8. Conditions de la compilation et effet constaté sur l'hôte (honnêteté sur la porte mémoire)
- **Décision de l'utilisateur** (11 nov.) : fermer Chrome puis lancer. Après la fermeture : **1 560 Mo disponibles** (contre 330 Mo avant) et ≈ 400 pages/s relues — la **porte de 2 500 Mo n'était donc PAS atteinte** ; le lancement s'est fait sur décision de l'utilisateur, avec ces garde-fous : heap Maven 768 Mo (`.mvn/jvm.config`), heap des tests 640 Mo (`-DargLine`), priorité `BelowNormal` et **garde mémoire** (`mem_guard.ps1` : arrêt des JVM Maven du dépôt si 2 mesures consécutives < 350 Mo ou > 9 000 pages/s). **Elle n'a jamais eu à arrêter Maven** (1 seule mesure à 9 452 pages/s pendant les tests) ; elle s'est déclenchée ensuite à vide (0 JVM) sur une pagination qui n'était pas la mienne.
- **Effet constaté** : les sondes HA-01 `web`, `api_app`, `lb160_web`, `lb162_web` ont échoué **en continu de 14:42:18 à (au moins) 14:46:30 locale** (≈ 126 échecs). Cette série a **commencé 80 s AVANT** mon premier Maven (14:43:38) : **je ne peux donc pas l'attribuer à la compilation**, mais elle l'a recouverte (et la pagination de l'hôte est montée à 9-30 k pages/s pendant et après). Cause la plus probable : ré-hydratation de la mémoire de `.158` (272 → 1 471 Mo) après la libération de Chrome. **Non prouvé.**
- **Conclusion** : sur cet hôte, ni les builds lourds ni la réhydratation des VM ne tiennent ensemble tant que la mémoire des VM n'est pas réduite (catégorie D).
- **Angular** : script `angular_check.sh` — attend 3 mesures calmes (≥ 1 000 Mo disponibles et < 3 000 pages/s), puis `ngc -p tsconfig.app.json --noEmit` et `tsc -p tsconfig.spec.json --noEmit` (heap node 700 Mo, garde mémoire dédiée). Les 3 mesures calmes sont arrivées à 14:53:58 (1 081 Mo, 173 pages/s) ; **les deux commandes ont rendu RC 0** (≈ 18 s au total) avec 871 Mo disponibles pendant l'exécution ; aucune série d'échecs de sonde n'a été recherchée sur cette fenêtre brève (le cluster était sain : 3 Ready, CNPG 2/2).
