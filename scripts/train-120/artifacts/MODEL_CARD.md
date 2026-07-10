# Model Card — Classifieur d'incidents Kubernetes (120 types) — HELIOS

## Objectif
Classer un incident Kubernetes parmi **120 types** a partir de signaux de monitoring, pour alimenter
la remediation automatique (Camunda detecte -> ML classe le type -> LLM decide l'action).

## Donnees
- 11 885 exemples (apres nettoyage), **120 classes equilibrees (~100/type)**.
- 100% genere par IA (Cerebras gemma-4-31b), ancre sur des incidents realistes.
- Features : 11 metriques (cpu/mem/restarts/latence/5xx/node/db/logs) + `event_reason` (K8s) + `log_sig` (signature de logs).

## Modele
- **LogisticRegression** (C=10, class_weight=balanced) sur StandardScaler(num) + TF-IDF(log_sig mots) + TF-IDF(event_reason char).
- Bat RandomForest / XGBoost / LightGBM sur ces features TF-IDF creuses.

## Performances (test)
- Accuracy 0.965 | F1 macro 0.9655 | ROC-AUC 0.9998 | CV 5-fold 0.9671 +/- 0.0011.
- 120/120 classes F1>=0.60 ; 104/120 F1>=0.90.

## Limites (honnete)
- Metriques SEULES = F1 0.55 -> c'est `log_sig` qui porte la separation (ablation).
- **F1 realiste en prod ~0.72-0.84** si 25-50% des log_sig sont bruites/absents.
- Dataset SYNTHETIQUE (propre) -> chiffres optimistes vs vrais logs.
- Confusions entre types semantiquement proches (SERVICE_UNAVAILABLE / NETWORK_POLICY_BLOCKING, etc.).

## Deploiement
- CANDIDAT (non live). Prerequis : le backend doit EXTRAIRE `event_reason` (kubectl events .reason)
  et `log_sig` (regex sur logs) a l'inference. Les types "composant mort" (etcd/apiserver) -> capteurs (Falco/Sentinelle).

## Artefacts
`ml_120_final.joblib`, plots EDA/comparaison/confusion/robustesse/learning_curve, REPORT*.txt.
