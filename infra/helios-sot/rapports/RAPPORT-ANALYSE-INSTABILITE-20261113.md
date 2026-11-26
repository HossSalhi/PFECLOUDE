# Analyse de l'instabilité du 13 nov. 2026 — soft lockups de `.161`, NotReady répétés de `.158`, incident #12315 / workflow #1433

> **Statut : analyse en LECTURE SEULE, rédigée le 13 nov. 2026 (≈ 11:15 heure locale, UTC+1).**
>
> Décision de l'utilisateur (≈ 10:40) : **« PAS DE GO. Reporter la réduction de RAM de `.161`. Maintenir `memsize` à 4096 Mo.
> Ne pas arrêter de VM, ne modifier aucun VMX et ne changer aucune configuration GRUB. »** Elle demandait 5 tâches avant de reconsidérer
> l'opération : (1) soft lockups de `.161`, (2) NotReady répétés de `.158`, (3) incident #12315 et workflow #1433, (4) procédure séparée
> de redémarrage pour le noyau 6.8.0-149, (5) vérification de stabilité après ce redémarrage.
>
> **Ce rapport ne change rien.** Aucune VM arrêtée ni redémarrée, aucun VMX ni GRUB modifié, aucun service touché, aucun commit/push.
> Seules écritures : ce rapport, `infra/helios-sot/RUNBOOK-REDEMARRAGE-NOYAU-161.md` (procédure préparée, **non exécutée**) et le dossier
> `C:\Users\houss\helios-state\kernel149-prep-20261113\` (preuves brutes, ligne de base de `.161`, outils en lecture seule ; fichiers
> nouveaux, empreintes dans `SHA256SUMS.txt`). Les tâches 1 à 3 sont terminées ; la tâche 4 est préparée ; la tâche 5 est **définie mais
> ne peut pas être exécutée** tant que le redémarrage n'a pas eu lieu (il exige un GO explicite).

## 1. Synthèse

| # | Question | Réponse | Confiance |
|---|---|---|---|
| 1 | Soft lockups de `.161` | **Un seul épisode** depuis son démarrage (13 nov. 03:38:40 UTC = 04:38:40 locale). Ce n'est **ni un défaut de `.161`, ni du noyau 6.8.0-147** : c'est un **gel de l'hôte** d'environ 2 minutes qui a figé 5 VM en même temps et s'est terminé à la **même seconde** pour toutes. Les mêmes soft lockups / RCU stalls figurent dans les journaux de `.153`, `.155`, `.158` (noyau 6.8.0-107). Un premier gel, plus long (4 à 5 min), avait touché `.153`, `.155`, `.158` à 04:02–04:07. | Élevée (journaux des invités et journaux VMware concordants) |
| 2 | NotReady répétés de `.158` | 4 NotReady cette nuit, deux causes : **(a)** les deux gels globaux (#12313 à 04:08, #12317 à 04:39) ; **(b)** des **stalls propres à `.158`** (#12179 à 02:21, #12503 à 07:22). `.158` est la seule VM dont la mémoire est à moitié paginée par Windows (**46 % résidente**, 1 415 Mo sur 3 072) : 79 épisodes « injoignable ou lent » depuis le 11 nov., contre au plus 6 pour chacune des autres VM. | Élevée pour (a), moyenne pour (b) |
| 3 | #12315 / workflow #1433 | **Faux positif engendré par le gel n°1**, suivi d'un échec **attendu** de la remédiation : la règle `NODE_HIGH_CPU` a vu 3 minutes de rattrapage à 100 % de CPU après la reprise de `.158` (les échantillons manquants pendant le gel n'ont pas compté dans la fenêtre de 5 min), puis la seule remédiation connue (plafonner `helios-cpuload.service`) a échoué à l'étape `verification_cible` parce que ce service est **inactif**. Aucune défaillance de Camunda. | Élevée |
| 4 | Cause du gel de l'hôte | **Non identifiée.** Facteurs aggravants mesurés : 11 vCPU pour 8 threads, RAM hôte à 1,3–1,7 Go disponibles cette nuit, SSD QLC occupé à 94 % (latence maximale cumulée 6,2 s), mémoire des VM largement paginable, Defender sans exclusion. Le journal Windows ne contient aucun événement de stockage, d'alimentation ou de mémoire. | — |
| 5 | Noyau 6.8.0-149 | Son installation n'a **aucun lien** avec les gels (les VM gelées tournent sous -107 comme sous -147). Mais le prochain démarrage de `.161` utilisera -149, et **le menu GRUB est masqué** (timeout 0) : il n'y a pas de filet de sécurité en cas d'échec. Voir le runbook. | Élevée |

**Conséquence pour l'opération reportée** : ni la réduction de RAM de `.161` ni le redémarrage pour le noyau ne traitent la cause (gel de l'hôte). Faire l'un ou l'autre
pendant une période où l'hôte gèle expose à un gel pendant l'arrêt/le démarrage (2 relances de VM sur 5 avaient été suivies d'un gel le 4 nov., cf. note `project_helios_host_ssd_stalls`).
Critères proposés avant de reconsidérer : voir §7.

## 2. Sources et méthode (tout en lecture seule)

* **Invités** : `journalctl` (noyau, systemd, kubelet, etcd) sur les 6 VM par SSH ; `sar`, PSI, `/proc/vmstat` ; ligne de base de `.161`.
* **VMware** : `vmware.log` des 6 VM (heartbeat des VMware Tools, « GuestInfo collection interval longer than expected », durée des commandes disque `scsiX:Y: Command READ/WRITE took N seconds`). Ces lignes sont écrites par le processus `vmware-vmx` **côté hôte** : elles mesurent directement les gels et la latence du stockage de l'hôte.
* **Prometheus (`.160`)** : cibles injoignables (`up == 0`), scrapes lents, état Ready des nœuds (kube-state-metrics), métriques node-exporter et etcd par fenêtre.
* **Base `pfe_db`** : `SELECT` uniquement (incident, incident_action, incident_history, workflow_instance), journaux des pods backend.
* **Code** : `infra/helios-sot/bridge/bridge.py`, `scripts/helios-ops/detecter.candidate.py`.
* **Hôte Windows** : journaux d'événements (System, Defender), tâches planifiées, compteurs du SSD, mémoire résidente de chaque `vmware-vmx`, préférences VMware (sans WMI lourd, sans rien modifier).
* Preuves brutes : `C:\Users\houss\helios-state\kernel149-prep-20261113\preuves\` (fichiers 01 à 15), outils : `…\outils-lecture-seule\`.

## 3. Chronologie de la nuit du 12 au 13 nov. (heure locale = UTC + 1)

| Heure locale | Événement | Incident HELIOS | Preuves |
|---|---|---|---|
| 02:18 → 02:24 | `.158` **seule** : kubelet « housekeeping took too long » jusqu'à 18,8 s, échecs de renouvellement du lease ; NotReady 02:21:30–02:22:00 (kube-state-metrics). Windows : services NVIDIA « terminés de manière inattendue » à 02:19:58 et 02:20:01 (coïncidence, aucun lien établi). | **#12179** NODE_NOT_READY 02:21:33 → RESOLVED 02:24:00 | 01, 02, 06, 07-158 |
| 04:00 | Compaction + troncature du WAL de Prometheus sur `.160` (03:00 UTC) ; disque de `.160` occupé à 100 % pendant la fenêtre. | — | 03, 14 |
| 04:01:14 → 04:01:30 | Sauvegarde hors site sur `.161` (16 s : pg_dump 288 Ko, snapshot etcd 23 Mo pris sur `.158`, 27 fichiers). Terminée **avant** le gel. | — | 09 |
| ≈ 04:02:10 → 04:06:58 | **GEL GLOBAL n°1.** VMware : `.158` 289 s, `.153` 259 s, `.155` 224 s (fin à 04:06:58). Soft lockups : `.153` 174 s, `.155` 129 s, `.158` 246 s. vmx : « GuestInfo actual=224 sec » sur `.155`, `READ(10) took 5.3 s` à 04:06:56 sur `.153` et `.155`. etcd perd son leader à 04:02:42 (`.160` et `.162` : « lost leader »). kube-state-metrics : `.153` NotReady 04:03:30 → 04:07:15, `.158` 04:03:15 → 04:09:15. | **VM_DOWN infra/loki #12312** (04:07:15, 30 s, « Connection reset »), **#12313** NODE_NOT_READY `.158` 04:08:16 → 04:11:02 | 01, 03, 07, 12, 13 |
| 04:09:21 → 04:13:51 | Analyse Microsoft Defender (événements 1000/1001), **après** la fin du gel n°1. | — | 06 |
| 04:11:04 → 04:11:06 | NODE_HIGH_CPU sur `.158` (faux positif, §6) ; workflow #1433. | **#12315** RESOLUTION_FAILED, workflow **#1433** FAILED / ESCALATED | 04, 11 |
| ≈ 04:36:40 → 04:38:42 | **GEL GLOBAL n°2.** VMware : `.161` 125 s, `.153` / `.155` / `.158` 113 s, `.160` 70 s ; **reprise simultanée à 04:38:40–41**. Soft lockups : `.161` (26, 38, 82, 96 s), `.153` (96 s, `alloy`) ; RCU stalls : `.155`, `.158`. etcd : 3 membres sur 5 gelés, quorum perdu de 04:37:00 à la réélection de 04:38:45 ; apiserver injoignable ≈ 105 s. | **#12317** NODE_NOT_READY `.158` 04:39:33 → 04:41:25 | 01, 07, 08, 12, 13 |
| 07:05 → 07:25 | Mises à jour automatiques (`apt-daily-upgrade`) sur `.160` (07:05), `.161` (07:10), `.162` (07:20) : services redémarrés par needrestart. Sur `.161` : `camunda8` et `helios-ssh-bridge` redémarrés à 07:11:10 (libc6). | aucun | 07-160/161/162, 10 |
| 07:07 et 07:21:30 | `.158` **seule** : 15 s puis 105 s ; kubelet housekeeping 12,8 s ; NotReady 07:22:45–07:23:00. | **#12503** NODE_NOT_READY 07:22:44 → 07:24:48 | 01, 02, 07-158 |

## 4. Tâche 1 — soft lockups de `.161`

### 4.1 Faits

* `.161` (noyau 6.8.0-147) a **un seul épisode depuis son démarrage** (11 nov. 16:43 locale, 41 h) : 4 messages à 03:38:40 UTC — CPU#0 « stuck for 26s » puis « 82s » (`kworker/u257:2`), CPU#2 « 96s » (`pool-2-thread-1`), CPU#1 « 38s » (`multipathd`).
* La trace est celle d'un **write-back** (`wb_workfn` → `ext4_writepages` → `folio_mkclean` → `native_flush_tlb_multi` → `smp_call_function_many_cond`) : le CPU#0 attend l'accusé de réception d'une IPI (invalidation de TLB) d'un **autre vCPU qui n'était pas exécuté**.
* Le journal complet de `.161` est **muet de 03:36:45 à 03:38:40** (≈ 115 s), alors que `helios-sonde-bridge` y écrit toutes les 16 s ; 142 lignes à 03:38:40 (traces) puis reprise normale.
* Dans l'invité, rien ne signale un problème propre : iowait 0,02–0,27 %, défauts de page majeurs ≈ 0, aucun swap, PSI mémoire/E/S négligeables ; la charge n'apparaît qu'**après** (load1 = 17 au tick suivant : rattrapage).
* **Même seconde sur d'autres VM** : `.153` « stuck for 96s » (`alloy`) à 03:38:41 UTC, `.155` et `.158` « rcu_preempt … stall » à 03:38:41 et 03:38:40, `.160` perd le heartbeat VMware Tools de 03:38:10 à 03:38:40.
* **VMware (côté hôte)** : `.161` « Tools heartbeat timeout » à 03:37:20 (dernier heartbeat ≈ 03:37:00), « GuestInfo … actual=125 sec ». Catalogue des gels ≥ 60 s vus par VMware depuis le démarrage des VM (11 nov. 16:42, `.162` : 12 nov. 16:51) : **2 groupes seulement**, tous deux cette nuit (fichier 13) :

| Fin du gel (locale) | VM touchées (durée du gel en s) |
|---|---|
| 04:06:58 | `.153` (259), `.155` (224), `.158` (289) |
| 04:38:42 | `.153` (113), `.155` (113), `.158` (113), `.160` (70), `.161` (125) |

* Les commandes disque lentes (≥ 3 s) vues par vmx sont rares : à la fin du gel n°1 (`.153` 5,4 s, `.155` 5,3 s ×2, READ(10)), et lors du démarrage des VM le 11 nov. (≤ 4,0 s). **Aucune** pendant le gel n°2.

### 4.2 Interprétation

Les soft lockups sont le **symptôme, dans l'invité, d'un gel de l'hôte** : les vCPU des VM n'ont pas été exécutés pendant 70 à 289 s, puis ont repris ensemble ; le noyau a constaté un « trou » d'horloge et l'a rapporté comme un lockup. Ce n'est **pas** un défaut du noyau :
les mêmes messages apparaissent sous 6.8.0-107 (`.153`, `.155`, `.158`) et 6.8.0-147 (`.161`), et l'invité était sain avant et après.

### 4.3 Ce qui est exclu

* Défaut du noyau 6.8.0-147 ou de `.161` (symptômes identiques sur les VM en -107 ; aucune pression mémoire/E/S/CPU dans l'invité).
* Camunda / JVM (journal Camunda sans erreur ; redémarrage de 07:11 expliqué par les mises à jour automatiques, §3).
* La sauvegarde hors site comme cause directe (elle s'est terminée à 04:01:30, 40 s avant le début du premier gel et 35 min avant le second).

### 4.4 Facteurs aggravants mesurés (hôte)

| Facteur | Mesure | Source |
|---|---|---|
| Sursouscription CPU | 11 vCPU (`.161` 3, `.153`/`.155`/`.158` 2, `.160`/`.162` 1) pour **8 processeurs logiques** (Core i5-11320H, 4 cœurs, portable Lenovo 82K1, secteur, plan « Utilisation normale ») | 05, 06 |
| Mémoire de l'hôte | 24 359 Mo ; Σ memsize 16 128 Mo ; Σ working sets des `vmware-vmx` 18 215 Mo ; **1,3 à 1,7 Go disponibles** cette nuit (2,1 Go ce matin) | 05 |
| Mémoire des VM paginable | `prefvmx.minVmMemPct = 25` (jusqu'à 75 % de la RAM d'une VM peut être paginée) ; fichiers `.vmem` nommés (RAM invitée adossée à des fichiers, 6 fichiers = memsize) | 05 |
| Résidence par VM | `.158` **46 %** ; `.153` 114 % ; `.155` 113 % ; `.161` 127 % ; `.160` 153 % ; `.162` 161 % | 05 |
| Stockage | SSD NVMe Intel SSDPEKNW512GZL (QLC, 512 Go), `C:` **94 % occupé** (30 Go libres), santé « Healthy », 60 °C ; latence maximale cumulée : lecture 6,2 s, écriture 6,2 s, flush 0,9 s | 06 |
| Antivirus | Microsoft Defender actif, **aucune exclusion** (ni le dossier des VM, ni `vmware-vmx.exe`) ; analyse 04:09–04:13 (après le gel n°1) | 06 |
| Journal Windows | Aucun événement de stockage (Disk, stornvme, Ntfs), d'alimentation (Kernel-Power), de veille/reprise ou d'épuisement de ressources. Seuls événements d'erreur : services NVIDIA arrêtés (12/11 15:21, 23:42 ; 13/11 02:19–02:20), `hcmon` « USBPcap » (02:34, 03:29), DCOM 10016 | 06 |

### 4.5 Hypothèses sur le déclencheur (aucune prouvée)

1. **Saturation mémoire + stockage de l'hôte** (pagination des `.vmem` sur un SSD QLC presque plein) : compatible avec `.158` à 46 %, avec la lecture de 5,4 s à la fin du gel n°1 et avec l'I/O à 100 % de `.160` (compaction de 04:00) ; **mais** le gel n°2 n'a laissé aucune trace d'E/S dans les invités ni dans les journaux VMware.
2. **Contention CPU / ordonnancement** (11 vCPU sur 8 threads) : PSI CPU de 0,5 à 1,0 sur les 3 nœuds pendant le gel n°2 ; un gel de 2 à 5 min n'est cependant pas un effet normal de contention.
3. **Antivirus ou pilote de filtre** : non établi (la seule analyse Defender du créneau démarre après le gel n°1).
4. **Gestion d'énergie / thermique / pilote GPU du portable** : les arrêts des services NVIDIA coïncident avec des épisodes de `.158` (15:21, 23:42, 02:19) mais **pas** avec les deux gels globaux.
5. **Créneau de sauvegarde hors site** (03:01 UTC) : un gel global a suivi la sauvegarde le 8 nov. (90–120 s) et le 13 nov. (4–5 min), pas le 9, 10, 11, 12 nov. : coïncidence 2 jours sur 6, aucune causalité.

Ce qui permettrait de trancher (non exécuté, demande un GO) : enregistrer côté hôte, en continu, `Memory\Pages Input/sec`, la latence de `PhysicalDisk`, le temps DPC/interruptions et la mémoire disponible ; et lancer périodiquement `vmware_log_freezes.py` (outil fourni, lecture seule).

## 5. Tâche 2 — NotReady répétés de `.158`

### 5.1 Chiffres (Prometheus, node-exporter, critère « up == 0 ou scrape > 2 s », 8 → 13 nov. 11:00, fichier 02)

| VM | épisodes (5 jours) | par jour (08, 09, 10, 11, 12, 13 nov.) |
|---|---|---|
| `.153` | 67 | 42, 20, 2, 1, –, 2 |
| `.155` | 82 | 62, 16, 1, 1, –, 2 |
| **`.158`** | **208** | **62, 32, 35, 47, 20, 12** |
| `.160` | 291 | 109, 128, 48, 4, –, 2 |
| `.161` | 44 | 27, 13, 1, 1, –, 2 |
| `.162` | 296 | 79, 133, 81, 1, 1, 1 |

Les 8–10 nov. correspondent aux essais HA-01 (arrêts de VM provoqués). **Depuis le 11 nov., `.158` a 79 épisodes ; chacune des autres VM en a au plus 6.** La plupart sont courts (15–120 s) et **propres à `.158`** (les autres VM ne bougent pas).

### 5.2 Les quatre NotReady de la nuit

| Incident | Nature | Preuves dans `.158` |
|---|---|---|
| #12179 (02:21) | stalls de `.158` seule (30 s, 60 s, 15 s à 02:18, 02:21, 02:23) | kubelet « Housekeeping took longer than expected » 18,8 s / 6,7 s / 5,2 s ; « Failed to update lease » ; aucun message noyau |
| #12313 (04:08) | **gel global n°1** | soft lockup 246 s (`kube-scheduler`, `sleep`) ; PLEG « last seen active 5m43s ago » |
| #12317 (04:39) | **gel global n°2** | RCU stall à 03:38:40 UTC ; « Failed to update lease … etcdserver: request timed out » |
| #12503 (07:22) | stall de `.158` seule (105 s), **aucun gel VMware ≥ 60 s** | housekeeping 12,8 s, lease KO 06:21:31 UTC ; coïncide avec la mise à jour automatique de `.162` (07:20) — plausible, **non reproduit le 12 nov.** |

### 5.3 Pourquoi `.158`

* C'est la seule VM dont la mémoire est **à moitié paginée par Windows** (1 415 Mo résidents sur 3 072, soit 46 % ; les autres 113–161 %). Toute activité de l'invité qui touche une page « froide » déclenche une lecture disque côté hôte : le kubelet, containerd ou l'etcd de `.158` s'arrêtent quelques secondes à dizaines de secondes.
* Le kubelet de `.158` parle à l'apiserver **local** (`192.168.148.158:6443`) et à l'etcd local : un stall de plus de ≈ 40 s (`node-monitor-grace-period`) suffit à passer le nœud NotReady. `.158` héberge en plus un pod backend, un membre etcd et un apiserver.
* Dans l'invité, `.158` n'a ni swap ni pression mémoire visible (`SwapTotal 0`, MemAvailable 946 Mo) : **le problème est invisible de l'intérieur**, il se lit dans la mémoire résidente côté hôte et dans les retards du kubelet.
* `.158` tourne sous 6.8.0-107 sans mise à jour en attente : le noyau n'est pas en cause.

### 5.4 Limites

Je n'ai pas de série temporelle de la mémoire résidente de `vmware-vmx` pendant les épisodes (le moniteur hôte du 12 nov. a été arrêté par le système) : la corrélation `.158` ↔ 46 % est une mesure de ce matin (11:08), cohérente avec le relevé du 12 nov. 15:42 (1 341 Mo).

## 6. Tâche 3 — incident #12315 et workflow #1433

### 6.1 Déroulé à la seconde (heure locale, base `pfe_db`, journaux du pod `backend-…-gv9k5`)

| Heure | Événement |
|---|---|
| 04:11:02 | #12313 NODE_NOT_READY `.158` résolu (fin du gel n°1 + 2 min de vérification) |
| 04:11:03.957 | `HostIncidentIngestionService` crée **#12315** « Host-mesure: NODE_HIGH_CPU sur k8s-worker1 » (MEDIUM, source PROMETHEUS, `detecte=NON_COLLECTE`) |
| 04:11:04.222 | `ActionServiceImpl` crée l'action **#1546 `HOST_SERVICE_CPU_QUOTA`** (« quota de service borné via Bridge ») par `HELIOS-DETERMINISTIC` |
| 04:11:04.448 | `HostCpuIncidentRemediationOrchestrator` : « workflow confirmed by Camunda » ; workflow **#1433** (`Incident-Remediation-Workflow`) démarre à 04:11:04.001 |
| 04:11:05.745 | `[EXEC:FAILED] HOST_SERVICE_CPU_QUOTA — plafond non appliqué à l'étape verification_cible` (`failed_reason`, `executor_result`) |
| 04:11:05.843 | « Automated remediation failed … » → incident **RESOLUTION_FAILED** ; workflow FAILED / orchestration_state **ESCALATED** (fin 04:11:05.896) |
| 04:11:05.915 | « HELIOS n'a pas pu résoudre l'incident automatiquement. Raison : … verification_cible » |

Le workflow dure 1,9 s. Camunda était UP (`workflow confirmed by Camunda`). La clé `camunda_process_instance_key` est vide **pour tous** les workflows CPU déterministes, y compris les réussis (#1420 à #1432) : ce n'est pas une anomalie (fichier 15).

### 6.2 Cause de l'échec de la remédiation (code)

`infra/helios-sot/bridge/bridge.py` — `quota_cible_saine()` (ligne 809) : vérifie que l'unité cible existe, est chargée **et `ActiveState == active`**. Les seules cibles autorisées sont `helios-cpuload.service` sur `worker1`, `worker2` et `master` (lignes 337–339 et 370–372). Sur `.158`, `helios-cpuload.service` est **inactif** (aucun processus `helios-cpuload`, vérifié à 11:02) puisqu'aucun test de charge n'était en cours : `quota_appliquer()` retourne `etape = verification_cible`. Il n'existe aucune autre remédiation pour NODE_HIGH_CPU : **la remédiation ne peut réussir que pendant un test de charge**.

### 6.3 Pourquoi l'incident a été créé (faux positif)

Règle (`scripts/helios-ops/detecter.candidate.py`, ligne 332) :
`min_over_time((100 - (avg by(instance)(rate(node_cpu_seconds_total{mode="idle"}[3m])) * 100))[5m:30s]) > 90`.

Mesures Prometheus (fichier 04) pour `.158`, UTC : CPU à 37 % (03:02), 63 % (03:03), **aucun échantillon de 03:04 à 03:07** (VM gelée), puis **100 % de 03:08 à 03:10** (rattrapage après la reprise à 03:07:47), 68 % (03:11). Valeur de la règle à 03:11 : **91 > 90** → incident à 03:11:03 UTC = 04:11:03 locale.
`min_over_time` **ignore les échantillons absents** : la condition « plus de 90 % pendant 5 minutes » a été satisfaite par 3 minutes de rattrapage post-gel. La valeur de la règle est restée ≤ 50 sur `.153` et `.155`, qui n'ont donc pas déclenché. Les deux échecs antérieurs de même signature, #9415 et #9416 (11 nov. 12:52 et 14:32, workflows #1351 et #1352 FAILED / ESCALATED), datent de la période de pression mémoire de l'hôte.

### 6.4 Conclusion et suite

* #12315 / #1433 sont une **conséquence** du gel n°1, pas une panne de CPU, de Camunda ou du Bridge. L'escalade (RESOLUTION_FAILED → ESCALATED) est le comportement **prévu** quand aucune remédiation n'est possible.
* L'incident est le seul de la nuit à ne pas être RESOLVED ; il n'est ni OPEN, ni IN_PROGRESS, ni NEW. Rien à réparer côté `.158` (CPU normal, load1 0,7 à 11:02).
* Pistes (non exécutées, demandent un GO ; le Bridge et le détecteur sont des artefacts déployés, pas des fichiers du dépôt seuls) :
  1. exiger une **couverture d'échantillons** dans la règle (`count_over_time(...[5m:30s]) >= 8`) ou invalider NODE_HIGH_CPU quand ≥ 2 VM ont un trou de scrape simultané ;
  2. ne pas tenter `HOST_SERVICE_CPU_QUOTA` quand `helios-cpuload.service` est inactif : classer directement « cause hôte, escalade » avec un message explicite ;
  3. fusionner les NODE_NOT_READY multi-nœuds survenus ensemble en un seul incident « gel d'hôte » (les incidents #12312, #12313, #12315 relèvent d'un même événement).

## 7. Impact sur l'opération reportée et critères avant de la reconsidérer

* La **réduction de RAM de `.161`** (4096 → 3584 Mo) ne traite aucune cause ci-dessus ; gain attendu sur la mémoire disponible de l'hôte : +200 à +400 Mo (extrapolation de l'étape 1, non mesurée). Elle impose un arrêt/démarrage de VM.
* Le **redémarrage pour le noyau -149** est indépendant (runbook séparé) ; il n'améliore ni n'aggrave les gels.
* **Critères objectifs proposés** (à valider par l'utilisateur) avant toute nouvelle opération qui arrête ou redémarre une VM :
  1. aucun gel VMware ≥ 60 s dans les **48 h** précédentes (`vmware_log_freezes.py`) et aucun épisode multi-VM (`prom_episodes.py`) ;
  2. RAM disponible Windows ≥ 1 500 Mo sur 5 × 10 s (porte existante) **et** pages lues < 5 000/s ;
  3. `.158` résidente ≥ 80 % de son `memsize` (aujourd'hui 46 %) ou, à défaut, 0 épisode propre à `.158` dans les 6 h précédentes ;
  4. créneau hors des moments à risque : compactions Prometheus (heures paires locales ±10 min : 00, 02, 04, 06, 08, 10, 12…), `pg_dump` (03:00 et 03:30), sauvegarde hors site (04:01), mises à jour automatiques (07:00–07:30) ;
  5. utilisateur présent, console VMware ouverte.

## 8. Pistes d'atténuation (aucune exécutée ; chacune demande un GO distinct)

| Réf. | Piste | Effet attendu | Remarque |
|---|---|---|---|
| H1 | Libérer ≥ 100 Go sur `C:` (SSD QLC à 94 %) ou déplacer les VM sur un second disque | Moins de stalls d'E/S | Déjà recommandé le 4 nov. (non appliqué) |
| H2 | Exclure de Defender `C:\Users\houss\Documents\Virtual Machines` et `vmware-vmx.exe` | Supprime un acteur possible de blocages | Réversible, sans arrêt de VM |
| H3 | VMware : « Fit all virtual machine memory into reserved host RAM » ou relever `minVmMemPct` ; essayer `mainMem.useNamedFile = "FALSE"` sur une VM non critique | Moins de pagination des VM (surtout `.158`) | Modification de configuration VMware / VMX, arrêt de VM |
| H4 | Réduire la sursouscription : 11 vCPU pour 8 threads (ex. `.161` 3 → 2) | Moins de contention | Interdit dans la mission du 12–13 nov. |
| H5 | Libérer de la RAM hôte (sessions Claude Code ≈ 1,4–1,8 Go, Chrome) ; reprise éventuelle de la réduction de RAM | Moins de pagination | — |
| H6 | Plan d'alimentation « Performances élevées » ; vérifier thermique et pilote NVIDIA | Écarte les causes d'énergie | Portable |
| C1 | Détecteur : couverture d'échantillons de `NODE_HIGH_CPU` (cf. §6.4) | Supprime le faux positif après gel | Artefact déployé |
| C2 | Corréler les incidents multi-nœuds en « gel d'hôte » (cf. §6.4) | Moins de bruit | Artefact déployé |
| V1 | `.160` / `.161` / `.162` : mettre needrestart en mode liste (`$nrconf{restart} = 'l'`) ou planifier `apt-daily-upgrade` en fenêtre contrôlée | Plus de redémarrage surprise de Camunda/Bridge/containerd à 07:10 | Modifie la configuration des invités |
| O1 | Collecte hôte continue (pagination, latence disque, DPC) + exécution périodique de `vmware_log_freezes.py` | Permet de trancher la cause (§4.5) | Tâche planifiée = GO |

## 9. Limites et points non établis

* **Le déclencheur exact des deux gels globaux n'est pas identifié** ; les hypothèses du §4.5 sont classées, non prouvées.
* Les gels < 60 s ne sont pas dans le catalogue VMware (seuil de l'outil) ; les épisodes courts de `.158` viennent de Prometheus et des journaux du kubelet.
* Pas de série temporelle de la mémoire hôte pour la nuit (la surveillance du 12 nov. n'a pas été relancée).
* `.162` n'a pas de gel dans le catalogue VMware : son journal ne commence que le 12 nov. 16:51 (démarrage) et la VM n'a pas été vue gelée ; ses lignes etcd montrent seulement la perte du leader.
* Le chemin de démarrage de `.161` (BIOS hérité probable : « BIOS 6.00 », `sda1` de 1 Mo, pas de partition EFI) est à **confirmer à la console** ; l'entrée « UEFI Firmware Settings » du menu GRUB le contredit.
* Corrections par rapport à mes messages du matin : le gel de `.161` n'était pas de « 96 s » mais d'environ 125 s vu par VMware ; il n'y avait pas un seul gel mais **deux gels globaux** cette nuit ; l'hypothèse « seules les VM multi-vCPU gèlent » est **abandonnée** (`.160`, 1 vCPU, a aussi perdu son heartbeat 70 s au gel n°2).

## 10. Annexe — fichiers

Dossier `C:\Users\houss\helios-state\kernel149-prep-20261113\` : `baseline-161-avant-redemarrage-20261113.txt` (+ `.sha256`), `baseline-161-complement-20261113.txt` (+ `.sha256`), `essai-a-blanc-verif-…txt` (validation du script de vérification), `SHA256SUMS.txt`.
`preuves\` : 01 cibles injoignables · 02 épisodes sur 5 jours · 03 métriques par fenêtre · 04 CPU et règle NODE_HIGH_CPU · 05 mémoire résidente des vmx · 06 événements Windows, Defender, tâches planifiées, SSD · 07 journaux des 6 VM · 08 soft lockups de `.161` · 09 avant/après le gel et sauvegarde hors site · 10 redémarrage de Camunda à 07:11 · 11 incident #12315 et workflow #1433 · 12 extraits des `vmware.log` · 13 catalogue des gels et latences disque VMware · 14 créneaux quotidiens · 15 clés Camunda des workflows.
`outils-lecture-seule\` : `ram_control.py`, `ctrl_rapide.sh`, `prom_*.py`, `vmware_log_freezes.py`, `host_vm_mem.py`, `host_events.ps1`, `host_perf.ps1`, `remote_*.sh`, `verif_post_redemarrage_161.sh` (tous en lecture seule).
