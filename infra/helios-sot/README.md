# infra/helios-sot — source de vérité de la politique VM_DOWN (objectif : rallumage automatique réel des QUATRE VM .158 .155 .162 .153 ; .160 = détection seule)

> **MISE À JOUR du 17 nov. 2026 — consigne de l'utilisateur : `.153` (k8s-master) devient une cible AUTO (HA + auto-rallumage).** Elle INVERSE « `.153` jamais cible » (6 nov.) et est appliquée à toutes les couches du DÉPÔT (YAML, `VmPowerPolicy.java`, `VmPowerWatch`, `VmRecoveryVerifier`, watchdog, outils, tests) ; `.160` reste `DETECT_ONLY`. **Déployé et armé en production le 17 nov. 2026 (≈ 22:54-23:01 : watchdog hôte `3be42a875c68`, image `0.2.166-master153`, `ACTIVE_VMS` = les 4 VM AUTO ; `policy_reconcile.py` COHÉRENT)** ; **arrêt réel de `.153` le 17 nov. 2026 à 23:58 : rallumage automatique en 84 s (un seul `vmrun start`), `RESOLVED` en 2 min 37 s, HELIOS 100 % disponible : auto-rallumage de `.153` VALIDÉ** (un seul essai ; `RUNBOOK-MASTER-153.md` §8). Détail, risques, déploiement, essai réel et retour arrière : **`RUNBOOK-MASTER-153.md`**. Les paragraphes datés du 5-6 nov. ci-dessous décrivent l'ÉTAT D'ALORS (`.153` « jamais une cible »).

Plan approuvé le 2026-11-05, **consigne finale du 2026-11-06 (après-midi)** : seules `.155` `.158` `.162` sont
redémarrées automatiquement après détection et validation de la cible ; **`.160` (VM de supervision) est en DÉTECTION SEULE**
(incident + historique + interface, ni e-mail, ni escalade Camunda, aucun redémarrage : rallumage par l'administrateur) ;
`.153` n'était **jamais une cible** à cette date (elle est **AUTO depuis le 17 nov. 2026** : voir la mise à jour ci-dessus) et reste un APPELANT du watchdog ; aucun `RESOLVED` sans preuve de récupération
(jamais sur un simple ping ou « Powered On »).

> **Bilan à jour : `MATRICE-FINALE-VM_DOWN.md` (matrice par VM, preuves, réserves) ; ordre des opérations de production, risques et retours arrière : `RUNBOOK-VMDOWN-3PLUS1.md` §H ; preuve de convergence : `tools/policy_reconcile.py`.**
>
> **CONSIGNE FINALE DE L'UTILISATEUR du 6 nov. 2026 (après-midi, après l'essai réel de `.155`) — elle PRIME sur tout ce qui suit.**
> « L'auto-rallumage juste pour (`.155`, `.158`, `.162`) mais pas pour `.160`, car cette VM est le responsable de monitoring. »
> C'est la TROISIÈME bascule sur `.160` en une journée (09:50 « pas `.160` » → 11:20 « les quatre VM » → maintenant « pas `.160` »).
> Elle ANNULE celle de ≈ 11:20 et est appliquée PARTOUT : `infra/helios-vm-policy.yaml` (`k8s-monitoring` = `DETECT_ONLY`, route `NONE`,
> notifications `UI_ONLY`), `VmPowerPolicy.java`, bloc `VM_POLICY` du watchdog, `VmPowerWatch` (incident `VM_DOWN` interface seulement + rappel
> d'historique « DÉTECTION SEULE », JAMAIS d'action/Camunda/POST), pré-vol, statut AUTO, script d'activation (refuse `k8s-monitoring`), interface
> Angular, contrat golden et tests (`CoherenceTest` interdit à nouveau tout écart outils/politique). La route `BACKEND_DIRECT` (validée par l'essai
> réel de `.160` du 13:28) est CONSERVÉE mais DORMANTE (couture de test package-private `VmPowerPolicy.overrideEntryForTest`) : la rétablir
> exigerait une décision explicite (une ligne dans les trois tables + armement). **Production : le backend déployé (`0.2.163-vm4auto`) et le
> watchdog de l'hôte (`0f32b027cb5e`) portent encore l'ANCIENNE table (`.160` AUTO) ; seul le verrou `HELIOS_VMPOWER_ACTIVE_VMS` décide de ce qui
> est réellement rallumé** — voir « Désarmement de `.160` » dans `JOURNAL-MISE-EN-PRODUCTION.md`.

> **[ANNULÉE par la consigne finale de l'après-midi ci-dessus] CONSIGNE de l'utilisateur du 6 nov. 2026 (≈ 11:20).** Les **QUATRE VM `.158`, `.155`, `.162` et `.160`** doivent toutes bénéficier du rallumage automatique réel ; seule `.153` reste interdite comme cible. Elle **ANNULE l'exclusion de `.160` décidée à ≈ 09:50** (« mais 160 non… »), qui n'avait été appliquée qu'aux OUTILS (`n3_preflight.py`, `apply_vm4_auto.sh`) — jamais à la politique déployée, où `.160` est restée `AUTO` (route DIRECTE du backend, notifications « interface seulement » : ni e-mail, ni escalade Camunda). C'est cet écart outils/politique qui a produit la contradiction signalée ; trois tests (`CoherenceTest`) interdisent désormais qu'il revienne. **Trois niveaux à ne jamais confondre** (voir `tools/auto_status.py`) : CONFIGURÉE AUTO (politique + watchdog), ARMÉE (`ACTIVE_VMS` + `DRYRUN=false`), VALIDÉE (arrêt réel suivi d'un `RESOLVED`). Au 6 nov. 11:35 : les 4 sont CONFIGURÉES ; seule `.158` est ARMÉE et VALIDÉE.
**RÉSULTATS 6 nov. 2026 — ESSAIS RÉELS (N3) : `.158` RÉUSSI (10:46), `.162` RÉUSSI (12:41), `.160` RÉUSSI (13:28) ; chemin prouvé quatre fois : backend → POST → watchdog → `vmrun start` UNE seule fois, filet non intervenu, VMware corrobore. `.160` : route directe, AUCUN workflow Camunda, AUCUN e-mail la concernant. `.155` (13:39) : RALLUMAGE AUTOMATIQUE PROUVÉ mais vérification de santé NON confirmée (`RESOLUTION_FAILED`) — la réplique PostgreSQL `postgres-5` ne peut pas se réintégrer (pg_rewind) et doit être RE-CLONÉE ; `.155` n'est donc ARMÉE, pas encore VALIDÉE** (arrêt brutal → rallumage automatique → `RESOLVED` en 4 min 29 s, une seule action, une seule décision de démarrage ; détail : `JOURNAL-MISE-EN-PRODUCTION.md`). Le rallumage réel est ACTIF pour `k8s-worker1` seulement ; `.155` et `.162` restent à tester. — État antérieur au 6 nov. 08:45 : tout est DÉPLOYÉ EN MODE OMBRE (watchdog, pare-feu, jeton, backend `0.2.163-vm4auto`, Bridge cible) ; AUCUN rallumage réel n'est activé (`DRYRUN=true`, `ACTIVE_VMS` vide) et aucun arrêt réel n'a été fait. L'activation par VM et chaque essai réel exigent une autorisation écrite nominative.** Journal détaillé : `JOURNAL-MISE-EN-PRODUCTION.md`.

| Dossier / fichier | Rôle | État |
|---|---|---|
| `../helios-vm-policy.yaml` | Table fermée des 5 VM + seuils (source de vérité) : AUTO `.155` `.158` `.162` **et `.153` (depuis le 17 nov. 2026)**, DETECT_ONLY `.160` ; DENY_RESTART conservé (aucune VM) | prêt (production : `.153` non déployée ni armée) |
| `baseline/` | Copies byte-identiques de la PRODUCTION du 5 nov. 2026 + `MANIFEST.sha256` (ignorées par Git : elles contiennent des identifiants hérités) | figé |
| `watchdog/helios-vm-watchdog.py` | Watchdog hôte CIBLE : exécuteur sans autonomie, `GET /vm/<nom>/state`, `POST /restart/<nom>` idempotent | dépôt : `.160` = `DETECT_ONLY` (révision de l'après-midi du 6 nov.) ; **déployé** (version `0f32b027cb5e`, en marche depuis le 6 nov. 01:00) = ANCIENNE table (`.160` AUTO) : **redéploiement à faire (autorisation requise)** |
| `bridge/bridge.py`, `bridge/telemetrie.py` | Bridge CIBLE : plus aucune capacité de power-on, filtre `/notifier` (dérivé de la baseline par `tools/make_target_bridge.py`) | **déployé** sur `.161` le 6 nov. 07:30 |
| `contract/*.json` | Contrat HTTP « golden » du watchdog (34 scénarios ; `.160` réellement en `DETECT_ONLY` : 403 `DETECTION_ONLY`, plus de simulation), rejoué par les tests Python ET Java | prêt (régénéré le 6 nov. après-midi) |
| `host/Install-HeliosWatchdog.ps1` + `host/tache-HELIOS-Watchdog.xml.template` | Tâche planifiée (S4U, boot + session + relance 5 min, sans restriction batterie) ; désactive l'ancienne `HELIOS-Watchdog-HTTP` (un seul watchdog sur 9099). Dry-run par défaut, définition validée par le Planificateur | **exécuté** (étape A, 5 nov. 20:43) ; tâche ensuite activée et démarrée |
| `tools/policy_reconcile.py` (+ `scripts/helios-ops/test_policy_reconcile.py`, 12 tests ; compare aussi l'empreinte de contenu de l'image déployée) | **Réconciliation de la politique entre TOUTES les couches, lecture seule** : YAML, Java, watchdog (dépôt / script déployé / en direct), `ACTIVE_VMS`, **code réellement présent dans l'image backend déployée** (marqueurs de version des classes), script d'activation, pré-vol, Bridge, hôte ; code de sortie 0 = COHÉRENT | prêt ; 6 nov. 16:35 : **5 écarts, tous sur `.160`** (production pas encore alignée) |
| `tools/restrict_active_vms.py` (+ `test_restrict_active_vms.py`, 11 tests avec faux kubectl) | **Désarmement ciblé** de `HELIOS_VMPOWER_ACTIVE_VMS` : RETIRE des noms, n'en ajoute jamais, ne touche ni `ENABLED` ni `DRYRUN` ni aucune autre clé ; dry-run = diff exact + effet sur le backend + retour arrière ; `--apply` = sauvegarde, correctif, relecture, redémarrage progressif, `printenv` dans chaque pod | prêt, dry-run exécuté (6 nov. 17:20) ; **`--apply` à lancer par l'administrateur** |
| `tools/backup_sequence.py` (+ `test_backup_sequence.py`, 13 tests) | **Sauvegardes A puis B, jamais ensemble** : pré-contrôles (CronJobs, espace, primaire, fenêtre 02:00/02:30), Job `Complete`, journal, nouveau fichier + taille + SHA-256, puis **preuve de restauration** ; arrêt au premier échec | prêt, dry-run exécuté ; `--apply` par l'administrateur |
| `tools/restore_probe.py` (+ `test_restore_probe.py`, 11 tests) | **Preuve de restaurabilité** : le dump est RECHARGÉ dans un PostgreSQL jetable (pod sans trafic, `/run` seulement) et chaque table doit retrouver les lignes du dump ; `--analysis-only` = lecture seule stricte | analyse seule exécutée (A et B : 16 tables, 11 961 lignes, identiques) ; restauration réelle **refusée à l'assistant** (permissions) : à lancer par l'administrateur |
| `tools/rebuild_replica.py` (+ `test_rebuild_replica.py`, 16 tests, environnement simulé) | **Reconstruction d'une réplique CNPG bloquée SANS supprimer PVC/PV/donnée** : dry-run = 14 pré-conditions (primaire prêt, réplique non prête, PV `Retain`, nœud éligible, espace, **0 écriture sur l'ancien primaire après la fourche par `pg_waldump`**, sauvegardes A/B récentes ET restaurées, aucun démarrage de VM) + diff exact + retour arrière ; `--apply` = annotation `cnpg.io/pvcStatus=detached`, retrait de l'ownerReference (correctif JSON avec `test`), suppression du POD seul, attente 2/2 + streaming | prêt, dry-run à exécuter ; **`--apply` sur autorisation spécifique** |
| `tools/image_digest.py` (+ `test_image_digest.py`, 4 tests) et `IMAGE-0.2.164-vm3auto.sha256` | **Empreinte de CONTENU** d'une image (304 fichiers classes + ressources), identique en local et dans un pod : prouve que le code déployé = le code vérifié | `0.2.164-vm3auto` = `623e83da…` (16 fichiers diffèrent de la `0.2.163`, aucun Camunda) |
| `host/Update-HeliosWatchdog.ps1` | Met à jour le SCRIPT du watchdog (table de politique) et relance la tâche : dry-run par défaut, refus si un démarrage est en cours, sauvegarde, `/health` + `--check`, retour arrière automatique ; `-RollbackTo` | prêt, dry-run exécuté (6 nov. 16:30) ; **`-Apply` à lancer par l'administrateur** |
| `host/Set-HeliosMaintenance.ps1` | Pose/retire/liste les drapeaux de maintenance (remplace la pause D7 de HA-01). Dry-run par défaut | prêt |
| `tools/make_target_bridge.py` | Reproduit le Bridge cible depuis la baseline (test de dérive) | prêt |
| `tools/apply_vm4_auto.sh` | **Étapes d'administration en une commande** (pare-feu, jeton, ConfigMap, image, Bridge, vérification ; `activate`/`deactivate` explicites). Dry-run par défaut | **exécuté** par l'administrateur le 6 nov. (pare-feu, jeton, ConfigMap, backend, Bridge) ; `activate` NON lancé |
| `tools/verify_vm4_auto.py` | Vérification **lecture seule** de l'état réel (niveau « vérifié sans arrêt de VM »), jamais de « validé par arrêt réel » | exécuté : **24 PASS / 0 FAIL / 1 PENDING** au 6 nov. 08:26 (le seul PENDING = validation par arrêt réel, N3) |
| `tools/rotate_watchdog_token.py` | Rotation du jeton backend↔watchdog (fichier hôte + secret), empreintes seulement, restauration auto ; testée avec un faux kubectl | **exécuté** le 6 nov. 01:16 |
| `tools/deploy_bridge_target.sh` | Bridge cible sur `.161` : sauvegardes, jeton retiré, preuves, retour arrière auto. Dry-run par défaut | **exécuté** le 6 nov. 07:29:57 (retour arrière : `--rollback 20261106T062957Z`) |
| `tools/make_minimal_backend.sh`, `tools/build_minimal_image.sh` | Copie « minimale » du backend HORS dépôt (sans l'édition `businessId` jamais déployée) et construction de l'image Jib | exécuté : `pfe-backend:0.2.163-vm4auto` |
| `tools/shadow_observe.py` (+ `scripts/helios-ops/test_shadow_observe.py`, 35 tests ; mode `--armed --since …` pour observer 24 h la configuration FINALE sans arrêt de VM → `observation/armed-observations.jsonl`) | **Observation du mode ombre, lecture seule** : un relevé (cadence de `VmPowerWatch` par pod, état des 5 VM, base, journaux, hôte, Bridge) ajouté à `observation/shadow-observations.jsonl` ; `--loop 900 --until <AAAA-MM-JJTHH:MM> --stop-on-event` ; `--report` pour la synthèse | outil prêt ; boucle interrompue à ≈ 08:50 (mémoire système) puis relancée à 09:09 sur accord de l'utilisateur : 1 relevé/15 min jusqu'au 7 nov. 01:45 |
| `tools/n3_preflight.py` (+ `scripts/helios-ops/test_n3_preflight.py`, 58 tests) | **Pré-vol GO/NO-GO d'un essai réel, lecture seule** (`--vm k8s-worker1|k8s-worker2|k8s-lb2`) : critères du plan §H + contrôle propre au répartiteur `.162` (l'AUTRE HAProxy, `.160`, sain) et aux cibles qui portent le leader du backend, le primaire CNPG ou le leader etcd (EFFETS ATTENDUS affichés) ; cibles = exactement les VM `AUTO` de la politique (test de cohérence) ; **refuse `k8s-monitoring` (détection seule) et `k8s-master`** ; un GO n'est PAS une autorisation | prêt, exécuté pour les cibles (6 nov.) ; le contrôle de la pile de monitoring de `.160` a été retiré avec sa cible |
| `tools/auto_status.py` (+ `test_auto_status.py`, 9 tests) | **Statut AUTO effectif, lecture seule** : CONFIGURÉE / ARMÉE / VALIDÉE par VM + DÉTECTION SEULE de `.160` (signale « ANOMALIE » si son nom figure encore dans `ACTIVE_VMS`, « à redéployer » si le watchdog en direct la dit encore AUTO) + cooldowns backend (5 min depuis le 18 nov. 2026, valeur du dépôt ; 30 min avant) et watchdog (240 s) restants | prêt, exécuté (6 nov. 11:35) |
| `tools/n3_watch.py` | **Suivi EN DIRECT d'un essai réel, lecture seule** : chronologie horodatée (état watchdog, nœud, leader, incident, action, historique, preuves, journal du watchdog) + résumé | utilisé pour `.158` le 6 nov. |
| `tools/make_minimal_backend_master153.sh`, `tools/make_minimal_backend_cooldown5.sh` (+ `patches/*.patch` / `.sha256`, `scripts/helios-ops/test_cooldown_coherence.py`, 8 tests) | Copies « minimales » HORS dépôt du backend de la production (`.153` AUTO → `0.2.166-master153` ; cooldown de redémarrage 30 → 5 min → `0.2.167-cooldown5` = production + DEUX fichiers, garde `diff -rq`) : **jamais l'arbre** (retrait de l'IA Java non déployé). Cohérence du cooldown (YAML = Java = outils ≥ watchdog 240 s) verrouillée par test | `0.2.166-master153` déployée le 17 nov. ; `0.2.167-cooldown5` **déployée le 18 nov. 10:55** (empreinte `IMAGE-0.2.167-cooldown5.sha256`, `policy_reconcile.py` COHÉRENT) : `RUNBOOK-MASTER-153.md` §8.quater |
| `tools/check_bridge_no_poweron.sh` | Contrôle fonctionnel du Bridge : 7 × `POST /action RESTART_NODE_VM` doivent tous être refusés (`POWER_ON_INTERDIT`) | prêt, **à lancer par l'utilisateur** (refusé à l'assistant par le classifieur de permissions) |

Côté backend (`backend/backend/.../service/vmpower/`) : `VmPowerPolicy` (table fermée + autorisation), `WatchdogClient`
(un seul POST, jamais rejoué), `VmProbes` (sondes parallèles bornées), `VmPowerWatch` (seul décideur de redémarrage,
mode ombre ; route CAMUNDA pour les workers et `.162` ; `.160` en détection seule — la route DIRECTE existe mais est dormante), `VmRecoveryVerifier` (preuves de
récupération : workers = Ready + kubelet + SSH + etcd + CNPG + backend ; `.162` ; `.160`). Défauts SÛRS :
`helios.vmpower.enabled=false`, `dry-run=true`, `active-vms=` vide.

## Tests (aucune VM réelle, aucun `vmrun.exe`, rien sur 192.168.148.1)

```
cd scripts/helios-ops && python -m unittest test_watchdog_vm_policy test_watchdog_contract test_bridge_no_poweron -v
cd backend/backend && ./mvnw -o test                                  # suite complète (politique, client, watch, vérification...)
cd frontend && npx ng test --watch=false --browsers=ChromeHeadless --include='src/app/features/infrastructure/**/*.spec.ts'
```

Les tables `VM_POLICY` (watchdog), `VmPowerPolicy.java` et le YAML sont comparées par les tests : un écart rend un test
rouge. Le contrat `contract/*.json` se régénère avec `python scripts/helios-ops/gen_watchdog_golden.py --write`
(relire le diff). Les copies de `baseline/` se contrôlent avec `cd baseline && sha256sum -c MANIFEST.sha256`.

## Ce que le watchdog cible ne fait plus (par rapport au déployé `helios-node-autoheal.py`)

Plus de boucle autonome, plus de branche `k8s-master`, plus de session SSH (`/host-exec`), plus de connexion à HELIOS,
plus de `kubectl`, **plus aucun identifiant en dur**, plus de `GET /status` non authentifié. Il ne lance `vmrun start`
que sur un ordre authentifié (`X-Helios-Token` + IP appelante autorisée), pour une VM `AUTO`, prouvée éteinte par
`vmrun -T ws list`, hors maintenance, hors cooldown/backoff, avec une RAM hôte suffisante, une seule fois par incident.

## Ordre de mise en production (chaque ligne = une autorisation distincte)

1. **Hôte** — `host/Install-HeliosWatchdog.ps1 -Apply` (copie du script, dossiers d'état, tâche enregistrée **désactivée**, ancienne `HELIOS-Watchdog-HTTP` désactivée).
2. **Hôte** — pare-feu `9099` limité aux 3 nœuds `.153`/`.155`/`.158` (appelants ; `.153` n'est JAMAIS une cible) ; retrait de
   l'agent legacy `:9999` ; activation et démarrage de la tâche ; `GET /health` depuis `.153`, `.155` et `.158`.
3. **Déploiement backend en MODE OMBRE** (`HELIOS_VMPOWER_ENABLED=true`, `HELIOS_VMPOWER_DRYRUN=true`, `ACTIVE_VMS` vide) —
   **avant** le Bridge : c'est le backend qui reprend les notifications `.162`/`.160` que le Bridge ne relaiera plus.
4. **Déploiement Bridge cible** (retrait de `HELIOS_WATCHDOG_TOKEN` de `bridge.env`, redémarrage du service Bridge).
5. Observation ≥ 24 h en mode ombre (historique `MODE OMBRE — redémarrage ... AUTORISÉ mais NON demandé` uniquement
   quand la VM est réellement éteinte), puis **activation par VM** (`HELIOS_VMPOWER_ACTIVE_VMS`, `DRYRUN=false`).
6. Essais réels un par un avec confirmation écrite nominative : `.158` (FAIT le 6 nov.), puis `.162`, `.160` et `.155` (le plus dur, en dernier). `.153` n'est jamais redémarrée.
   `.153` n'est jamais redémarrée. Avant de rejouer HA-01 : remplacer la pause D7 de `p4_vmstop.py` par
   `host/Set-HeliosMaintenance.ps1` (drapeau de la VM visée).

## Points à confirmer avant déploiement

- Les pods backend (2 réplicas, anti-affinité stricte) peuvent se trouver sur n'importe quel nœud, `.153` comprise (le pod
  « surge » d'un déploiement n'a pas d'autre nœud libre) : les trois nœuds sont donc autorisés comme **appelants** (décision
  du 5 nov. 2026, parité watchdog/pare-feu testée) ; `.153` reste interdite comme **cible** de redémarrage.
- Le filtre `/notifier` du Bridge repose sur le format réel observé des sujets (`[HELIOS] SAFE_STOP <TYPE> sur <cible>`,
  relevé dans les journaux HA-01) ; revérifier sur un échantillon récent du journal du Bridge avant la bascule.
- Les incidents `NODE_NOT_READY` des workers/master ne sont plus clos par Camunda sans redémarrage : ils le sont par
  `VmPowerWatch` après deux lectures `Ready` persistées (même mécanisme de preuve que `.162`/`.160`).
