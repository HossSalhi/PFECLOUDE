# GATE-0 — résultat : **VALIDÉ le 3 nov. 2026 à 22:40 UTC** (22 préconditions sur 22, évaluation automatique rejouable)

Évaluation : `ops/gate0_check.py` → `GATE-0-mesures.md` (tableau complet ci-dessous). Première évaluation (15:00 UTC) : 21/22, seule la RAM de l'hôte manquait ;
l'utilisateur a libéré la mémoire (Chrome fermé) et une réduction non destructive des working sets inactifs a restauré le tampon : **RAM hôte ≥ 2 668 Mo pendant toute
la fenêtre de 10 min (22:29 → 22:39 UTC), jamais < 600 Mo**. La valeur dépend de l'activité sur le poste : elle est re-mesurée avant chaque étape sensible.

## Ce que montrent les 6 h de mesures supplémentaires (16:30 → 22:30 UTC) — déterminant pour la suite
- Disponibilité réelle de la plateforme : **99,95–99,97 %** (86 échecs sur ~30 000 s/sonde), répartis en 5 incidents de 1 à 11 s à 16:34, 17:00, 17:20, 18:29 et 22:10 UTC.
- **Chaque incident est vu à l'identique par deux points de mesure indépendants** (poste Windows et `.161` avec résolution DNS « comme une VM ») et **sur les deux LB**.
- **Chaque incident coïncide avec un pic de pression CPU de `.153` seul** (PSI 0,69–1,26 s/s) alors que `.155`, `.158`, `.160`, `.162` restent à 0,07–0,34.
  Cause : HAProxy envoyait 1/3 du trafic web et API vers `.153` (et kube-proxy redirige 1/3 des requêtes vers la réplique ingress de `.153`) : quand `.153` sature,
  1/3 des requêtes échouent sur les deux LB en même temps. C'est un défaut RÉEL présent aujourd'hui, indépendant de toute panne de VM.
- Correctifs retenus (Phase 1) : `.153` retiré des backends applicatifs et placé en **secours** pour l'API (C1) ; taint de `.153` et ingress à 2 réplicas sur les workers (C2) ;
  bail leader de l'opérateur CNPG à 60/40 s (C7) ; `admin.conf` de `.153` via le nom (C3, car son watchdog Calico interroge l'API toutes les 5 min).

## Ajouts faits en Phase 0 (tous additifs / réversibles)
| Élément | Détail | Retour arrière |
|---|---|---|
| Sauvegardes hors VM | `.161:/opt/helios-backups/ha01/pre-20261103T141840Z` (18 fichiers, 69 Mo, sha256 vérifiés) ; snapshots etcd `ha01-pre-*` sur les 3 nœuds | rien à défaire |
| Base `ha01` + table `canary` | sur le primaire CNPG, propriétaire `appuser` | `DROP DATABASE ha01` |
| Deployment `pfe/ha01-canary` | épinglé sur `.153`, `helios.io/monitor=false`, écrit 5/s | `kubectl -n pfe delete deploy ha01-canary` |
| Image `postgresql:16` importée dans containerd de `.153` | depuis `/home/kube/cnpg-postgresql-16.tar` | inutile |
| Images préchargées | `traefik:v3.7.17` sur `.155` et `.158` ; `metrics-server:v0.8.1` sur `.158` | `ctr images rm` |
| Harnais de mesure | poste Windows (sondes + échantillonneur, PID dans `harness-baseline-p0.pids`) et `.161` (`ha01-probe-161.service`) | `probes/stop_harness.ps1`, `systemctl stop ha01-probe-161` |
| Base jetable `ha01_verify` | créée puis supprimée pendant le test de restauration | déjà supprimée |
| Réduction des working sets inactifs (hôte) | `EmptyWorkingSet` sur les processus utilisateur inactifs — aucune application fermée par moi, aucune donnée perdue | sans objet |

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

## Mesures automatiques finales (ops/gate0_check.py — rejouable)
| Groupe | Précondition | Résultat | Mesure |
|---|---|---|---|
| 1 | Nœuds Kubernetes Ready 3/3 | ✅ | ['k8s-master', 'k8s-worker1', 'k8s-worker2'] |
| 1 | etcd 5/5 membres sains | ✅ | 5/5 |
| 1 | CNPG sain 2/2 | ✅ | Cluster in healthy state/postgres-7/2 |
| 1 | 0 pod non sain (hors Completed) | ✅ | aucun |
| 1 | HAProxy .160 et .162 : tous serveurs UP | ✅ |  |
| 1 | .153 stable >= 2 h : HAProxy lastchg apiserver >= 7200 s sur les 2 LB | ✅ | {'160': 19096, '162': 19095} |
| 1 | .153 : 0 transition Ready sur 2 h (Prometheus) | ✅ | 0.0 |
| 1 | .153 : etcd et apiserver sans redémarrage depuis 2 h | ✅ | aucun |
| 2 | Jeu de sauvegardes hors VM (.161) : sommes vérifiées | ✅ | 18 OK |
| 2 | Snapshots etcd ha01-pre sur les 3 nœuds (> 20 Mo) | ✅ | 3/3 |
| 2 | Export YAML des objets à modifier (dépôt) | ✅ | results/2026-11-03/pre/ |
| 2 | Configs non secrètes sauvegardées (HAProxy, dnsmasq, manifests) | ✅ | 5 |
| 2 | Test de restauration du dump frais (16 tables, 11 659 lignes identiques) | ✅ | fait le 2026-11-03 (base jetable ha01_verify, supprimée) |
| 3 | RAM hôte disponible >= 2000 Mo sur toute la fenêtre de 10 min (>= 3 mesures) | ✅ | min=2668 max=2901 n=52 |
| 3 | RAM hôte jamais < 600 Mo sur 10 min | ✅ | 2668 |
| 4 | Images préchargées sur .155 (traefik v3.7.17 + metrics-server v0.8.1) | ✅ | 2 |
| 4 | Images préchargées sur .158 (traefik v3.7.17 + metrics-server v0.8.1) | ✅ | 2 |
| 5 | Sondes web/API/LB : fenêtre récente de 30 min sans échec | ✅ | 30.1 min, 7 sondes, 0 échecs (historique complet: 86 échecs sur 30064 s/sonde) |
| 5 | Canari DB : fenêtre récente de 30 min, 0 échec, 0 écriture acquittée perdue | ✅ | 30.2 min, acquittées=8822, échecs=0, perdues=0, trou max=2.83s (historique: échecs=8, perdues=0) |
| 5 | Échantillonneur actif sur la dernière heure (aucun trou > 90 s) | ✅ | n=331, trou max=40s |
| 6 | Automatismes HELIOS inventoriés (agent :9999, watchdog :9099, Bridge, backend) + D7 consigné | ✅ | helios-automatismes-P0.txt |

**Verdict automatique : VALIDÉ**

## Historique de l'attente de la RAM (première évaluation 15:00 UTC → validation 22:40 UTC)
## Mise à jour 16:30 UTC — attente de la RAM (l'utilisateur libère la mémoire lui-même)
- **Observateur de RAM arrêté par Claude Code** (protection « système en forte pénurie de mémoire pendant l'inactivité de la session ») ; **non relancé**, conformément
  à la consigne du système, tant que l'utilisateur ne le demande pas. Les sondes et l'échantillonneur (processus Windows indépendants) continuent de tourner.
- RAM hôte disponible depuis 15:00 UTC (échantillonneur, par quart d'heure) : médianes 1,38 → 1,45 → 1,42 → 1,82 → 1,83 → 1,76 Go ; **jamais 10 min de suite ≥ 2 Go**
  (meilleure fenêtre : max 2,1 Go à 15:45). Chrome est passé de ≈ 1,4 à ≈ 1,07 Go. Il manque ≈ 0,3 Go de façon durable.
- Cluster : sain pendant ces 2 h (3 nœuds Ready, PostgreSQL 2/2, 0 pod non sain). Sondes : 99,90–99,97 % sur 2 h 8, **3 micro-incidents simultanés sur TOUTES les sondes et les deux LB**
  (15:00:07, 15:32:29, 16:00:51 UTC ; poignées de main TLS expirées côté client + `/readyz` d'un apiserver en HTTP 500) : artefact côté poste/hôte (il touche `.160` ET `.162`
  au même instant) combiné à des pics de pression CPU de `.153` (PSI 0,98 / 0,67 / 0,51). Ces pics confirment le besoin de C2 (taint) et C7 (bail de l'opérateur CNPG).
- **Leçon de mesure** : une sonde unique sur le poste Windows peut être faussée par la charge de l'hôte. Ajout d'un **2e point de vue sur `.161`**
  (`ha01-probe-161.service`, résolution DNS « comme une VM » via dnsmasq `.160` puis `.162`, sortie `.161:/opt/helios-backups/ha01/probe161-p0.jsonl`) : seules les défaillances
  observées des deux côtés seront comptées comme pannes réelles pendant les tests.
