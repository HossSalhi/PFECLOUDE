# Rapport de clôture — mission « STABILISATION ET CLÔTURE » (6 → 11 nov. 2026)

Rédigé le 11 nov. 2026 (≈ 14:50). Convention : `[MACHINE] [MODE : RO | MODIFIANT]`. Aucun secret n'est reproduit. HEAD du dépôt **inchangé** (`bc3265a`), **aucun commit, push, reset ni clean**.

## 0. Verdict global

**Le critère de clôture du brief n'est PAS atteint** et je ne le déclare pas atteint :
1. l'**observation de 24 h** est terminée mais **non valide** (FAIL brut, fenêtre contaminée par 8 arrêts de VM provoqués) — **à refaire** ;
2. **trois des quatre scénarios** ne sont pas pleinement prouvés : VM_DOWN **validé** ; CPU **partiel** ; RAM et DISK **NON VALIDÉS — PREUVE MANQUANTE** (`../MATRICE-GLOBALE-4-SCENARIOS.md`) ;
3. l'**hôte n'a pas la mémoire** de ses 6 VM plus un poste de travail (≈ 440-520 Mo disponibles à 14:50, après fermeture de Chrome) : cause directe des `NotReady` du 11 nov. ;
4. le déploiement de la version sans IA et plusieurs correctifs de production attendent votre autorisation ; les **specs Angular à l'exécution** (`ng test`) n'ont pas été jouées (la compilation Angular, elle, est **validée**).

Bonnes nouvelles vérifiées à 14:49 : après la libération de RAM, le cluster s'est **rétabli seul** (3 nœuds Ready, **CNPG 2/2**, backend 2/2, frontend 2/2, etcd 5/5), l'agent watchdog répond (`/health` ok), `.161` exécute Bridge, Camunda et la sentinelle ; la production tourne **déjà sans IA** (service IA, Ollama et collecteur ML `disabled`/`inactive`).

## 1. Tableau par tâche

| Tâche | État initial | Action | Fichiers / machines | Risque | Vérification | Preuve | Verdict |
|---|---|---|---|---|---|---|---|
| §1-2 Baseline et préservation | 74 fichiers suivis modifiés + 642 non suivis (716 entrées), HEAD `bc3265a` | manifeste SHA-256 (`repo_manifest.py`), archive de sécurité avant tout retrait | `helios-state\snapshots\` | perte de changements locaux | recoupement manifeste de départ / archive / arbre de travail | **597 identiques, 28 modifiés volontairement, 86 retirés tous archivés au contenu identique au départ, 0 perdu**, 3 sans empreinte (2 `.bak` volumineux, 1 journal en croissance) ; HEAD et `git stash` inchangés | **VALIDÉ** |
| §3 Bridge sans mise sous tension | ancien installateur dangereux, contrôle non strictement lecture seule | `deploy_bridge.py` neutralisé (sortie 3), `check_bridge_no_poweron.sh` réécrit, 4 tests | `camunda-direct/bridge/`, `infra/helios-sot/tools/`, `.161` | faible | contrôle complet 16/16 (6 nov.) ; **statique 9/9 PASS (11 nov.)** ; `test_bridge_no_poweron.py` 16/16 | `bridge.py` `85307e71e1f1` = dépôt = déployé ; 0 variable `HELIOS_WATCHDOG*` ; écoute `127.0.0.1:8099` seul ; `.161` → hôte `:9099`/`:9999` inaccessible | **VALIDÉ** (reste D : vieux `bridge.env.bak` sur `.161`, rotation des jetons) |
| §4 Observation passive 24 h | à lancer | exécutée du 6 nov. 21:35 au 7 nov. 21:35 ; **classée** le 11 nov. | `helios-state\observation\`, `classify_observation.py` | contamination | `classify_observation.py` 26 tests ; passivité 8/8 | brut **FAIL** (40/99 et 35/99) ; **8 arrêts de VM provoqués par l'utilisateur** ; période propre 4/63 FAIL dont 3 gels d'hôte et **1 non expliqué** | **ÉCHEC / NON VALIDÉ — à refaire** (C) |
| §5 PostgreSQL / CNPG | archivage WAL nul (no-op), MinIO historique incompatible | analyse, procédure d'activation préparée, état relevé le 11 nov. | `RAPPORT-POSTGRESQL-CNPG.md` | arrêt de PostgreSQL si mal activé | lecture seule | primaire `postgres-8`, timeline 127 ; **réplique absente ≈ 6,5 h le 7 nov. puis rétablie le 11 nov. (2/2)** ; 5 PVC `Bound`, rien supprimé | état **VALIDÉ** (2/2) ; archivage WAL durable **BLOQUÉ / AUTORISATION** |
| §6 78,5 s d'indisponibilité | inexpliquées | chronologie, cause racine (leader du controller-manager sur la VM arrêtée : 113,7 s), correctifs C1-C4 ; mise à jour 7→11 nov. | `RAPPORT-DISPONIBILITE-78S.md` | modifier les délais du plan de contrôle | recoupement de 4 sources | C4 (pré-vol) implémenté : `n3_preflight.py` 64 tests | analyse **VALIDÉE** ; C1/C2/C3 **BLOQUÉS** ; C4 **CORRIGÉ ET REVÉRIFIÉ** |
| §7 Sauvegardes | rotation de 7 destructrice (volumes pleins) | CronJobs A/B et script hors cluster : rotation **gardée** ; listes `.protected` | CronJobs `pfe`, `.158`/`.155`/`.161` | suppression d'une sauvegarde | Jobs de test, `gzip -t`, 14 + 7 tests | 11 nov. : **12 + 12 + 13 dumps**, rien supprimé, dernier 11 nov. 09:00Z hors cluster ; les 6 pods `Error` du 9 nov. = base arrêtée, pas la garde | **CORRIGÉ ET REVÉRIFIÉ** |
| §8 Matrice CPU/RAM/DISK/VM_DOWN | dispersée | `MATRICE-GLOBALE-4-SCENARIOS.md` (SELECT, journaux, tests) | `infra/helios-sot/` | conclure sans preuve | preuves citées par identifiant | VM_DOWN : 13 démarrages réels ; CPU : 25 `RESOLVED` jusqu'au 30 oct., 8 échecs depuis le 2 nov. ; RAM, DISK : aucune remédiation automatique prouvée | **PARTIEL** |
| §9 Retrait IA/ML/LLM/RAG | pile IA dans le dépôt | inventaire, archive, retrait, refonte Java déterministe | `RAPPORT-SUPPRESSION-IA.md` | régression de classification | **Maven 432 tests / 0 échec** ; tests Python ; grep final | 450 fichiers retirés (archive de 1 054, vérifiée octet par octet), 41 modifiés, `/api/ai/predict` supprimé | **VALIDÉ dans le dépôt** (Java : 432/0/0 ; Angular : compilation OK) ; `ng test` non joué ; déploiement **D** |
| §10 HAProxy / TLS / API / Ingress | non audité | audit complet, correctif C2 préparé | `RAPPORT-HAPROXY-TLS-API-INGRESS.md` | modifier la production | `Verify return code: 0` ×5, 0 `verify none` chargé | config identique sur `.160`/`.162`, aucun VIP | **VALIDÉ** ; anomalies A1-A9 **D** |
| §11 Tests finaux | — | Maven, Python, Angular (compilation), contrôles, manifeste | — | tests lourds sur hôte saturé | voir §3 | voir §3 | **VALIDÉ** sauf `ng test` (non joué) |
| §12 Opérations interdites | — | **aucune** exécutée par l'assistant | — | — | journaux | aucun arrêt/démarrage de VM, aucune charge, aucun déploiement, aucune suppression de PVC/PV/sauvegarde ; `.153` et `Incident.java`/BPMN intacts | **RESPECTÉ** (les arrêts du 7-9 nov. sont ceux de l'utilisateur) |
| §13 Livrables | — | rapports, matrices, journal, runbook, mémoire | `infra/helios-sot/…` | — | — | voir §8 | **FAIT** |

## 2. Catégories

**A. VALIDÉ** : préservation des changements locaux ; Bridge sans mise sous tension (statique 9/9) ; protection des sauvegardes (revérifiée) ; audit HAProxy/TLS/API/DNS ; analyse des 78,5 s ; **VM_DOWN `.155` `.158` `.162` AUTO** (13 démarrages réels : `state_before = POWERED_OFF`, appelant = backend, un seul par incident, 0 superflu), `.160` détection seule (0 démarrage depuis la consigne finale), `.153` jamais redémarrée ; `policy_reconcile.py` **COHÉRENT**, 0 écart (11 nov. 14:49) ; **retrait de l'IA dans le dépôt** (Maven 432/0/0 ; Python 491/0).

**B. CORRIGÉ ET REVÉRIFIÉ** : rotation des sauvegardes (CronJobs A/B, copie hors cluster) ; installateur de l'ancien Bridge neutralisé ; **pré-vol N3** (critère « aucun gel d'hôte sur 60 min », fail-closed) ; **observateur** (`arrets_vix`, RAM, verdict `RESTART_VM` affiné : 13 relevés de stabilité et les 35 relevés FAIL de l'outil « armé » ne l'étaient qu'à cause du compteur cumulatif de redémarrages) ; `test_build_deploy_backend.py` (4 échecs préexistants dus à la dérive du faux `kubectl`, 5/5 OK) ; classification de l'observation (`classify_observation.py`).

**C. EN COURS** : **nouvelle observation de 24 h** — à enregistrer (`Register-HeliosObservation24h.ps1 -Tool shadow_armed|stabilite -Apply`) seulement quand : RAM de l'hôte ≥ 2,5 Go (≈ 1,1 Go à 14:54), cluster stable ≥ 1 h (3 Ready, CNPG 2/2, etcd 5/5 : **atteint depuis 14:49, il faut 1 h**) **et** votre engagement de ne pas arrêter de VM pendant 24 h.

**D. BLOQUÉ / AUTORISATION REQUISE** (rien n'est exécuté) :
1. **Mémoire de l'hôte (priorité 1)** : la somme des mémoires résidentes des 6 VM dépasse la configuration de ≈ 5,8 Go ; réduire `.161` de 4 096 à ≈ 3 072 Mo (le guest en utilise 2,2 Go, IA arrêtée) **exige d'arrêter `.161`** ; arrêter le harnais de sondes HA-01 (2 processus Python, 522 Mo de journal, +100 Mo/jour) ; ou 32 Go de RAM. Sans cela, tout build lourd ou toute réhydratation de VM rouvre une période de pagination.
2. **Cooldown de 30 min** (backend) : `.162` est restée éteinte 26 min le 7 nov. ; rendre le cooldown conditionnel = code backend + redéploiement.
3. **CNPG** : archivage WAL durable (identifiants MinIO dédiés, `serverName` neuf du jour, ex. `postgres-t127-…`) ; **rien à faire pour la réplique**, revenue seule.
4. **Production, version sans IA** : déployer le backend (rolling update), recharger Prometheus/détecteur de `.160` (l'alerte `AIServiceDown` y existe encore), traiter les fichiers IA de `.161` (`/opt/pfe-ai-service`, modèles Ollama), redéployer le Bridge sans champs `ai_*`. **Changement de comportement à valider** : aujourd'hui les pannes d'applications sont escaladées en `AI_UNAVAILABLE` ; la version sans IA rétablit leur remédiation automatique déterministe.
5. Correctifs d'architecture **C1** (timers du controller-manager), **C2** (`rise 20` HAProxy), **C3** (journaux d'accès Traefik) ; sonde DNS sans vérification TLS ; stats HAProxy sans authentification ; renouvellement `kubeadm` **avant le 30 mars 2027** ; contrôleur `ingress-nginx` inactif ; CI `.github` et `docker-compose` (cassés par le retrait, hors périmètre).
6. **Secrets** : un **mot de passe d'application Gmail en clair** dans `C:\Users\houss\helios-host-relay.py` (hors dépôt, valeur non reproduite) à **renouveler** ; l'**archive de sécurité** contient des copies de fichiers de secrets du dépôt (`infra/host-services/.env`, `backend-secret.yaml`, `monitoring-minio.env`, `bridge_token.txt`…) : à **chiffrer ou supprimer** après validation, et ces secrets à **renouveler** ; jetons du Bridge et anciens `bridge.env.bak` sur `.161`.
7. **Essais réels CPU / RAM / DISK** (charge, saturation) pour compléter la matrice : interdits sans accord nominatif.

## 3. Tests réels
| Suite | Résultat |
|---|---|
| **Maven** `./mvnw -B -o test-compile` puis `test` (11 nov. 14:43-14:45, heap 768/640 Mo, garde mémoire) | **BUILD SUCCESS** ; 205 + 49 fichiers compilés ; **432 tests, 0 échec, 0 erreur, 0 ignoré** (1 min 12 s) = 433 − 6 − 3 − 2 + 10 attendu |
| **Python** (29 fichiers `unittest`, `scripts/helios-ops/` + `infra/ha01/ops/`) | **491 tests, 0 échec** ; `test_p4_162.py` (mode script) : tous « OK » |
| `check_bridge_no_poweron.sh --static-only` | **9/9 PASS** (la partie fonctionnelle 7 refus a été jouée le 6 nov. : 16/16) |
| `policy_reconcile.py` | **COHÉRENT** : 6 couches alignées, 0 écart (backend 2/2 prêts) |
| `repo_manifest.py --compare` | 601 identiques, 27 modifiés, 43 ajoutés, **86 « disparus » tous archivés** (recoupement SHA-256 : 0 non archivé), 2 journaux en croissance |
| **Angular** `ngc -p tsconfig.app.json --noEmit` (types + gabarits AOT) puis `tsc -p tsconfig.spec.json --noEmit` (types des specs), 11 nov. 14:53-14:54, heap node 700 Mo | **RC = 0 pour les deux** (11 s + 7 s) : le frontend sans la page `fine-tuning` compile, gabarits compris. Lancé automatiquement quand l'hôte est passé à 1 081 Mo disponibles et ≈ 0 page/s (3 mesures calmes) ; mémoire disponible 871 Mo pendant la vérification, aucun déclenchement de la garde |
| `ng test` (Karma) | **non exécuté** (navigateur, mémoire) ; 4 specs `incident-detail` échouaient déjà avant ⇒ **NON VALIDÉ — PREUVE MANQUANTE** pour le comportement à l'exécution |

## 4. Empreintes déployées et états des machines (11 nov. 14:49, lecture seule)
- **Aucun déploiement** dans cette phase : backend `pfe-backend:0.2.164-vm3auto`, empreinte `623e83dae74d5376…` ; Bridge `bridge.py` `85307e71e1f1`, `telemetrie.py` `ba7d53286cf2` ; watchdog version `715022649dd0`, contrat 1, `ENABLED=true DRYRUN=false ACTIVE_VMS=k8s-lb2,k8s-worker1,k8s-worker2`.
- Nœuds : `.153` `.155` `.158` **Ready** ; etcd **5/5** ; CNPG **2/2** (primaire `postgres-8`) ; backend 2/2 ; frontend 2/2 ; 3 pods `Error` (jobs de sauvegarde du 9 nov., conservés).
- VM : 6 allumées ; mémoire résidente `.158` 1,5 Go (3,0 Go configurés, en réhydratation), `.161` 5,6 Go (4,0 configurés) ; hôte : **≈ 500 Mo disponibles**, pagination revenue à ≈ 100-600 pages/s ; `C:` ≈ 31 Go libres (93 % plein).
- `.161` : `helios-ssh-bridge`, `camunda8`, `helios-sentinel` actifs ; `pfe-ai-service`, `ollama`, `helios-collecteur-ml` **désactivés et arrêtés**.

## 5. Écarts, incidents de la session et risques résiduels
- **Porte mémoire non respectée à votre demande** : après la fermeture de Chrome, **1 560 Mo** étaient disponibles (porte : 2 500) ; j'ai lancé Maven comme convenu, avec heap borné et garde mémoire. Les sondes `web`/`api_app`/`lb*_web` ont échoué en continu **de 14:42:18 à au moins 14:46:30** ; la série a **commencé 80 s avant** mon premier Maven (14:43:38), donc **non attribuable à la compilation**, mais elle l'a recouverte. Cause la plus probable : réhydratation de la mémoire de `.158`. **Non prouvée.** La compilation Angular a ensuite **attendu** un hôte calme (≥ 1 000 Mo disponibles, ≈ 0 page/s, 3 mesures) et s'est déroulée sans incident à 14:53-14:54 (seuil de 1 000 Mo choisi par moi, **inférieur** à la porte de 2 500 Mo convenue : écart assumé et mesuré).
- Les arrêts du 7 et du 9 nov. (dont les 6 VM d'un coup, `.153` incluse) sont **les vôtres** (confirmé) ; ils expliquent les FAIL de la fenêtre mais **pas** les gels d'hôte (5,3 % de la fenêtre), qui font tomber web et API 30-57 s sans arrêt de VM : limite d'architecture (un seul hôte et un seul SSD).
- Ma propre analyse initiale a compté 14 arrêts puis 15 ; le décompte exact est **8 arrêts le 7 nov. (4 prouvés par journaux VMware, 4 estimés par l'incident) + 6 le 9 nov. à 11:33-11:35 + 1 le 9 nov. à 11:54**.
- Fichiers de configuration du **dépôt** modifiés mais **non appliqués** en production (ConfigMap du backend, règles Prometheus, manifestes) : la production reste sur l'ancienne configuration jusqu'au déploiement autorisé.
- Risques résiduels : mémoire de l'hôte (D1) ; pas d'archive WAL (le point de reprise est le dernier dump) ; harnais de sondes qui grossit ; secrets en clair (D6).

## 6. Fichiers de cette phase
- **Créés** : `infra/helios-sot/tools/classify_observation.py`, `scripts/helios-ops/test_classify_observation.py`, rapports `RAPPORT-OBSERVATION-24H.md`, `ANNEXE-CLASSEMENT-OBSERVATION-24H.md`, `RAPPORT-SUPPRESSION-IA.md`, `RAPPORT-CLOTURE-FINAL.md`, matrice `MATRICE-GLOBALE-4-SCENARIOS.md`.
- **Modifiés (outils)** : `passive_observe.py` + `test_passive_observe.py` (24 tests), `n3_preflight.py` + `test_n3_preflight.py` (64 tests), `test_build_deploy_backend.py`.
- **Modifiés (documents)** : `MATRICE-FINALE-VM_DOWN.md` (§8), `JOURNAL-MISE-EN-PRODUCTION.md`, `RUNBOOK-VMDOWN-3PLUS1.md` (§I), `PROGRESSION-MISSION-CLOTURE.md` (§4-11), `RAPPORT-DISPONIBILITE-78S.md` (§6), `RAPPORT-POSTGRESQL-CNPG.md` (§7).
- **Retrait de l'IA** : 450 fichiers retirés, 41 modifiés (liste dans `helios-state\snapshots\ai-removal-bilan.txt` et `RAPPORT-SUPPRESSION-IA.md` §2-3).
- **Hors dépôt** : `helios-state\observation\{incidents-vm-extract.json, classement-24h.json, annotations.jsonl}`, `helios-state\snapshots\ai-removal-20261106\` (+ manifeste), mémoire (`project_helios_host_ram.md`, `project_helios_observation_24h_nov2026.md`, index compacté à 15,9 Ko).

## 7. Commandes de reprise
```
# pré-requis : mémoire disponible >= 2 500 Mo (Get-CimInstance Win32_PerfFormattedData_PerfOS_Memory)
cd frontend && npx ng test --watch=false --browsers=ChromeHeadless      # specs à l'exécution (non jouées)
# nouvelle observation (cluster stable >= 1 h, aucun arrêt de VM pendant 24 h)
powershell -File infra\helios-sot\host\Register-HeliosObservation24h.ps1 -Tool shadow_armed -Apply
powershell -File infra\helios-sot\host\Register-HeliosObservation24h.ps1 -Tool stabilite  -Apply
```
