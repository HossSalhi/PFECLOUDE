# Matrice finale VM_DOWN — état au 6 nov. 2026, 19:40

> **MISE À JOUR du 17 nov. 2026 :** sur demande de l'utilisateur, `.153` (k8s-master) devient une 4ᵉ cible AUTO (HA + auto-rallumage) ; la **production** a été mise à jour le 17 nov. 2026 vers 23:00 (watchdog hôte, image `0.2.166-master153`, armement : `.153` AUTO et ARMÉE, `policy_reconcile.py` COHÉRENT) ; **arrêt réel de `.153` fait le 17 nov. 2026 à 23:58 : auto-rallumage VALIDÉ** (un seul `vmrun start`, `RESOLVED` en 2 min 37 s, HELIOS 100 % disponible ; un seul essai, voir `RUNBOOK-MASTER-153.md` §8). Les lignes `.153` ci-dessous décrivent l'état du 6 nov. Voir `RUNBOOK-MASTER-153.md`.

**Condition de fin de la mission atteinte : trois cycles réels prouvés (`.158` `#4888`, `.155` `#4930`, `.162` `#4889`) et toutes les politiques cohérentes** (`python infra/helios-sot/tools/policy_reconcile.py` = **COHÉRENT** à 18:21 puis de nouveau à 19:36, APRÈS le dernier essai). Le dernier cycle, celui de `.155` (incident `#4930`, 6 min 6 s, un seul `vmrun start`, CNPG sain), a été joué avec l'image finale `0.2.164-vm3auto` et l'autorisation écrite nominative de l'utilisateur. Décision de l'utilisateur (≈ 19:11, réserve R1) : `#4888` et `#4889` comptent comme PASS. **Il reste, hors condition de fin** : l'observation de 24 h (à lancer par l'utilisateur ou sur son feu vert), le contrôle fonctionnel du Bridge (côté utilisateur), la cause racine PostgreSQL (aucune archive WAL, risque R3 pour `.158`) et les réserves du §6. Ce document se régénère à la main après chaque étape de `RUNBOOK-VMDOWN-3PLUS1.md` §H.

Légende des preuves : **L** corrigé localement (dépôt, tests verts) · **P** déployé en production · **RO** testé en lecture seule sur le système réel · **S** testé en mode ombre · **R** validé par une panne réelle autorisée.

## 1. Politique et état par couche

| VM | Attendue | Locale (dépôt) | Production (backend / watchdog script / watchdog en direct) | Armement `ACTIVE_VMS` |
|---|---|---|---|---|
| `.155` k8s-worker2 | AUTO (Camunda) | AUTO | AUTO / AUTO / AUTO | armée |
| `.158` k8s-worker1 | AUTO (Camunda) | AUTO | AUTO / AUTO / AUTO | armée |
| `.162` k8s-lb2 | AUTO (Camunda) | AUTO | AUTO / AUTO / AUTO | armée |
| `.160` k8s-monitoring | **DETECT_ONLY** | DETECT_ONLY (YAML, Java, watchdog, pré-vol, activation, front) | **DETECT_ONLY / DETECT_ONLY / DETECT_ONLY ✓** (watchdog `715022649dd0` le 17:48 ; backend `0.2.164-vm3auto` le 18:19:47) | **non armée ✓** (retirée le 6 nov. 17:40) |
| `.153` k8s-master | **DENY_RESTART** | DENY_RESTART | DENY_RESTART / DENY_RESTART / DENY_RESTART | non armée ✓ |

**Preuve de convergence (18:21 puis 19:36, lecture seule)** : `policy_reconcile.py` → « COHÉRENT : toutes les couches appliquent la politique définitive (.155 .158 .162 AUTO ; .160 DETECT_ONLY ; .153 DENY_RESTART) ». Détail : image `pfe-backend:0.2.164-vm3auto`, 2 pods prêts, **empreinte réelle du contenu des pods `623e83dae74d5376…` (304 fichiers) = empreinte attendue**, `imageID` `sha256:b3006e5b…`, marqueurs de code nouveaux présents ; armement `ENABLED=true DRYRUN=false ACTIVE_VMS=k8s-lb2,k8s-worker1,k8s-worker2` ; hôte `:9099` ouvert, `:9999` fermé, tâche `HELIOS-Watchdog` Running (`-HTTP` et `NodeAutoheal` Disabled) ; Bridge sha `85307e71e1f1`, **jeton absent** (aucune capacité de mise sous tension). **Un seul acteur de démarrage : le watchdog `:9099`.**

## 2. Chaîne détection → décision → exécution → vérification (essais réels du 6 nov.)

| VM | Détection | Décision | Exécution | Vérification | Résultat | Preuve |
|---|---|---|---|---|---|---|
| `.158` | `NODE_NOT_READY` `#4888` (10:43:00) | `RESTART_VM` action 1386 (10:43:06,7), Camunda | UN `vmrun start` 10:43:11,2 ; filet non intervenu | 2 passes persistées (Ready, kubelet, SSH, etcd, CNPG, backend) PASS 10:45:59 | **RESOLVED en 4 min 29 s** (image `0.2.163`) | `#4888`, action 1386 |
| `.155` | `NODE_NOT_READY` `#4930` (19:27:22,5) | action 1422 (19:27:25,6), Camunda | UN `vmrun start` 19:27:29,45 ; filet non intervenu | 7 relevés effectifs, les 2 derniers consécutifs (19:29:13, 19:30:57) avec 7 critères vrais, PASS `#705` 19:30:57,7 | **RESOLVED en 6 min 6 s** (image `0.2.164`) | `#4930`, action 1422, vérification `#705` |
| `.155` (historique) | `NODE_NOT_READY` `#4929` (13:40:09) | action 1421 (13:40:26,0) | UN `vmrun start` 13:40:30,2 | 18 relevés « non prêt » (réplique PostgreSQL absente) ⇒ FAIL 13:46:52 ; refermé SEUL le 19:16:51 après réparation de la base (`#704` PASS) | RESOLUTION_FAILED puis RESOLVED hors cycle (aucun faux RESOLVED entre-temps) | `#4929`, `#703` FAIL, `#704` PASS |
| `.162` | `VM_DOWN` `#4889` (12:38:30) | action 1387 (12:38:30,8), Camunda | UN `vmrun start` 12:38:35,4 | 8 passes, 2 dernières consécutives, 10 critères (SSH, HAProxy, API, 443/6443, DNS, etcd) PASS 12:41:04 | **RESOLVED en 3 min 25 s** (image `0.2.163`) | `#4889`, action 1387 |
| `.160` | `DETECT_ONLY` : incident `VM_DOWN` interface seulement + historique « DÉTECTION SEULE » (rappel 15 min) | **aucune** | **aucune** (ni action, ni Camunda, ni watchdog, ni e-mail) | fermeture par 2 passes de preuves quand l'administrateur la rallume | — | 37 tests ; (historique : essai sous l'ancienne politique AUTO, `#4895`, 2 min 15 s, non retenu) |
| `.153` | `NODE_NOT_READY` détecté et notifié (ex. `#4890`, résolu seul en 33 s) | refus `DENY_RESTART` | **0 action `RESTART_VM` vers `k8s-master` depuis le 6 nov. 00:00** (y compris pendant l'essai de 19:24) ; 88 actions HISTORIQUES du 29 oct. au 4 nov. 11:29 (ancienne fonction MASTER-VM-DOWN : 17 COMPLETED, 71 FAILED) | 2 lectures Ready | — | requête SQL, tests |

## 3. Catégories de preuve par VM

| VM | L | P | RO (19:36) | S | R |
|---|---|---|---|---|---|
| `.158` | oui | oui (`0.2.164-vm3auto`) | pré-vol **GO** ; attentions : le leader du backend ET le primaire PostgreSQL sont sur la cible | ≈ 1 h 25 de relevés en mode ombre pur (08:31-09:56) | **oui** — `#4888` (image `0.2.163-vm4auto`), compté PASS par l'utilisateur |
| `.155` | oui | oui | pré-vol **GO** (27/27) avant l'essai ; état post-essai sain | idem | **oui** — `#4930` (image finale `0.2.164-vm3auto`) |
| `.162` | oui | oui | pré-vol **GO** ; HAProxy `.160` sain (7 serveurs UP) | idem | **oui** — `#4889` (image `0.2.163-vm4auto`), compté PASS par l'utilisateur |
| `.160` | oui (DETECT_ONLY) | **oui** : désarmée 17:40, watchdog `DETECT_ONLY` 17:48, backend `DETECT_ONLY` 18:19:47 | `vmrun` réel : DETECT_ONLY, 403 sans appel | ancienne table | sans objet (l'essai de 13:28 date de la politique AUTO abandonnée) |
| `.153` | oui | oui | oui | oui | refus prouvé par l'absence de toute action depuis le 6 nov. (et `DENY_RESTART` dans 5 couches) |

## 4. Rapport de validation finale (format imposé par la mission, §9)

✅ prouvé par un cycle réel · ❌ échec réel · ⚠️ non isolé · N/A sans objet. **Rien n'est validé sur le seul état Kubernetes `Ready`** : pour les workers, la lecture persistée « Ready » est la lecture EFFECTIVE (`VmRecoveryVerifier.effectiveWorkerObservation`) : elle n'est vraie que si `node_ready`, `kubelet_active`, `ssh`, `etcd_self`, `etcd_has_leader`, `cnpg_healthy` ET `backend_available` sont tous vrais ; sinon `PROOFS_FAILED`.

| Critère | `.158` (`#4888`) | `.155` (`#4930`) | `.162` (`#4889`) |
|---|---|---|---|
| Détection réelle | ✅ arrêt réel `vmrun stop hard` 10:41:30 ; watchdog `POWERED_OFF` 10:41:34 ; Kubernetes `Ready=Unknown` 10:42:16 | ✅ arrêt réel 19:24:52,2 ; watchdog `POWERED_OFF` 19:24:54 ; nœud `Unknown` 19:26:56 ; incident 19:27:22,5 | ✅ arrêt réel 12:37:39 ; `POWERED_OFF` 12:37:44 ; « arrêt confirmé par vmrun list (3 observations sur 34 s) et toutes les sondes réseau en échec » |
| Incident persistant | ✅ `#4888` `NODE_NOT_READY` (6 lignes d'historique) | ✅ `#4930` `NODE_NOT_READY` (8 lignes d'historique, 19:27:22,7 → 19:30:58,5) | ✅ `#4889` `VM_DOWN` `infra/k8s-lb2` |
| Politique AUTO | ✅ action `RESTART_VM` `#1386` 10:43:06 (route Camunda) | ✅ action `#1422` 19:27:25,6 ; « VmPowerWatch : k8s-worker2 éteinte (confirmée) — workflow RESTART_VM lancé » | ✅ action `#1387` 12:38:30,8 |
| Une seule commande `vmrun` | ✅ 1 (5 mesures concordantes) | ✅ **1, cinq sources** : watchdog (`VMRUN_START` 4 → 5, dont `k8s-worker2` 1 → 2), base (1 action `RESTART_VM`, 1 tentative), backend (1 seule ligne `watchdog POST /restart/k8s-worker2`, pod leader `gqchc` ; le pod remplaçant `4mnn6` : 0), VMware (1 processus VMX : `VMX_PowerOn` 18:27:27,0Z → `poweredOn` 18:27:29,4Z), filet (« désarmé : fin sans action ») | ✅ 1 (5 mesures) |
| VM réellement RUNNING | ✅ `START_DONE` « CONFIRME_DANS_VMRUN_LIST » 10:43:10,9 | ✅ `START_DONE` « CONFIRME_DANS_VMRUN_LIST » 19:27:29,8 ; décision `STARTED` HTTP 200 (appelant `.158`, `state_before=POWERED_OFF`) ; `vmrun list` = 6 VM | ✅ 12:38:35,7 |
| SSH opérationnel | ✅ critère des deux relevés finaux (10:45:44, 10:45:59) | ✅ `ssh` (TCP 22) vrai aux relevés 6 et 7 (19:29:13, 19:30:57) ; lecture indépendante 19:33 : `ssh kube@.155` OK, `uptime` 6 min | ✅ critère `ssh` vrai aux deux dernières passes |
| Services opérationnels | ✅ kubelet, etcd, CNPG, backend (mêmes deux relevés) | ✅ `kubelet_active`, `etcd_self`, `etcd_has_leader`, `backend_available` vrais aux relevés 6 et 7 ; indépendant 19:33 : `kubelet` et `containerd` actifs, **0 pod anormal** dans le cluster, 19 pods Running sur `.155` (+ 3 Jobs `Completed`), dont le backend `4mnn6` (neuf) et `postgres-8` | ✅ HAProxy 7 serveurs UP, API via HAProxy, 443, 6443, DNS UDP 53, etcd (10 critères) |
| Kubernetes Ready | ✅ `Ready=True` 10:43:39 (+ critères ci-dessus) | ✅ `node_ready` vrai (relevés 6 et 7) ; indépendant : `Ready=True`, `MemoryPressure/DiskPressure/PIDPressure/NetworkUnavailable=False` | ✅ 3 nœuds Ready après l'essai (`.153` NotReady ≈ 10 s, incident `#4890` résolu seul, 0 action sur `.153`) |
| PostgreSQL/CNPG sain | N/A (relevé : streaming, retard 0 o à 10:47) | ✅ `cnpg_healthy` vrai (relevés 6 et 7) ; indépendant 19:34 : « Cluster in healthy state » 2/2, primaire `postgres-7`, `postgres-8` en streaming async, **retard 0 o**, en recovery | N/A (relevé : streaming, retard 0 o à 12:42) |
| Deux vérifications positives | ✅ `#667` PASS : 10:45:44 et 10:45:59 (écart 15,06 s) | ✅ `#705` PASS 19:30:57,7 : relevés 6 et 7 consécutifs (19:29:13 et 19:30:57), après 5 relevés négatifs (`Unknown`, puis `PROOFS_FAILED` ×4) | ✅ `#669` PASS : 12:40:48,7 et 12:41:04,3 (écart 15,66 s) |
| Incident RESOLVED | ✅ 10:45:59,5 — **4 min 29 s** après l'arrêt | ✅ **19:30:58,5 — 6 min 6 s** après l'arrêt ; même incident `#4930` de bout en bout | ✅ 12:41:04,5 — **3 min 25 s** après l'arrêt |
| **Verdict VM_DOWN** | **PASS** (compté PASS par l'utilisateur) | **PASS** (image finale, CNPG sain) | **PASS** (HA-01 respecté, compté PASS par l'utilisateur) |

**Pièces conservées pour chaque preuve** (détail complet, chronologies à la milliseconde et SQL dans `JOURNAL-MISE-EN-PRODUCTION.md`, sections « ESSAI RÉEL » de `.158`, `.162`, `.155` du 13:39 et du 19:24) :

| Pièce | `.158` `#4888` | `.155` `#4930` | `.162` `#4889` |
|---|---|---|---|
| Date et heure | 6 nov. 10:41:30 → 10:45:59 | 6 nov. 19:24:52 → 19:30:58 | 6 nov. 12:37:39 → 12:41:04 |
| Journaux du détecteur | `VmPowerWatch` : « éteinte (confirmée) — workflow RESTART_VM lancé » 10:43:06 | idem 19:27:25,8 (pod leader `gqchc`) | `VmPowerWatch` : incident `VM_DOWN` créé 12:38:30,7 |
| Décision de politique | AUTO, route CAMUNDA, notifications EMAIL_AND_UI | idem | idem |
| Journal du watchdog | `VMRUN_START` rc=0, `START_DONE`, `STARTED` HTTP 200 (appelant `.155`, `state_before=POWERED_OFF`) | `VMRUN_START` rc=0 18:27:29,450Z ; `START_DONE` 18:27:29,798Z (requête `3a20160814aa4dbf`, incident 4930) ; `STARTED` HTTP 200, appelant `192.168.148.158`, `state_before=POWERED_OFF` | idem (appelant `.155`) |
| Nombre exact de `vmrun start` | **1** | **1** (5 sources) | **1** |
| État de la VM | `RUNNING` (confirmé dans `vmrun list`) | idem | idem |
| Résultats SSH et Kubernetes | SSH + kubelet + etcd + CNPG + backend vrais aux 2 relevés finaux ; Ready 10:43:39 | 7 critères vrais aux relevés 6 et 7 ; lecture indépendante SSH/kubelet/containerd/nœud/pods OK | 10 critères vrais (dont `ssh`, `etcd_quorum`) ; nœuds Ready |
| État PostgreSQL | primaire `postgres-5`, réplique `postgres-7` en streaming, retard 0 o (10:47) | 2/2 « healthy », `postgres-8` streaming async retard 0 o, primaire `postgres-7` inchangé (19:34) | streaming, retard 0 o (12:42) |
| Résultat final | RESOLVED (`#667` PASS) | RESOLVED (`#705` PASS) | RESOLVED (`#669` PASS) |

**Réserve R1 — TRANCHÉE par l'utilisateur (≈ 19:11) : `#4888` et `#4889` comptent comme PASS.** Ils datent de l'image `0.2.163-vm4auto` ; la `0.2.164-vm3auto` ne modifie que le traitement de `.160` (16 fichiers listés dans `IMAGE-0.2.164-vm3auto.sha256`) : la chaîne des workers et de `.162` est inchangée. Le cycle de `.155` (`#4930`) a, lui, été joué avec l'image finale.

**Impact applicatif de l'essai de `.155`, mesuré par les sondes HA-01 (fenêtre 18:24:00Z-18:33:59Z, fichier `probe-baseline-p0.jsonl`)** — à connaître, ce n'est pas un critère de la mission : `api_app` 87,7 % disponible (57 échecs sur 465, **plus longue série 78,5 s** à partir de 18:25:28Z, soit +36 s après l'arrêt : erreurs 502 Bad Gateway et délais ; cause probable, non prouvée : un des deux pods backend était sur la VM arrêtée et le trafic échoue vers lui jusqu'à son retrait des points d'accès par Kubernetes) ; `web` 95,3 % (plus longue série 35,9 s) ; `kube_api` 95,9 % (série de 40,3 s à 18:29:18Z, au retour de `.155` ; le leader etcd est passé de `.153` à `.162` entre les pré-vols de 19:23 et de 19:35 — lien probable, non prouvé) ; `lb160_api` 97,8 %, `lb160_web` 96,2 %, `lb162_api` 95,9 %, `lb162_web` 96,8 %. Le leader du backend (sur `.158`) n'a pas bougé ; le pod backend qui était sur `.155` (`kj89h`) a été remplacé par `4mnn6`.

## 5. Pré-vols N3 (`n3_preflight.py`, lecture seule)

| Résultat | `.158` | `.155` | `.162` |
|---|---|---|---|
| **19:17 (avant l'essai de `.155`)** | **GO** | **GO** (27/27), re-vérifié GO à 19:23:38 juste avant l'arrêt | **GO** |
| 18:25 (avant réparation de la base) | NO-GO : 2 critères CNPG | NO-GO : 2 critères CNPG + `#4929` ouvert | NO-GO : 2 critères CNPG |
| Attentions | le **leader du backend ET le primaire PostgreSQL sont sur `.158`** : un arrêt couperait la base ≈ 55 s (bascule CNPG) | aucune sur les rôles (ni leader du backend, ni primaire PostgreSQL, ni leader etcd sur `.155` au départ) | `.153` NotReady ≈ 10 s et clients DNS en cache coupés 13-31 s attendus (HA-01) ; HAProxy `.160` sain |
| Critères communs GO | watchdog `AUTO` prêt, 5 VM `RUNNING`, backend 2/2 sans erreur, 3 nœuds Ready, etcd 5/5, CNPG 2/2 streaming retard 0 o, RAM hôte ≈ 5 Go, secteur, batterie 57 %, disque C: 32,5 Go, filet `vm_revive.ps1`, politique conforme, aucune action `RESTART_VM` récente, sauvegarde la plus récente 70 min (< 120) | idem | idem |

**Conditions HA-01 propres à `.162`, mesurées à ≈ 18:30 (lecture seule), à REMESURER avant un éventuel nouvel essai** : RAM hôte disponible 5 621 Mo (minimum 4 380 Mo sur les 3 derniers points ≥ 2 300 ✓) ; **0 gel d'E/S de l'hôte sur 10 min, 60 min et 3 h** ✓ ; secteur ✓, batterie 57 % ≥ 40 ✓ ; harnais de sondes actif (< 15 s) ✓ ; etcd 5/5 ✓ ; HAProxy `.160` sain ✓. À refaire juste avant un test : sauvegardes `.161` renouvelées et vérifiées, `infra/ha01/ops/p5_162_preflight.py` (écrit le JSON exigé par `p4_vmstop.py`, < 30 min).

## 6. Écarts restants et réserves

1. **Base sans réplique — RÉPARÉE le 6 nov. 19:16 (autorisation spécifique de l'utilisateur).** `rebuild_replica.py --instance postgres-5 --apply --backup-max-age-min 90` : PVC `postgres-5` annoté `detached` et relu, `ownerReference` Cluster retirée et relue, **Pod `postgres-5` supprimé (seul objet supprimé)**, CNPG a créé `postgres-8` sur `.155` : « Cluster in healthy state » 2/2 en 79 s. **Aucun PVC, PV ni sauvegarde supprimé** : PVC `postgres-5` `Bound`/`detached`, PV `pvc-c4e26c9a…` `Retain`/`Bound` intact. Métadonnées sauvegardées : `C:\Users\houss\helios-state\backups\rebuild-replica-20261106-191453.json`. Seuil d'âge des sauvegardes assoupli de 60 à 90 min (point 8). **Cause racine non corrigée** : `spec.backup` vide ⇒ aucune archive WAL ⇒ `pg_rewind` impossible après l'arrêt brutal d'un primaire.
2. **`#4929` refermé SEUL le 6 nov. 19:16:51** (`VmPowerWatch.closeIfRecovered` : deux passes persistées à 22,3 s d'écart, 7 critères vrais, vérification `#704` PASS) ; ce n'était pas une validation de `.155` — c'est `#4930` qui l'est.
3. **R3 — risque propre à `.158` tant qu'elle héberge le primaire PostgreSQL** (déduction du mécanisme observé sur `#4929`, non rejouée) : un nouvel arrêt brutal de `.158` ferait basculer la base vers `.155`, puis l'ancien primaire risque de rester bloqué en `pg_rewind` comme `postgres-5` (même cause racine). Le PASS réel de `#4888` (réplique hébergée sur `.158`) est la preuve retenue ; `.158` n'est pas rejouée. Correctif durable : une vraie archive WAL (`spec.backup.barmanObjectStore` ou plugin barman-cloud), modification du Cluster séparée et à autoriser.
4. **Déploiement du 6 nov. 18:09-18:20** : premier essai (rolling update 18:12:08, 2/2 prêts et sains) annulé à 18:16:37 par un **faux échec du script** (il testait la santé d'anciens pods encore en arrêt) puis retour arrière automatique vers `0.2.163` (18:17:21). Défaut corrigé dans `scripts/helios-ops/build-deploy-backend.sh` (seuls les pods non terminés de la nouvelle image sont contrôlés ; option `SKIP_BUILD=1`), redéploiement réussi à 18:19:47, retour arrière automatique conservé. Mon `TaskStop` du script n'avait pas arrêté le processus enfant (Windows) : cause du retour arrière, sans conséquence durable.
5. **Observation de 24 h non démarrée** : seulement ≈ 1 h 25 de relevés en mode ombre pur ; `shadow_observe.py --armed` (35 tests) est prêt ; à lancer maintenant que le dernier essai réel est fait (un arrêt de VM aurait fait un FAIL légitime), sans arrêt ni démarrage de VM : `python infra/helios-sot/tools/shadow_observe.py --armed --since "<maintenant>" --loop 900 --until "<maintenant + 24 h>"`.
6. **Contrôle fonctionnel du Bridge** (`check_bridge_no_poweron.sh`) à lancer par l'utilisateur ; le Bridge déployé est byte-identique à la version cible (sha `85307e71e1f1`, jeton absent).
7. **PV de la nouvelle instance : politique `Delete` → `Retain`, FAIT le 6 nov. 19:23:21 sur autorisation de l'utilisateur** (`pvc-886ba62b-267f-44ba-b15d-747f16463a77`, postgres-8) : les 3 PV de données (`postgres-5`, `postgres-7`, `postgres-8`) sont désormais en `Retain`, aucune suppression ; retour arrière = remettre `Delete`.
8. **Rotation des sauvegardes** : les volumes A et B contiennent chacun 7 dumps (maximum conservé par le CronJob : `ls -1t | tail -n +8 | xargs -r rm -f`) ⇒ **toute nouvelle sauvegarde supprime automatiquement la plus ancienne** (aujourd'hui `pfe_db-20261103-020004.sql.gz` sur A, `pfe_db-20261103-231446.sql.gz` sur B). D'où la copie hors cluster `C:\Users\houss\helios-state\backups\pfe_db-hostcopy-20261106-181423.sql.gz` (19:14 locale, sha256 `16065ad4d6c53fc5f129641abdc21c981f75ddbf2b9026634f32178cbb1ac77c`) et le seuil assoupli à 90 min pour la reconstruction. Les dumps de 18:07 atteignent 120 min à 20:07 : au-delà, le critère « sauvegarde < 120 min » des pré-vols échouera tant qu'une sauvegarde fraîche (avec sa rotation) n'est pas faite.
9. **Effets de bord constatés pendant l'essai de `.155`** : leader etcd `.153` → `.162` ; API Kubernetes via HAProxy indisponible 40 s vers le retour de `.155` ; `api_app` indisponible jusqu'à 78,5 s de façon continue après l'arrêt (voir §4, causes probables non prouvées). Aucun incident secondaire créé (le seul incident de la fenêtre est `#4930`) ; `.153` n'a pas été NotReady.
10. Reliquats sans effet : clé `REMEDIATION_WATCHDOG_BASE_URL=…:9999` du ConfigMap ; 21 tests de la copie minimale hors dépôt qui cherchent `infra/` (verts dans l'arbre) ; 4 specs Angular `incident-detail` en échec avant ce chantier ; sauvegardes `pg_dump` stockées sur les nœuds du cluster (pas hors cluster) ; dernier relevé de la boucle d'observation (10:56) en FAIL historique (arrêt prévu de l'essai `.158`).

## 7. Sauvegardes PostgreSQL vérifiées (P2 terminée, 6 nov. 2026)

| Dump | Fichier | Taille | SHA-256 | Restauration réelle (PostgreSQL jetable, une transaction, arrêt à la 1re erreur) |
|---|---|---|---|---|
| A (12:30) | `pfe_db-20261106-113036.sql.gz` | 271 272 o | `b89fcd84a18d4079…` | **RESTAURABLE** 17:53 (14,4 s, 16 tables = 11 961 lignes) |
| B (12:30) | `pfe_db-20261106-113034.sql.gz` | 271 279 o | `51c1323967d3a7f5…` | **RESTAURABLE** 18:07 (12,5 s) |
| A fraîche | `pfe_db-20261106-170741.sql.gz` | 276 719 o | `4d620a27fb7f88871883e4f9fe81ff9365b4d61dcfc5adc93e2920203228cbd9` | **RESTAURABLE** 18:07 (5,3 s) ; comptes restaurés = comptes de la base vivante |
| B fraîche | `pfe_db-20261106-170801.sql.gz` | 276 722 o | `0bb1d23474ec991f483fa3535dff70eb395f1896bf3202ea8017896258df2b1c` | **RESTAURABLE** 18:08 (5,3 s) ; idem |
| Copie hors cluster (19:14) | `helios-state\backups\pfe_db-hostcopy-20261106-181423.sql.gz` | 276 737 o | `16065ad4d6c53fc5f129641abdc21c981f75ddbf2b9026634f32178cbb1ac77c` | intégrité seulement (`gzip -t` OK, pied « dump complete », 16 blocs COPY / 12 107 lignes) — pas de restauration jetable |

Les deux sauvegardes fraîches ont été produites **séquentiellement** (`backup_sequence.py`), SHA-256 du transfert identique, archive lue en entier. Rapports : `C:\Users\houss\helios-state\backups\restore-proof-*.json`. Aucune sauvegarde supprimée par ce chantier en dehors de la rotation automatique de 7 du CronJob ; PGDATA de l'ancien primaire intact (jamais touché par les preuves).

## 8. Mise à jour du 11 nov. 2026 — preuves complémentaires et réserves

- **9 démarrages réels supplémentaires** sous la politique finale (7 → 9 nov.), tous consécutifs à des **arrêts provoqués par l'utilisateur** (confirmé) : `#4936` `#4937` `#4976` `#4978` (`.162`), `#4938` (`.155`), `#4977` `#4979` `#5051` `#5861` (`.158`). Chacun : un seul `VMRUN_START`, `state_before = POWERED_OFF`, appelant = pod backend, incident `RESOLVED` ; aucun démarrage superflu, aucune cible `.153`/`.160`. **Total sous la politique finale : 13 démarrages réels** (4 du 6 nov. + 9). Détail : `rapports/RAPPORT-OBSERVATION-24H.md`.
- **Réserve 5 (observation de 24 h)** : l'observation a été menée (6 nov. 21:35 → 7 nov. 21:35) et s'est terminée en **FAIL brut** ; elle est **contaminée** par 8 arrêts provoqués et par des gels d'hôte : **non validée comme preuve de stabilité, à refaire**.
- **Réserve 8 (rotation des sauvegardes)** : **corrigée** le 6 nov. (rotation gardée, listes `.protected`) et **revérifiée** le 11 nov. (volumes A/B : 12 dumps chacun, rien supprimé).
- **Nouvelles réserves** : cooldown de 30 min ⇒ `.162` éteinte 26 min (`#4937`) ; pré-vol N3 sans critère de gel d'hôte le 6 nov. (**corrigé le 11 nov.** : critère bloquant sur 60 min, fail-closed) ; mémoire de l'hôte saturée le 11 nov. (`.158` NotReady, charge 138).
- Matrice des quatre scénarios (VM_DOWN, CPU, RAM, DISK) : **`MATRICE-GLOBALE-4-SCENARIOS.md`**.
