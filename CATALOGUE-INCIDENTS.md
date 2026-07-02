# HELIOS — Catalogue des incidents (référence soutenance)

> **Résumé** : HELIOS **prouve 16 scénarios applicatifs bout-en-bout** (injection → détection → IA → remédiation → résolution) **+ les pannes de nœud/VM** (worker1, worker2, master). Le classifieur ML, lui, est **entraîné à reconnaître 52 classes** (dataset synthétique de **1 200 exemples × 416 features**) pour généraliser au-delà du périmètre reproductible en labo.
>
> **16 = périmètre prouvé · 52 = capacité de classification.** Les deux ne sont pas censés être égaux.

---

## 1. Architecture de la boucle (MAPE-K, Camunda central)

```
Monitoring (.160)  →  ALARME BRUTE déterministe (marqueur K8s / métrique CPU)
     └─ backend lève l'alarme SI Camunda est joignable (garde isReachable)
          → Camunda (BPMN incident-detection)  ← ORCHESTRATEUR CENTRAL
               → worker ai-classify APPELLE l'IA (.161)
                    → IA CLASSE + explique + propose (52 classes, ensemble ML+règles+LLM)
                         → Camunda exécute la REMÉDIATION (BPMN incident-remediation)
                              → API K8s / watchdog hôte
                                   → VÉRIFICATION → RESOLVED
```

- **Camunda arrêté ⇒ 0 détection ET 0 remédiation** (prouvé en live : app + worker1 + worker2 + master, 2 juil 2026).
- **L'IA reste le cerveau** : elle classe/explique/propose (apps **et** nœuds depuis 0.2.125).
- Le **TYPE nœud** est classé par l'IA ; `VM_DOWN` déterministe = repli si l'IA est injoignable.

---

## 2. Les 16 incidents applicatifs (PROUVÉS bout-en-bout)

| # | App (podinfo) | Classe IA | Marqueur de détection déterministe | Remédiation | Famille |
|---|---|---|---|---|---|
| 1 | helios-crashloop | POD_CRASH_LOOP | `crash_loop_backoff_detected` | rollout undo | Rollback |
| 2 | helios-oom | MEMORY_STRESS | `oom_killed_detected` (exit 137) | rollout undo | Rollback |
| 3 | helios-http5xx | HIGH_ERROR_RATE | `http_error_storm_detected` | rollout undo | Rollback |
| 4 | helios-latency | LATENCY_DEGRADATION | `http_latency_detected` (p95≥1s) | rollout undo | Rollback |
| 5 | helios-pending | POD_PENDING_INSUFFICIENT_RESOURCES | `pod_pending_unschedulable_detected` | rollout undo | Rollback |
| 6 | helios-init | POD_INIT_CONTAINER_FAILURE | `init_container_failure_detected` | rollout undo | Rollback |
| 7 | helios-config | POD_CONFIG_ERROR | `config_error_detected` | rollout undo | Rollback |
| 8 | helios-unavailable | DEPLOYMENT_UNAVAILABLE | `deployment_unavailable_detected` (readiness KO) | rollout undo | Rollback |
| 9 | helios-imagepull | POD_IMAGE_PULL_BACKOFF | `image_pull_backoff_detected` | rollout undo | Rollback |
| 10 | helios-cpu | CPU_STRESS | CPU via metrics-server (`maxPodCpuCores`≥0.30) | rollout restart | Restart |
| 11 | helios-disk | NODE_DISK_PRESSURE | `disk_pressure_detected` (pod évincé) | delete pods | Restart |
| 12 | helios-hpa | AUTOSCALING | `autoscale_enabled` + CPU | scale +1 | Scale |
| 13 | helios-service | SERVICE_UNAVAILABLE | `service_unreachable_detected` (sonde TCP) | delete networkpolicy | Réseau |
| 14 | helios-network | NETWORK_PARTITION | `network_partition` (netpol ingress+egress) | delete networkpolicy | Réseau |
| 15 | helios-dns | DNS_FAILURE | `dns_blocked` (egress sans :53) | delete networkpolicy | Réseau |
| 16 | helios-db | DATABASE_CONNECTIVITY | `db_blocked` (egress sans :5432) | delete networkpolicy | Réseau |

**5 familles de remédiation** (l'IA choisit la stratégie, rien n'est codé en dur par app) :
Rollback (9) · Restart/Delete-pod (2) · Scale (1) · Suppression NetworkPolicy = cause racine (4) · Restart-VM (nœuds).

---

## 3. Les incidents infra (nœud / VM) — PROUVÉS

| Cible | Classe (IA depuis 0.2.125) | Détection | Remédiation |
|---|---|---|---|
| worker1 | NODE_NOT_READY (repli VM_DOWN) | condition Ready + lease `kube-node-lease` figé | RESTART_VM via Camunda → watchdog hôte (`vmrun start`) |
| worker2 | NODE_NOT_READY | idem | idem |
| master | NODE_NOT_READY | sonde directe .153:6443 (watchdog) | escalade + **rallumage MANUEL** (pas d'auto) |

HA prouvée : 3 nœuds control-plane (quorum etcd 2/3), VIP `192.168.148.150`, CNPG failover primaire↔réplique.

---

## 4. Catalogue complet des 52 classes du modèle

Légende : ✅ démontré bout-en-bout · 🟢 ajout sûr possible · 🟡 faisable = dev backend · 🟠 besoin d'infra absente · 🔴 risqué/non reproductible sur 3 VMs · ⚙️ méta.

| Domaine | Classe | Statut |
|---|---|---|
| Méta | NO_INCIDENT / UNKNOWN_ANOMALY | ⚙️ |
| POD | POD_CRASH_LOOP · POD_OOM_KILLED · POD_IMAGE_PULL_BACKOFF · POD_PENDING_INSUFFICIENT_RESOURCES · POD_INIT_CONTAINER_FAILURE · POD_READINESS_FAILURE | ✅ |
| POD | POD_LIVENESS_FAILURE · POD_STUCK_TERMINATING | 🟢 |
| NODE | NODE_NOT_READY | ✅ |
| NODE | NODE_KUBELET_DOWN | 🟢 |
| NODE | NODE_HIGH_CPU · NODE_HIGH_MEMORY · NODE_DISK_FULL · NODE_DISK_IO_SATURATION · NODE_CLOCK_SKEW · NODE_HARDWARE_FAILURE | 🔴 |
| DB | DB_CONNECTION_REFUSED (≈DATABASE_CONNECTIVITY) | ✅ |
| DB | DB_CONNECTION_POOL_EXHAUSTION | 🟢 |
| DB | DB_SLOW_QUERIES | 🟡 |
| DB | DB_DEADLOCK · DB_REPLICATION_LAG · DB_HIGH_WRITE_LOAD · DB_DISK_FULL | 🔴 |
| RÉSEAU | NETWORK_DNS_FAILURE · NETWORK_PARTITION | ✅ |
| RÉSEAU | NETWORK_PACKET_LOSS · NETWORK_LATENCY_BETWEEN_NODES · SERVICE_DISCOVERY_FAILURE | 🟡 |
| RÉSEAU | INGRESS_BACKEND_DOWN | 🟠 |
| SÉCURITÉ | AUTH_TOKEN_EXPIRED · AUTH_BRUTE_FORCE_ATTEMPT · UNAUTHORIZED_API_ACCESS_SPIKE · TLS_CERTIFICATE_EXPIRY · SECRET_ROTATION_FAILURE | 🟠 |
| STOCKAGE | PVC_PENDING | 🟡 |
| STOCKAGE | PVC_USAGE_HIGH · PV_FILESYSTEM_CORRUPTION | 🔴 |
| PERF APP | ERROR_RATE_5XX_SPIKE (≈#3) · HIGH_LATENCY_P95 (≈#4) · CPU_SATURATION (≈#10) | ✅ |
| PERF APP | ERROR_RATE_4XX_SPIKE · HIGH_LATENCY_P99 | 🟢 (doublons) |
| PERF APP | MEMORY_LEAK · REQUEST_RATE_DROP | 🟡 |
| PERF APP | CIRCUIT_BREAKER_OPEN · THREAD_POOL_EXHAUSTION · EXTERNAL_API_DEPENDENCY_DOWN | 🟡 |
| OBSERV. | METRICS_SCRAPE_FAILURE · PROMETHEUS_OUT_OF_MEMORY · LOKI_INGESTION_LAG | 🟠 |

**Bilan** : 16 (+nœuds) ✅ prouvés · ~6 🟢 ajoutables sans risque (dont 2 doublons) · ~9 🟡 = dev · ~9 🟠 = infra absente · ~11 🔴 = risqué/non reproductible.

---

## 5. L'argument de soutenance

> « Je démontre **16 scénarios applicatifs + les pannes de nœud/VM de bout en bout** — chacun avec un **marqueur de détection déterministe**, une **classification par l'IA**, une **remédiation automatique** et une **résolution vérifiée**, le tout **orchestré par Camunda** (dont l'arrêt suspend toute détection ET remédiation).
> Mon modèle ML est **entraîné à reconnaître 52 classes** pour **généraliser** au-delà de ce que je peux provoquer en laboratoire. Les classes non démontrées relèvent soit de **variantes** (P99 vs P95), soit d'une **infra hors périmètre** (auth, TLS), soit de pannes **non reproductibles sans risque** sur 3 VMs. »
