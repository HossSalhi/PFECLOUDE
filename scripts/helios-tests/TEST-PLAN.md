# HELIOS Resilience Test Plan

## SLOs globaux (applicables à tous les tests)

| Métrique | Seuil de succès |
|---|---|
| Availability | ≥ 99% sur la durée totale du test (incident inclus) |
| MTTR (Mean Time To Remediate) | ≤ 5 min (détection→RESOLVED) |
| Error rate pendant panne | < 5% (pod redémarre, trafic redirigé) |
| Faux positifs HELIOS | 0 (aucune alerte sans panne réelle) |
| Faux négatifs HELIOS | 0 (chaque panne réelle génère un incident) |

---

## Tableau des scénarios

| # | Scénario | Durée injectée | Signal Prometheus clé | Type incident HELIOS | Action attendue | Critère de succès |
|---|---|---|---|---|---|---|
| 1 | Memory exhaustion | 5 min | `container_oom_events_total` > 0 | `POD_CRASH_LOOP` | `RESTART_POD` | Pod redémarré, Available=2 dans ≤ 3 min |
| 2 | CPU stress | 5 min | `container_cpu_usage_seconds_total` > 90% | `HIGH_CPU_USAGE` | `SCALE_DEPLOYMENT` | Répliques passent 2→4, p95 latency redescend |
| 3a | HTTP latency | 5 min | `http_request_duration_seconds{p95}` > 2s | `HIGH_RESPONSE_TIME` | `RESTART_POD` | p95 < 500ms dans ≤ 5 min après action |
| 3b | HTTP 503 | 5 min | `http_requests_total{status="503"}` > 10% | `DEPLOYMENT_UNAVAILABLE` | `RESTART_POD` | Error rate < 1% dans ≤ 5 min |
| 4 | DB outage | 3 min | `hikaricp_connections_active` drops to 0 | `DB_CONNECTION_FAILURE` | CNPG failover + `RESTART_POD` | DB primary bascule, 0 downtime observable |
| 5 | Disk pressure | 5 min | `kubelet_evictions_total` > 0 | `STORAGE_PRESSURE` | `RESTART_POD` (autre nœud) | Pod réschedulé sur nœud sain |
| 6a | Packet loss 30% | 5 min | `probe_success` (blackbox) = 0 | `HIGH_ERROR_RATE` | `RESTART_POD` | Pod redémarré, packet loss supprimé |
| 6b | Network partition | 4 min | `kube_pod_status_ready` = 0 | `NODE_NOT_READY` | `CORDON_NODE` + reschedule | Pod migré sur nœud actif ≤ 4 min |
| 6c | DNS failure | 5 min | `coredns_dns_responses_total{rcode="NXDOMAIN"}` spike | `SERVICE_UNAVAILABLE` | `RESTART_POD` | DNS restauré après redémarrage pod |
| 7 | Pod kill | 10 min | `kube_deployment_status_replicas_available` < 2 | `DEPLOYMENT_UNAVAILABLE` | `RESTART_POD` | Available=2 dans ≤ 2 min (PDB empêche 0/2) |
| 8 | Pod eviction | 5 min | `kube_pod_status_reason{reason="Evicted"}` > 0 | `DEPLOYMENT_UNAVAILABLE` | `RESTART_POD` | Pod réschedulé ≤ 3 min |
| 9 | Svc-to-svc failure | 5 min | `http_client_requests_seconds{p95}` > 1.5s | `HIGH_RESPONSE_TIME` | `RESTART_POD` | Latence backend → AI service rétablie |
| 10 | Heavy load / HPA | 8 min | `kube_horizontalpodautoscaler_status_current_replicas` | `HIGH_CPU_USAGE` | `SCALE_DEPLOYMENT` | Répliques scale 2→4→6, p95 tient sous 500ms |

---

## Métriques MTTR à collecter pour chaque test

```
t0 = timestamp injection chaos
t1 = timestamp création incident dans HELIOS (détection)
t2 = timestamp début action Camunda (IN_PROGRESS)
t3 = timestamp RESOLVED confirmé

MTTD (detection) = t1 - t0   → cible ≤ 120s (polling interval)
MTTR (remediate) = t3 - t0   → cible ≤ 5 min
Overhead Camunda = t3 - t2   → cible ≤ 2 min
```

---

## Procédure de test standardisée

```bash
# 1. Vérifier état initial propre
kubectl get pods -n helios-tests
kubectl get pods -n pfe
# → tous Running, 0 incident HELIOS actif

# 2. Lancer le monitoring en parallèle (terminal séparé)
python stress_nginx.py --url http://192.168.148.158:31080 \
  --duration 600 --workers 30 --output baseline.csv

# 3. Appliquer le chaos
kubectl apply -f chaos/01-memory-exhaustion.yaml

# 4. Observer HELIOS UI → incident doit apparaître dans ≤ 120s
# → noter t0, t1, t2, t3

# 5. Vérifier action réalisée
kubectl describe deployment nginx-test -n helios-tests
kubectl get events -n helios-tests --sort-by='.lastTimestamp'

# 6. Nettoyage
kubectl delete -f chaos/01-memory-exhaustion.yaml
# Attendre retour à l'état sain, vérifier 0 incident actif

# 7. Répéter pour chaque scénario
```

---

## Résultat attendu scénario Nginx complet (test principal)

```
[t=0]   stress_nginx.py --workers 200 → trafic normal
[t=30s] kubectl apply -f chaos/02-cpu-stress.yaml
[t=60s] CPU nginx > 90% → HELIOS détecte HIGH_CPU_USAGE (confidence > 45%)
[t=90s] Incident créé, Camunda auto-démarre (mode auto-approve)
[t=120s] SCALE_DEPLOYMENT : replicas 2 → 4
[t=150s] verify-remediation : CPU < 60%, p95 latency < 500ms → RESOLVED
[t=180s] stress_nginx.py : availability = 99.2%, p95 = 320ms ✓
```

---

## Matrice remédiation automatique (sans approbation humaine)

| Confidence | Severity | Action | Comporte-ment HELIOS |
|---|---|---|---|
| > 60% | HIGH | RESTART_POD | Auto-exécuté immédiatement |
| > 45% | MEDIUM | SCALE_DEPLOYMENT | Auto-exécuté |
| > 30% | LOW | RESTART_POD | Auto-exécuté avec vérification renforcée |
| < 30% | ANY | — | Incident créé, humain notifié (last resort) |

L'humain intervient uniquement si :
1. Toutes les tentatives de remédiation auto ont échoué (verify-remediation = false 3× de suite)
2. L'action requiert CORDON_NODE + remplacement nœud physique
3. Confidence < 30% sur 3 cycles consécutifs
