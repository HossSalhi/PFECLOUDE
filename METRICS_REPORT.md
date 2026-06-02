# HELIOS AIOps — Rapport de métriques

> Données extraites de la base `pfe_db` (table `incident`, `incident_action`, `incident_history`, `human_task`).
> Période : **2026-04-02 → 2026-06-01** (≈ 2 mois). Requêtes reproductibles : [scripts/metrics.sql](scripts/metrics.sql), [scripts/metrics2.sql](scripts/metrics2.sql).

## 1. Vue d'ensemble du jeu de données
| Indicateur | Valeur |
|---|---|
| Incidents détectés par l'IA | **617** |
| Applications distinctes surveillées | **15** |
| Actions de remédiation enregistrées | **414** (dont 267 exécutables) |
| Tâches humaines (demandes d'approbation) | **245** |
| Période | 2 mois |

## 2. Performance de remédiation (le résultat clé)
Actions **exécutables** uniquement (RESTART_POD, SCALE, ROLLBACK… — hors INVESTIGATION/ESCALATION qui sont consultatives) :

| Mode | Actions | Réussies | **Taux de réussite** |
|---|---|---|---|
| **AUTO** (sans humain) | 91 | 76 | **83,5 %** |
| MANUAL (avec approbation) | 176 | 69 | 39,2 % |

→ **La remédiation automatique réussit dans 83,5 % des cas.** 34 % des actions exécutables se sont déroulées **sans intervention humaine**.

## 3. Délai de résolution (MTTR)
| Population | Médiane | Moyenne |
|---|---|---|
| Incidents **auto-remédiés** (action AUTO réussie) | **187 s (~3,1 min)** | 1 627 s* |
| Tous incidents résolus | **258 s (~4,3 min)** | 11 990 s* |

\* La moyenne est gonflée par une longue traîne d'incidents laissés ouverts puis clôturés tardivement (nettoyage manuel) → **la médiane est l'indicateur fiable**. Demi des incidents sont résolus en **~4 minutes**.

## 4. Auto-résolution & humain dans la boucle
| Indicateur | Valeur |
|---|---|
| Incidents **auto-résolus** (condition disparue / vérification post-remédiation) | 104 |
| Incidents avec intervention humaine | 101 |
| Tâches d'approbation créées (≈ e-mails envoyés) | 245 |

## 5. Qualité de détection
| Indicateur | Valeur |
|---|---|
| **Proxy faux positifs** (auto-clôturé < 3 min, sans remédiation = signal transitoire) | **5,5 %** (34/617) |
| Qualité des labels — **fenêtre récente (3 derniers jours, post-correctifs)** | **100 % typés** (115/115) |
| Qualité des labels — tout l'historique | 44 % typés (le début du dev n'enregistrait pas le type) |

→ Les correctifs de labellisation (#2/#3) sont visibles : **100 % des incidents récents sont correctement typés** contre 44 % sur l'ensemble.

## 6. Répartition des incidents (incidents typés)
| Type | n | % |
|---|---|---|
| VM_DOWN | 135 | 49,6 % |
| POD_PENDING_INSUFFICIENT_RESOURCES | 57 | 21,0 % |
| NODE_HIGH_CPU | 19 | 7,0 % |
| GENERAL_DEGRADATION | 12 | 4,4 % |
| REQUEST_RATE_DROP | 10 | 3,7 % |
| INGRESS_BACKEND_DOWN | 9 | 3,3 % |
| MEMORY_STRESS / MEMORY_LEAK | 11 | 4,0 % |
| autres (PVC_PENDING, DATABASE_CONNECTIVITY…) | — | — |

Sévérité : HIGH 59,8 % · MEDIUM 30,5 % · CRITICAL 9,6 % · LOW 0,2 %.

## 7. Actions de remédiation par type
| Action | Total | Réussies |
|---|---|---|
| RESTART_POD | 213 | 120 |
| SCALE_DEPLOYMENT | 54 | 25 |
| INVESTIGATION (consultative) | 112 | — |
| ESCALATION (consultative) | 22 | — |

## 8. Méthodologie & limites (honnêteté)
- **Le jeu inclut du bruit de développement** : `VM_DOWN` (49,6 %) provient en grande partie des redémarrages de VM et des anciens contrôles infra-health (faux positifs), pas d'incidents applicatifs réels.
- **MTTR** = temps entre l'évènement `NEW` et le premier `RESOLVED` dans `incident_history` ; médiane retenue (moyenne biaisée par la traîne).
- **Faux positifs** : proxy = incident auto-clôturé en < 3 min sans aucune action de remédiation (borne haute estimée). Réduit par les correctifs #2 (mémoire) et #3 (labels).
- Les chiffres « production » les plus représentatifs : **remédiation auto 83,5 %**, **MTTR médian ~3–4 min**, **FP ≈ 5,5 %**.

## 9. Validation chaos (D2) — par type d'incident
Scénarios injectés sur l'application réelle **podinfo** (ou validés au niveau modèle via `feature_overrides`).

| Scénario | Méthode | Résultat HELIOS |
|---|---|---|
| **CPU stress** | charge CPU réelle dans le pod | `CPU_STRESS` → remédiation auto (restart) → **auto-résolu** (e2e, incident #1133) ✅ |
| **Crash loop** | commande conteneur forcée à échouer (CrashLoopBackOff réel) | `POD_CRASH_LOOP` détecté ✅ *(a nécessité le correctif honor_labels ci-dessous)* |
| **Fuite mémoire** | tendance mémoire (deriv) | mémoire **croissante** → `MEMORY_LEAK` ; **plate** → `NO_INCIDENT` (anti-faux-positif #2) ✅ |
| **Nœud non prêt** | `node_not_ready_count=1` | `VM_DOWN` HIGH ✅ |
| **Disque plein** | — | ❌ **non couvert** : aucune métrique disque dans le jeu de features (gap connu) |

### Correctif majeur découvert pendant D2 — étiquetage kube-state-metrics
Les métriques KSM (redémarrages, replicas indisponibles, pods pending) étaient **inexploitables pour le namespace `pfe`** : le scrape Prometheus écrasait les labels `namespace`/`pod` par l'identité du pod KSM (→ tout en `namespace="monitoring"`). Ajout de **`honor_labels: true`** au job `kubernetes-pods` → labels réels préservés → **détection crash-loop / indisponibilité / pending débloquée pour toutes les apps**. (CPU/mémoire via cAdvisor n'étaient pas affectés.)

---
*Régénérer : `cat scripts/metrics.sql scripts/metrics2.sql | ssh kube@192.168.148.153 "kubectl -n pfe exec -i postgres-4 -c postgres -- psql -U postgres pfe_db"`*
