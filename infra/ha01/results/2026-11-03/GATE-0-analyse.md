# GATE-0 — analyse de la Phase 0 (3 nov. 2026)

## 1. Cause de l'instabilité de `.153` (preuves : `baseline-prometheus-P0.txt`, journaux, sondes)
| Constat mesuré | Valeur |
|---|---|
| Transitions Ready de `k8s-master` sur 24 h | **95** (workers : 0) — surtout 09:00–12:00 UTC (19, 13, 11, 8 par heure) |
| Pression CPU maximale (PSI `cpu waiting`) sur 24 h | **0,96 s/s** sur `.153` (workers 0,13–0,21) ; load1 max **90** (workers 4,3 / 14,4) |
| Pression E/S, iowait, RAM | non en cause : iowait ≤ 3 %, PSI io ≤ 3 %, MemAvailable min 671 Mo (aucun OOM noyau sur 24 h) |
| etcd `.153` | 22 changements de leader vus, 309 propositions échouées, fsync WAL p99 max **2,74 s**, commit backend p99 max **8,19 s** (autres membres ≤ 0,88 s) |
| Redémarrages 24 h | apiserver `.153` +36, etcd +2, falco +13, speaker +34, calico-node +13, **opérateur CNPG +25** (7 pods successifs), 14 pods ingress-nginx successifs sur `.153` |
| Dernier épisode de stall `.153` (fsync p99 > 0,1 s) | **11:39 UTC** (≥ 3 h avant les mesures de ce GATE) ; `/readyz` de `.153` : 46 min en échec sur 3 h 18 (30 bascules) jusqu'à ~12:37 UTC |

**Diagnostic** : `.153` (2 vCPU, 2,1 Go) subit des épisodes de **saturation CPU** (pas mémoire, pas disque) qui retardent etcd (fsync/commit) puis l'apiserver
(`/readyz` en échec → bascules Ready). Boucle d'aggravation identifiée : l'opérateur CNPG (épinglé sur `.153`) perd son bail leader
(`Get https://10.96.0.1:443/apis/coordination.k8s.io/v1/namespaces/cnpg-system/leases/db9c8771.cnpg.io?timeout=5s: context deadline exceeded`
→ `leader election lost`, exit 1) → redémarre → rafale CPU (10 reconcilers, caches) → nouvelles lenteurs. Les épisodes coïncident aussi avec les
interventions du chantier précédent sur `.153` (HAProxy 10:21/10:28, `kubelet.conf` 10:36). Le nœud est stable depuis ~12:37 UTC.
Les drapeaux `--leader-lease-duration` (15) et `--leader-renew-deadline` (10) de l'opérateur sont confirmés (`/manager controller --help`) → correctif C7.

## 2. Décisions D1–D8 (défauts du plan, appliqués sous l'autorisation de l'utilisateur du 3 nov. 2026)
| # | Décision | Appliquée |
|---|---|---|
| D1 | `.153` réservé au control-plane (taint `NoSchedule`) — contredit partiellement la demande du 31 juil. « master ET worker » | Oui (C2) |
| D2 | Contrôleur : Traefik v3.7.17 (repli F5) ; NGF écarté (K8s ≥ 1.31/1.32 exigé) ; pas de montée de version K8s | Oui (Phase 3) |
| D3 | DNS actif/passif (`.160` primaire, `.162` secours) ; lignes `hosts` Windows à retirer au profit du NRPT existant | Oui (shadow puis enforce ; `hosts` en Phase 2b) |
| D4 | kubelet `.153` : nom `api.helios.orange.tn` si le test blackhole `.153`→`.160` prouve qu'il reste Ready, sinon IP locale | Selon test (Phase 2b) |
| D5 | Timings etcd | Seulement si la baseline après stabilisation le justifie |
| D6 | Sauvegardes hors VM sur `.161` + CronJob B (PVC `.155`) | Oui (C6) |
| D7 | Phase 2a : taint personnalisé (pas de cordon) ; Phase 4 : agent hôte `:9999` suspendu pendant chaque test (`ops/helios-pause.ps1`), Bridge/Camunda/backend laissés actifs et journalisés | Consigné (`helios-automatismes-P0.txt`) |
| D8 | Arrêt brutal (`vmrun stop hard`), une VM à la fois, **confirmation explicite de l'utilisateur avant chaque extinction** | Oui |

## 3. Note sur la RAM de l'hôte (précondition 3)
Mesure initiale : 0,65–1,4 Go disponibles (< 2 Go requis ; plancher d'abandon 600 Mo franchi à 515 Mo pendant les opérations lourdes des VMs :
import d'image, snapshots). Remède **non destructif** appliqué à 14:47 UTC : réduction du working set des processus utilisateur inactifs
(navigateur, WhatsApp, etc.; `EmptyWorkingSet`, aucune application fermée, aucune donnée perdue) → 2,7 Go disponibles, stable.
Cette valeur dépend de l'activité de l'utilisateur sur le poste : elle est **re-mesurée avant chaque répétition et chaque arrêt de VM**
(abandon si < 600 Mo, relance de VM seulement si ≥ 2 Go).
