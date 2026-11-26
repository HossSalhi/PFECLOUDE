# Runbook — scénario DISK E2E déterministe `DISK_E2E_158` (cible `.158` = `k8s-worker1`)

*Préparé le 13 nov. 2026, corrigé le 14 nov. (« corrections finales »), **DÉPLOYÉ le 14 nov. 2026 entre 13:18 et 13:30 (mission « terminer et déployer le scénario DISK E2E », § 12)**. **MISE À JOUR 14 nov. 2026 (15:21 → 15:24) : `--go` LANCÉ ET ABOUTI — incident #12922 `RESOLVED`, 7 points sur 7 démontrés par des données réelles (§ 14) ; les mentions « AUCUN remplissage » et « `--go` JAMAIS lancé » ci-dessous décrivent l'état de 13:34 et sont PÉRIMÉES.** **Statut à 13:34 : Bridge `00435e8f…` ET Backend `pfe-backend:0.2.165-disk-e2e` DÉPLOYÉS, drapeau dédié `HOST_DISK_E2E_158_ENABLED=true` ACTIF (ancien drapeau absent), AUCUN remplissage, `--go` JAMAIS lancé.** Pré-vol RÉEL de 13:34 = **ROUGE pour un seul motif : RAM de l'hôte 1 672 Mo < 2 500 Mo** (+ ORANGE : 29 gels ≤ 48 h, 2 incidents disque anciens). `Incident.java`, les BPMN et les traitements CPU / VM_DOWN sont intacts (260 classes sur 273 identiques octet pour octet, aucune CPU/VM_DOWN parmi les 12 modifiées + 1 ajoutée). L'injection réelle reste soumise à l'autorisation NOMINATIVE de l'exploitant et aux conditions de l'hôte (§ 6, § 11, § 12).*

## 1. Résultat attendu
`DETECTION → INCIDENT → HOST_RECLAIM_DISK → BRIDGE → VERIFY → RESOLVED`. Une escalade (`ESCALATE`, `RESOLUTION_FAILED`) est un **échec** du scénario, jamais un résultat attendu ; un échec de mesure ou de vérification n'est **jamais** transformé en `RESOLVED` (succès = fichiers exacts du scénario libérés **et** occupation réelle de `/` ≤ 77 % mesurée).

## 2. Ce qui a été préparé
| Composant | Fichier | Rôle |
|---|---|---|
| **Bridge** (source de vérité, généré) | `infra/helios-sot/bridge/bridge.py` ← `infra/helios-sot/tools/make_target_bridge.py` | action **`HOST_RECLAIM_DISK`** : supprime les SEULS fichiers du scénario (jeton unique, `rm -f --` sur chemins exacts), relit le répertoire, **mesure l'occupation réelle de `/`**, `ok` ssi plus aucun fichier du jeton ET occupation ≤ 77 % ; idempotent ; ne touche ni au minuteur ni à la garde ; le Bridge ne CHOISIT rien (`NODE_DISK_FULL → HOST_IMAGE_GC` reste inchangé dans sa table) |
| **Périmètre Backend** | `HostDiskScenario.java` (nouveau) | définition UNIQUE du scénario : `(k8s-nodes, k8s-worker1, NODE_DISK_FULL)`, nom `DISK_E2E_158`, seuil 77 % |
| **Activation dédiée** | `RemediationProperties` + `application.properties` | drapeau **`HOST_DISK_E2E_158_ENABLED`** (propriété `remediation.host-disk-e2e158-enabled`), **désactivé par défaut**, sans aucun lien avec l'ancien `HOST_DISK_REMEDIATION_ENABLED` |
| **Backend** | `HostDiskIncidentRemediationOrchestrator`, `RemediationPolicyService`, `KubernetesRemediationExecutor`, `RestTemplateBridgeClient`, `HostDiskVerificationService` | voir § 3 |
| **Scénario + porte** | `scripts/helios-ops/helios_disk_e2e.py` | paramètres figés, cohérence inter-composants (45 contrôles), **porte des prérequis**, collecte réelle en lecture seule (`collecter_faits_e2e`), simulation exécutant le VRAI Bridge |
| **Lanceur manuel** | `scripts/helios-ops/helios_disk_test.py` | `--preflight` et `--go` sur `.158` passent **obligatoirement** par la porte (§ 4) |

## 3. Activation et périmètre (ce que chaque drapeau ouvre — verrouillé par des tests)
| Cible / type d'incident | `HOST_DISK_E2E_158_ENABLED` seul | `HOST_DISK_REMEDIATION_ENABLED` (ancien) seul | les deux |
|---|---|---|---|
| `k8s-worker1` + `NODE_DISK_FULL` (**le scénario**) | **Bridge** (`HOST_RECLAIM_DISK`, seuil 77 %) | **refus** (n'active pas le scénario) | Bridge ; **jamais** de repli vers le pod hôte privilégié, même après un échec du Bridge |
| `k8s-worker2` (`.155`, primaire CNPG) + `NODE_DISK_FULL` | **jamais** | **jamais** (l'ancien drapeau l'ouvrait avant ce chantier) | **jamais** (refus : aucun Bridge, aucun pod hôte, aucune commande) |
| `k8s-worker1` **et** `k8s-worker2` + `NODE_DISK_PRESSURE` / `NODE_INODES_FULL` | n'ouvre rien d'autre : refus | **chemin d'origine STRICTEMENT inchangé** (pod hôte, mêmes cibles qu'avant, inodes à 85 %) | chemin d'origine |
| autres cibles (master, monitoring, `.161`…) | ignorées | ignorées | ignorées |

- Le seuil de **77 %** ne s'applique qu'à `k8s-worker1` + `NODE_DISK_FULL` **et seulement si** le drapeau du scénario est actif ; les inodes restent à **85 %** ; le défaut d'origine du service (`recovery-threshold-percent`) est **85,0** (rétabli).
- Aucun changement pour les scénarios CPU et VM_DOWN, ni pour `NODE_DISK_PRESSURE` / `NODE_INODES_FULL` (consigne : « ne jamais modifier ») ; suites de non-régression : § 8.
- **Risque résiduel déclaré (décision de l'utilisateur)** : `HOST_DISK_REMEDIATION_ENABLED` reste « global » pour PRESSURE/INODES, **`.155` (primaire CNPG) compris**, exactement comme avant. Il est **absent** de la ConfigMap de production aujourd'hui : rien ne change tant qu'il le reste. Le fermer pour `.155` est un changement de 3 lignes (politique, orchestrateur, exécuteur) qui modifierait ces deux types : non fait, car interdit par la consigne de ce chantier.

## 4. Lancement manuel : la porte est OBLIGATOIRE pour `.158`
`python helios_disk_test.py --target 158 --preflight|--go … --image-backend <TAG>`

Avant toute injection, le lanceur **collecte en lecture seule** (ssh de lecture, `kubectl get`, `SELECT`, lecture CIM) puis évalue `helios_disk_e2e.evaluer_e2e` :
- **Drapeaux EFFECTIFS du Backend** : `HOST_DISK_E2E_158_ENABLED=true` ; `HOST_DISK_REMEDIATION_ENABLED` absent ou `false` (sinon ROUGE : il ouvrirait le pod hôte privilégié) ; `HELIOS_BRIDGE_ENABLED=true`. L'env directe du conteneur l'emporte sur la ConfigMap ; une env `valueFrom` est « illisible ».
- **Backend** : déploiement prêt (réplicas, mises à jour, génération observée), **image du déploiement ET de chaque pod = `--image-backend`** (obligatoire, aucune valeur par défaut), pods démarrés **après** la dernière modification de `backend-config`.
- **Bridge (`.161`)** : `GET /actions` du processus en cours annonce `HOST_RECLAIM_DISK` ; **empreinte du fichier déployé = source de vérité** du dépôt.
- **Nœud `.158`** : `systemd-run` et `awk` présents, aucune unité `helios-diskfill-*` résiduelle, programme de la garde valide (statique).
- **Cooldown** : âge des actions `HOST_RECLAIM_DISK` sur `k8s-worker1` ≥ `REMEDIATION_COOLDOWN_MINUTES` (ConfigMap ; défaut 30) + 2 min ; **aucun incident DISK ouvert sur `k8s-worker1`** ; incident de fond sur `monitoring` toléré ; incident DISK ouvert **ailleurs** = ORANGE acquittable.
- **Hôte** : RAM disponible ≥ 2 500 Mo, pagination < 5 000 pages/s, plus le pré-vol de base de l'outil (identité nom d'hôte + InternalIP, volume du répertoire, Prometheus, cluster, CNPG, gels VMware, `C:`, VMDK).
- **Paramètres figés** : toute autre valeur que 81/82/83/77 %, 512 Mo, 720 s, 1 020 s est refusée.

**Refus ⇒ aucune charge, aucun incident** : fait manquant, illisible ou ROUGE ; ORANGE non acquitté ; image non déclarée. Les ORANGE ne s'acquittent que **code par code** (`--accepte-orange CODE`). **Refusés d'office sur `.158`** (exit 2, avant toute dépendance) : `--accepte-risque-primaire-cnpg` (le risque CNPG n'est jamais présumé accepté) et `--accepte-orange REMEDIATION_NON_ACTIVEE` (aucun lancement en « détection seule »).

## 5. Paramètres figés (aucune surcharge acceptée par la porte)
Détection `> 80 %` · injection **81 %** · plafond **82 %** · arrêt d'urgence **83 %** · garde système **83,5 %** · récupération vérifiée **≤ 77 %** · répertoire exclusif `/var/lib/helios-diskfill` · ≤ 16 fichiers · **≤ 6 Gio** cumulés · observation 720 s · expiration 1 020 s · 2 échantillons Prometheus sains consécutifs (≤ 8 tentatives, pas 30 s).

## 6. État des étapes — chaque étape réelle exige une autorisation nominative DISTINCTE
1. **Revue et commit** par l'utilisateur — NON FAIT (aucun commit, HEAD `bc3265a`).
2. ~~Construire le Backend~~ — **FAIT le 14 nov. 13:15** depuis la copie `helios-minimal-build-0.2.165-disk-e2e` (§ 12).
3. ~~Déployer le Bridge SoT sur `.161`~~ — **FAIT le 14 nov. 13:18** (§ 12).
4. ~~Déployer le Backend, activer `HOST_DISK_E2E_158_ENABLED=true`~~ — **FAIT le 14 nov. 13:26-13:30** (§ 12) ; le drapeau d'origine reste absent.
5. **Pré-vol réel** (`--preflight`) — FAIT à 13:34 (ROUGE : RAM) ; **puis GO nominatif de l'exploitant** (cible `.158`, acte) dans une fenêtre sans gel VMware ≥ 60 s sur 48 h (pas avant ≈ 16 nov. 04:33 UTC), avec RAM disponible ≥ 2 500 Mo et pagination < 5 000 pages/s — **FAIT le 14 nov. 2026 à 15:21:42, sur demande explicite de l'exploitant de ne pas attendre : la fenêtre propre de 48 h n'était PAS atteinte (ORANGE `GEL_HOTE_48H` acquitté par l'exploitant) ; résultat : § 14**.

## 7. Commandes — les étapes 1 à 3 ont été EXÉCUTÉES le 14 nov. 2026 (déroulé réel et preuves : § 12) ; l'étape 4 (injection) a été EXÉCUTÉE le même jour à 15:21:42 (§ 14)
> **Défaut de `build-deploy-backend.sh` constaté le 14 nov. (faux retour arrière)** : sa boucle de vérification lit le statut du déploiement AVANT que le contrôleur ait observé le nouveau spec (`readyReplicas=updatedReplicas=availableReplicas=2` périmés à t=0), sort trop tôt, ne trouve qu'un pod neuf et **revient à l'ancienne image alors que le nouveau pod est sain**. Utiliser plutôt : `kubectl set image` + `kubectl rollout status --timeout=480s` (attend la génération observée), vérifications explicites, `kubectl rollout undo` si KO (procédure suivie au § 12). Autre piège : `~/.docker/config.json` (credsStore Docker Desktop périmé) fait refuser le pull ANONYME de l'image de base par Jib (`DOCKER_CONFIG` n'est pas honoré) : le mettre de côté le temps du build puis le RESTAURER (SHA contrôlé).
```bash
# 0) Contrôles locaux (lecture) — depuis la racine du dépôt
python -B scripts/helios-ops/helios_disk_e2e.py --coherence                 # 45 contrôles
sha256sum infra/helios-sot/bridge/bridge.py                                 # empreinte attendue du Bridge déployé

# 1) Backend : copie MINIMALE hors dépôt, DÉJÀ PRÉPARÉE le 14 nov. 2026 : C:\Users\houss\helios-minimal-build-0.2.165-disk-e2e
#    = source RÉELLEMENT déployée (C:\Users\houss\helios-minimal-build-0.2.164 -> image pfe-backend:0.2.164-vm3auto) + EXACTEMENT les 19 fichiers DISK
#    (8 main/resources + 10 tests + HostDiskScenario) ; 326 tests / 0 échec compilés contre le déployé (javac + JUnit ; Maven COMPLET NON LANCÉ : RAM).
#    Preuves : C:\Users\houss\helios-state\disk-finalisation-20261114\ (diff, SHA-256 de la copie, sortie des tests).
#    NE PAS utiliser `helios-minimal-build` (ancienne base 0.2.163 vm4auto, `.160` en AUTO) ni `make_minimal_backend.sh` : ils régresseraient la politique VM_DOWN.
#    PRÉCONDITIONS À VÉRIFIER PAR L'EXPLOITANT (le script n'a AUCUNE porte RAM) : RAM disponible de l'hôte >= 2 500 Mo, pages lues/s < 5 000, C: >= 25 Go libres,
#    48 h sans gel VMware >= 60 s ; sinon NE PAS construire (Maven + Jib consomment ~1 Go et risquent de geler les VM).
BACKEND_DIR=/c/Users/houss/helios-minimal-build-0.2.165-disk-e2e MAVEN_HEAP=-Xmx512m SUDO_PASS=*** TAG=0.2.165-disk-e2e \
  bash scripts/helios-ops/build-deploy-backend.sh   # Jib sans Docker -> scp + `ctr import` sur .153/.158/.155 -> `kubectl set image` -> vérif 2/2 + santé interne -> retour arrière auto

# 2) Bridge SoT sur .161 (alias ssh `helios161`, sudo sans mot de passe). NE PAS utiliser `deploy_bridge_target.sh` tel quel : écrit pour le
#    déploiement VM_DOWN du 6 nov., son contrôle de dérive compare le Bridge déployé à la baseline `3e65e060…` alors que le déployé est
#    `85307e71…` : il s'arrêterait en « DÉRIVE ». Procédure proposée (modèle du script ; `telemetrie.py` est déjà identique : seul `bridge.py` change) :
ssh helios161 "sha256sum /opt/helios-ssh-bridge/bridge.py"                 # PRÉCONDITION (lecture) : 85307e71e1f199a3b193b24b9eab95752876c1c4ff59b2a0d80ec1ac615af814
TS=$(date -u +%Y%m%dT%H%M%SZ); scp infra/helios-sot/bridge/bridge.py helios161:/tmp/bridge.py.disk-e2e-158
ssh helios161 "sudo -n cp -p /opt/helios-ssh-bridge/bridge.py /opt/helios-ssh-bridge/bridge.py.avant-DISK-E2E-158-$TS && sudo -n install -m 644 -o root -g root /tmp/bridge.py.disk-e2e-158 /opt/helios-ssh-bridge/bridge.py && sudo -n systemctl restart helios-ssh-bridge"
ssh helios161 "curl -s -m 5 -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8099/sante; curl -s -m 5 http://127.0.0.1:8099/actions | grep -c HOST_RECLAIM_DISK; sha256sum /opt/helios-ssh-bridge/bridge.py"
#    preuves attendues : 200 · 1 · 00435e8ff652bfce5d9be36f0028f7d27411a156c410d1180fd65cf93d5d937d ; sans /sante 200 sous 25 s ou si l'empreinte diffère : retour arrière
#    retour arrière : sudo -n cp -p /opt/helios-ssh-bridge/bridge.py.avant-DISK-E2E-158-$TS /opt/helios-ssh-bridge/bridge.py && sudo -n systemctl restart helios-ssh-bridge

# 3) Activation DÉDIÉE (décision distincte) : le drapeau d'origine n'est PAS touché
kubectl -n pfe patch configmap backend-config --type merge -p '{"data":{"HOST_DISK_E2E_158_ENABLED":"true"}}'
kubectl -n pfe rollout restart deployment/backend && kubectl -n pfe rollout status deployment/backend

# 4) Pré-vol réel (lecture seule), puis lancement (autorisation nominative)
python scripts/helios-ops/helios_disk_test.py --target 158 --preflight --image-backend pfe-backend:0.2.165-disk-e2e
python scripts/helios-ops/helios_disk_test.py --target 158 --go --confirm DISK-158-81-82-83 --image-backend pfe-backend:0.2.165-disk-e2e --accepte-orange E2E_INCIDENTS_DISQUE_ANCIENS
#    ^ COMMANDE FINALE du lancement manuel. [14 nov. 2026 15:21:42 : LANCÉE avec, EN PLUS, `--accepte-orange GEL_HOTE_48H` (acquitté par l'exploitant malgré la fenêtre non propre) : commande exacte au § 14.] Texte d'origine : (NON lancée ; refusée tant qu'un ROUGE subsiste ; ne pas y ajouter GEL_HOTE_48H tant que la fenêtre n'est pas propre).
#    Jeton de confirmation (affiché par le pré-vol réel du 14 nov.) : DISK-158-81-82-83. ORANGE vus le 14 nov. : GEL_HOTE_48H (NE PAS l'acquitter : la
#    condition de la mission, 48 h sans gel >= 60 s, est plus stricte que l'outil) et E2E_INCIDENTS_DISQUE_ANCIENS (#1339/#1344 du 11 sept. 2026, voir § 11 :
#    à lire avant d'acquitter). Jamais --accepte-risque-primaire-cnpg ni REMEDIATION_NON_ACTIVEE sur .158 (refusés d'office).
```

## 8. Tests (tous locaux ; aucun accès réel)
```
cd scripts/helios-ops          # un processus par fichier
python -B -W ignore::ResourceWarning -m unittest test_helios_disk_test      # lanceur + porte câblée (210, dont la sonde du Bridge)
python -B -W ignore::ResourceWarning -m unittest test_helios_disk_e2e       # porte, collecte, sonde du Bridge, modèle Backend, nominal par le vrai lanceur + vrai Bridge (117)
python -B -W ignore::ResourceWarning -m unittest test_bridge_disk_reclaim   # Bridge (49)
python -B helios_disk_e2e.py --coherence | --demo | --refus
```
Java (hors Maven, `javac` ciblé + JUnit Platform ; Maven complet non lancé) : 32 classes DISK / CPU-RAM / politique / VM_DOWN / vérification Camunda, **326 tests, 0 échec**. Mutations (copies jetables hors dépôt) : Java **22/22** tuées, Python **35/35** tuées. Preuves : `C:\Users\houss\helios-state\disk-corrections-20261114\`.

## 9. Limites connues (déclarées, non masquées)
- Le Backend Java n'a pas été exécuté dans son ensemble ; le modèle Python du Backend n'est **pas** le code Java (ses constantes sont verrouillées contre les sources par les 45 contrôles de cohérence). Délais de la chaîne = paramètres du modèle.
- La collecte réelle de la porte (`collecter_faits_e2e`) a tourné pour la **première fois le 14 nov. 2026 à 12:03** (pré-vol réel, lecture seule, § 11) : les hypothèses ci-dessous sont **confirmées** (aucun fait « illisible ») — accès SSH `kube@192.168.148.161` pour `curl`/`sha256sum`, `GET /actions` sur `127.0.0.1:8099` (50 capacités lues), `managedFields` exposés par `kubectl --show-managed-fields`, colonnes `incident_action.created_at/target_name/target_namespace`, protection systemd de `.158` (systemd-run, awk, aucune unité résiduelle). **Ce qui n'est toujours PAS prouvé en réel** : l'exécution de la garde `awk` et des unités transitoires sur le nœud, `HOST_RECLAIM_DISK` sur le Bridge déployé (absent), le routage du Backend déployé (ancienne image).
- Les liaisons incident→jeton du Bridge sont **en mémoire** ; le programme de la garde (`awk`) et les unités systemd n'ont pas été exécutés sur un nœud (contrôle statique ici ; l'armement est vérifié actif par le lanceur avant toute allocation).
- Le pod du Backend doit avoir **redémarré après** la modification de la ConfigMap : la porte le vérifie et refuse sinon.
- La porte ne vérifie **ni l'URL ni le jeton** du Bridge côté Backend (secrets : jamais lus ; le chemin CPU les utilise déjà). Une configuration incomplète ferait échouer le scénario **après** l'injection (libération par l'outil, la garde ou l'expiration), jamais en `RESOLVED`.
- `.155` : l'outil garde son chemin d'origine (CNPG, détection seule) ; le Backend ne remédie plus **jamais** `NODE_DISK_FULL` sur `.155` (quels que soient les drapeaux) ; `NODE_DISK_PRESSURE` / `NODE_INODES_FULL` y restent **inchangés** (voir le risque résiduel du § 3).

## 10. Retour arrière
Code : restaurer les fichiers depuis Git (aucun commit n'a été fait ici). Production (après les étapes 3-4 seulement) : supprimer ou passer à `false` `HOST_DISK_E2E_158_ENABLED` puis redémarrer le Backend (la remédiation du scénario s'arrête), restaurer la copie de l'ancien Bridge (`bridge.py.avant-DISK-E2E-158-<UTC>`, § 7), redéployer l'image précédente (`pfe-backend:0.2.164-vm3auto`).

## 11. Pré-vol RÉEL du 14 nov. 2026 à 12:03 (lecture seule) — verdict ROUGE, aucune charge, aucun incident
Commande : `python scripts/helios-ops/helios_disk_test.py --target 158 --preflight --image-backend pfe-backend:0.2.165-disk-e2e` (code de sortie 1). Sortie brute conservée : `C:\Users\houss\helios-state\disk-finalisation-20261114\PREVOL-REEL-158-lecture-seule.txt`. Contexte : l'hôte Windows a redémarré le 14 nov. à 05:35 ; les 6 VM ont été rallumées à la main entre 11:28 et 11:42. **Ceci est une preuve RÉELLE de l'état de préparation (et de son échec), pas une preuve du scénario : rien n'a été injecté.**

| Niveau | Constat réel | Ce qui le lève (décision distincte) |
|---|---|---|
| ROUGE | RAM de l'hôte 990 Mo < 1 500 Mo (pré-vol de base) et < 2 500 Mo (scénario) `E2E_HOTE_RAM` | libérer la RAM de l'hôte : les 6 `vmware-vmx` occupent ≈ 22,3 Go sur 24,4 Go, donc structurel ; toute réduction de la RAM d'une VM exige une autorisation spécifique |
| ROUGE | `HOST_DISK_E2E_158_ENABLED` absent `E2E_DRAPEAU_DISQUE` | activation dédiée (§ 7.3) **après** le déploiement du Bridge et du Backend |
| ROUGE | Bridge déployé sans `HOST_RECLAIM_DISK` (50 capacités) ; empreinte `85307e71…` ≠ source de vérité `00435e8f…` | § 7.2 |
| ROUGE | image du Backend `0.2.164-vm3auto` (déploiement et 2 pods) ≠ `0.2.165-disk-e2e` | § 7.1 |
| ORANGE | 29 gels d'hôte ≥ 60 s dans les 48 h (dernier : 14 nov. 04:32 UTC, `.158` 147 s) `GEL_HOTE_48H` | fenêtre propre : pas avant ≈ 16 nov. 04:33 UTC |
| ORANGE | incidents disque anciens ouverts : #1344 `k8s-worker1-DiskPressure` et #1339 `k8s-worker2-DiskPressure` (NODE_DISK_PRESSURE, 11 sept. 2026, source AI, aucune action liée, `helios_incident` = 0 dans Prometheus) `E2E_INCIDENTS_DISQUE_ANCIENS` | ni touchés ni fermés ; à lire puis acquitter explicitement |
| INFO | #3858 `monitoring` NODE_DISK_FULL (23 oct.) toléré ; `/` de `.158` à 67,06 % (+5 315 Mo pour atteindre 81 %) ; répertoire de test absent ; VMDK `twoGbMaxExtentSparse` alloué 32,52 Go / 40 Go | — |

**Vérifié en réel, sans ROUGE** : `systemd-run` et `awk` présents sur `.158`, aucune unité `helios-diskfill-*` résiduelle, programme de la garde valide (statique), cooldown (0 action `HOST_RECLAIM_DISK` dans l'historique), pagination < 5 000 pages/s, pods Backend démarrés après la dernière modification de `backend-config`, identité de `.158` (nom d'hôte + InternalIP), `/var/lib` sur le même volume que `/`.

**Lectures complémentaires du même jour** (SELECT / ssh de lecture) : 3 nœuds Ready sans pression ; CNPG 2/2 (primaire `postgres-8` sur `.155`, réplique `postgres-7` sur `.158` Ready) ; Backend 2/2 Ready ; Zeebe UP ; Bridge actif ; la clé du Bridge (`SHA256:4FfHhMut…`) figure dans les `authorized_keys` de `kube@.158`. **93 incidents actifs au total** (presque tous anciens ; une première lecture limitée à 25 lignes avait masqué #1339/#1344). **Observations hors scénario, non traitées** : `k8s-recovery.service` en échec sur `.153` (« /var/log/k8s-recovery.log: Permission denied », cluster sain) ; incident `#12920` `NODE_HIGH_CPU` sur `lb2` ouvert à 11:44, après le rallumage des VM.

## 12. Déploiement RÉALISÉ le 14 nov. 2026 (13:15 → 13:34) — preuves et retours arrière
Preuves : `C:\Users\houss\helios-state\disk-deploy-20261114\` (sorties brutes, SHA-256, YAML avant, diffs des outils, empreintes des classes avant/après). **Rien n'a été rempli, `--go` n'a jamais été lancé, aucun incident n'a été créé ni fermé, aucun commit.**

| Étape | Fait | Preuve réelle | Retour arrière |
|---|---|---|---|
| Build | Maven/Jib sous garde mémoire (porte 5 × 10 s ≥ 1 500 Mo et < 5 000 pages/s, jamais abaissée ; arrêt auto si < 350 Mo ou > 9 000 pages/s) | `pfe-backend:0.2.165-disk-e2e`, tar 230 420 480 octets, SHA-256 `2d8daf41…640c`, imageID `sha256:7c4ab923…`, digest `sha256:02af4194…` ; compilation de 219 sources (`-parameters`, release 17) | supprimer `target/` de la copie |
| Import | `ctr -n k8s.io images import` sur `.158`, `.155`, `.153` (un par un, santé du cluster entre chaque) | image présente (`presence=1`) sur les 3 nœuds | `ctr -n k8s.io images rm docker.io/library/pfe-backend:0.2.165-disk-e2e` (non fait : aucun nettoyage) |
| Bridge | `bridge.py` SoT installé sur `.161` (scp → `/tmp`, SHA + syntaxe vérifiés, `cp -p` de l'ancien, `install` + `mv` atomique, **redémarrage du seul `helios-ssh-bridge`** placé juste après un cycle de détection) à 13:18:01 | `/sante` 200, `/actions` 50 → **51** capacités dont `HOST_RECLAIM_DISK`, SHA `00435e8f…`, 0 erreur journal ; **sonde `recommend_only` réelle** vers `.158` : `ok=true execute=false ACCEPTED RECOMMEND_ONLY`, occupation de `/` lue par le Bridge via SSH 67,13 % (commandes exécutées : `stat` du répertoire (absent) et `stat -f /`, rien d'autre) ; refus réels : `.155` `REJECTED_TARGET_NOT_ALLOWLISTED`, mauvais scénario `REJECTED_SCENARIO`, mauvais type `REJECTED_TYPE` (aucune commande lancée) | `sudo -n cp -p /opt/helios-ssh-bridge/bridge.py.avant-DISK-E2E-158-20261114T121623Z /opt/helios-ssh-bridge/bridge.py && sudo -n systemctl restart helios-ssh-bridge` (copie locale + `.sha256` : `bridge.py.deploye-avant-DISK-E2E-158-…`, ancien SHA `85307e71…`) |
| Backend | 1er essai par `build-deploy-backend.sh` : **faux échec du script** (voir § 7) → retour automatique à `0.2.164-vm3auto` (propre, 2/2) ; 2e essai (`set image` + `rollout status`) à 13:26:45 | 2 pods Ready (`worker1`, `worker2`), 0 redémarrage, santé interne 200, démarrage en 12 s, **0 ligne ERROR/Exception** ; classes : **273 (260 identiques, 12 modifiées, 1 ajoutée), toutes DISK, aucune CPU/VM_DOWN/Camunda/`Incident`** (`classes-avant-0.2.164.txt` ↔ `classes-apres-0.2.165.txt`) | `kubectl -n pfe rollout undo deployment/backend` (ou `kubectl set image … backend=pfe-backend:0.2.164-vm3auto`) ; YAML d'origine : `deployment-backend-avant-0.2.165.yaml` |
| Drapeau | `kubectl patch configmap backend-config --type merge` : UNE clé ajoutée (74 → 75 clés, aucune autre modifiée) puis `rollout restart` à 13:28:37 | `HOST_DISK_E2E_158_ENABLED=true` dans les 2 pods, `HOST_DISK_REMEDIATION_ENABLED` absent, 2/2 Ready, 0 ERROR | `python C:\Users\houss\helios-state\disk-deploy-20261114\outils\activer_drapeau.py <image> <imageID(s)> --revert` (retire la clé puis redémarre) ou `kubectl patch configmap backend-config --type json -p '[{"op":"remove","path":"/data/HOST_DISK_E2E_158_ENABLED"}]'` + `rollout restart` ; YAML d'origine : `configmap-backend-config-avant.yaml` |
| Protection systemd | exercice RÉEL non destructif avec le code de l'outil (`NoeudSsh`) sur `.158` : jeton ALÉATOIRE FICTIF, fichiers inexistants, garde au seuil réel 83,5 % (impossible à atteindre à 68,5 %) | garde `awk` et minuteur armés et ACTIFS sous systemd (toujours actifs après 8 s), puis désarmés ; **0 unité résiduelle**, `/` inchangé (68,52 %) | les unités sont transitoires (`RuntimeMaxSec`) |
| Non-régression en direct | lectures seules | aucun incident créé depuis 13:00, 0 action Backend ; tâches planifiées du Backend (`cycle` 37, `poll` 26, `checkInfrastructure` 21… par 10 min) et workers Camunda (5 types de job × 2 réplicas) reconnectés ; Zeebe UP ; 3 nœuds Ready, etcd 5/5, CNPG 2/2, 0 cible Prometheus down | — |

**Effets de bord (réels, transitoires)** : l'import d'image sur `.153` (disque) a fait monter `ETCD_SLOW_FSYNC` à 1,23 s ; le Bridge a envoyé **2 e-mails d'alerte « SAFE_STOP »** (13:22:46 `ETCD_SLOW_FSYNC` sur `k8s-master` ; 13:27:45 `DEPLOYMENT_UNAVAILABLE` sur `backend`, pendant les rollouts) — détection seule, aucune action automatique, aucun incident créé. Les `BrokenPipeError` du Bridge (`do_GET`, client déjà parti) sont préexistants (11 sur 24 h avant le déploiement). L'import a occupé ≈ 0,6 Go de `/` sur chaque nœud (`.158` : 67,06 % → 68,52 %) ; l'ancienne image `0.2.164-vm3auto` n'a PAS été supprimée.

**Nouveauté du pré-vol (testée : 117 + 210 tests)** : une SONDE du Bridge — validation seule (`recommend_only`) de `HOST_RECLAIM_DISK` vers `.158` par la liaison SSH du **Bridge** (celle qui servira à la remédiation), avec comparaison de l'occupation de `/` lue par le Bridge et par le lanceur (écart ≤ 3 points, sinon `E2E_BRIDGE_MESURE`). Elle n'est JAMAIS envoyée à un Bridge qui n'est pas EXACTEMENT la source de vérité et qui n'annonce pas la capacité (un ancien Bridge remplacerait une demande `NODE_DISK_FULL` par `HOST_IMAGE_GC`, action réelle). Le jeton du Bridge est lu et utilisé SUR `.161` (tube, jamais en argument ni affiché). Un pré-vol réussi **n'est pas** une preuve de remédiation (message ajouté à la sortie).

**[MISE À JOUR 14 nov. 2026 15:27 : le routage `.158` → Bridge, le seuil de 77 % et le scénario nominal complet ont été PROUVÉS EN RÉEL (§ 14) ; reste non prouvé en réel : le refus pour `.155` (tests seulement).]** **Non prouvé en réel à la date de ce paragraphe (13:34)** : le routage `.158`/`.155` du Backend déployé et le seuil de 77 % — prouvés par 326 tests Java + identité octet pour octet des classes déployées, pas par une décision réelle (il faudrait un incident `NODE_DISK_FULL` réel) ; le scénario nominal complet DETECT → REMEDIATE → VERIFY → RESOLVED — prouvé en simulation (vrai code du Bridge derrière son vrai HTTP, nœud et Backend simulés), jamais en réel. **Mise à jour 14 nov. 2026 (tests Java)** : les 273 classes de `target/classes` sont identiques octet pour octet à celles du pod ; l'exécution des 326 tests contre ces octets a été REFUSÉE par la porte mémoire (1 337 Mo < 1 700 Mo) et reste à relancer (`...\.claude\jobs\d63b89e5\tmp\javac\run_java_tests_octets_deployes.py`, à recopier si le job est supprimé).

## 13. Lancement réel : conditions, fenêtre conseillée, déroulé attendu, preuves à collecter (préparé le 14 nov. 2026 ; lancé le même jour à 15:21 sans attendre la fenêtre conseillée, voir § 14)
**Décision de l'utilisateur (14 nov.)** : ni RAM physique supplémentaire ni SSD externe `D:` ; objectif = injection RÉELLE d'un `NODE_DISK_FULL` sur `.158` et résolution automatique vérifiée ; aucun rebuild/redéploiement sans problème démontré ; aucun processus terminé par Claude ; **injection uniquement sur accord explicite de l'utilisateur**.

**Conditions (seuils INCHANGÉS)** : (1) fenêtre de stabilité : aucun gel VMware ≥ 60 s sur 48 h (dernier gel connu : 14 nov. 04:32:14 UTC ⇒ propre à partir du 16 nov. 04:32 UTC = 05:32 locale ; tout nouveau gel — **veille moderne / capot fermé compris** — repousse l'échéance de 48 h) ; (2) RAM hôte disponible ≥ 2 500 Mo sur 5 mesures espacées de 10 s et pages lues < 5 000/s ; (3) pré-vol réel sans ROUGE, seul ORANGE restant `E2E_INCIDENTS_DISQUE_ANCIENS` (#1344/#1339 du 11 sept. : sans action liée, `helios_incident` = 0, à lire puis acquitter par l'exploitant) ; (4) accord explicite. Une surveillance de fond en lecture seule (`surveille_pret_disk.py`) applique (1)-(3) et n'annonce PRÊT qu'alors ; elle ne lance JAMAIS la commande.

**Fenêtre horaire conseillée** (heure locale, d'après les tâches connues du projet — à reconfirmer) : éviter 03:00-04:15 (`pg_dump` 03:00/03:30, sauvegarde hors site 04:01), 07:00-07:35 (mises à jour automatiques : redémarrent `camunda8` et `helios-ssh-bridge`, ce qui couperait l'appel au Bridge) et ±5 min autour des heures paires (compactions Prometheus) ; le lancement dure au plus ≈ 17 min (observation 720 s + expiration 1 020 s). Exemple de créneau sûr : 08:10-08:50 ou 10:10-10:50. Utilisateur présent.

**Déroulé attendu** : injection jusqu'à ≈ 81 % (≈ +4,7 Go, fichiers de 512 Mo, garde `awk` 83,5 % + minuteur d'expiration armés) → au plus ≈ 5 min plus tard le détecteur (cycle `minute % 5 == 2` UTC) émet `NODE_DISK_FULL` sur `k8s-worker1` (**e-mails d'alerte possibles**) → le Backend crée l'incident (aucun incident `NODE_DISK_FULL` n'existe pour `k8s-worker1`, tous statuts confondus ; l'index d'unicité `ux_incident_open_dedup` ne porte que sur les statuts NEW/OPEN/IN_PROGRESS/WAITING_FOR_APPROVAL ; le seul `NODE_DISK_FULL` existant est #3858 `monitoring`, sans lien) → politique ⇒ `HOST_RECLAIM_DISK` via le Bridge (suppression EXACTE des fichiers du jeton, mesure de `/`) → vérification (2 passes saines) → `RESOLVED`. Un `ESCALATE`/`RESOLUTION_FAILED` est un ÉCHEC du scénario, jamais un résultat attendu ; si HELIOS ne répond pas, la garde, le minuteur ou l'outil libèrent eux-mêmes les fichiers.

**Preuves RÉELLES à collecter juste après (lecture seule)** : `python C:\Users\houss\helios-state\disk-deploy-20261114\outils\preuves_injection_reelle.py --depuis "AAAA-MM-JJ HH:MM:SS"` (heure LOCALE du lancement de `--go`) → verdict par point : P1 détection (`/` > 80 %, plafond 82 %), P2 incident créé, P3 `HOST_RECLAIM_DISK` exécuté automatiquement par le Bridge (base + journal du Bridge), P4 fichiers supprimés (répertoire vide/absent, aucune unité résiduelle), P5 retour ≤ 77 %, P6 deux vérifications saines, P7 statut `RESOLVED`. Un point non démontré est `KO` ou `INDETERMINE`, jamais `OK` ; testé le 14 nov. sur l'incident réel #12914 (il rend correctement `KO` pour les points qui ne le concernent pas).

## 14. Injection RÉELLE du 14 nov. 2026 (lancée à 15:21:42) — incident #12922 `RESOLVED`, 7 points sur 7 démontrés par des données réelles
**Autorisation et commande.** L'exploitant a demandé explicitement de ne pas attendre la fenêtre propre (14 nov. ≈ 15:20) et accepté les deux ORANGE, acquittés code par code. Commande exacte (racine du dépôt, code de sortie 0) : `python -B scripts/helios-ops/helios_disk_test.py --target 158 --go --confirm DISK-158-81-82-83 --image-backend pfe-backend:0.2.165-disk-e2e --accepte-orange GEL_HOTE_48H,E2E_INCIDENTS_DISQUE_ANCIENS`. Pré-vol de cet appel : **aucun ROUGE** ; ORANGE `GEL_HOTE_48H` (29 gels ≤ 48 h : **la fenêtre propre exigée par la mission n'était PAS atteinte ; risque accepté par l'exploitant**) et `E2E_INCIDENTS_DISQUE_ANCIENS` (#1344 / #1339) ; `/` de `.158` à 68,56 % ; sonde `recommend_only` du Bridge vers `.158` acceptée (écart de mesure 0,00 pt).

**Déroulé réel** (heure locale ; horloges de `.158`, `.160`, `.161` et `.155` alignées sur celle de l'hôte à moins de 1 s, mesuré à 15:30) :

| Heure | Événement | Source |
|---|---|---|
| 15:21:50 | sonde du Bridge (validation seule) : 0 fichier à supprimer | journal du Bridge |
| avant 15:22:27 | injection : minuteur d'expiration (1 020 s) et garde `awk` (83,5 %) armés, puis 10 blocs `fallocate` (9 × 512 Mo + 135 Mo = **4 743 Mo**) : `/` de 68,56 % à **81,00 %** en ≈ 23 s | sortie de l'outil |
| 15:22:27 | 1er échantillon Prometheus > 80 % (80,65 %) | Prometheus `.160` (pas de 15 s) |
| 15:22:33 | le détecteur émet `NODE_DISK_FULL` sur `k8s-worker1` (80,6468 %) | journal du Bridge |
| 15:22:42 → 15:22:57 | `/` = 81,00 % (maximum ; plafond du scénario 82 % respecté) ; `helios_incident{cible=k8s-worker1}` ≥ 1 dès 15:22:42 | Prometheus |
| 15:22:45 | e-mail d'alerte « SAFE_STOP NODE_DISK_FULL sur k8s-worker1 » envoyé | journal du Bridge |
| 15:22:53 | **incident #12922 créé** (source `PROMETHEUS`) ; action `HOST_RECLAIM_DISK` créée par `HELIOS-DETERMINISTIC` ; workflow Camunda confirmé (clé de corrélation 1443) | base, journal du Backend |
| 15:22:54 → 15:22:56 | Bridge : liste le répertoire (10 fichiers), `rm -f --` sur les 10 chemins EXACTS du jeton `fde46d37`, relit, mesure `/` : **81,00 % → 68,56 %** ; **action #1556 `COMPLETED`** (AUTO, `CAMUNDA_WORKER`) à 15:22:56 | base, journal du Bridge |
| 15:23:12 | 1er échantillon Prometheus ≤ 77 % (68,56 %) | Prometheus |
| 15:23:22 puis 15:23:37 | vérification : échantillon sain 1/8, puis 2/8 consécutifs → **vérification #921 `PASS`** → **incident `RESOLVED`** à 15:23:37 (workflow `COMPLETED`) | base, `workflow_logs` |
| 15:23:26 | nouvelle observation de `NODE_DISK_FULL` corrélée à l'épisode actif (aucun doublon créé) | `incident_history` |
| ≈ 15:24 | fin de l'outil : `liberer_tout` désarme minuteur et garde (`systemctl stop` de ces deux unités) puis `rmdir` du répertoire vide ; constaté à 15:24:16 : 0 unité, répertoire absent | outil, collecteur |

**Durées (horodatages de la base)** : incident → action `COMPLETED` 3 s ; action → `RESOLVED` 41 s ; **incident → `RESOLVED` 44 s** ; 1er échantillon > 80 % → `RESOLVED` 70 s. **Réserves sur ces chiffres** : (a) le détecteur n'émet que sur le cycle `minute % 5 == 2` (UTC) et l'occupation a franchi 80 % quelques secondes avant celui de 14:22:33 UTC : la latence de détection (6 s) est le MEILLEUR cas, jusqu'à ≈ 5 min dans le pire ; (b) les `T+…` de la sortie de l'outil (détection T+34 s, `RESOLVED` T+86 s) sont des horodatages de relevé, en avance d'une dizaine de secondes sur ceux de la base (écart déduit des échantillons Prometheus ; mécanisme non élucidé, probablement le début d'un relevé qui dure plusieurs secondes sur cet hôte) : seuls les horodatages de la base, du Bridge et de Prometheus font foi.

**Verdict par point** (collecteur `preuves_injection_reelle.py`, données réelles indépendantes de la sortie de l'outil) : P1 détection ✅ · P2 incident créé ✅ (un seul dans la fenêtre) · P3 `HOST_RECLAIM_DISK` exécuté automatiquement par le Bridge ✅ · P4 fichiers supprimés ✅ (répertoire absent, 0 unité `helios-diskfill-*`) · P5 retour ≤ 77 % ✅ (68,56 %, identique à l'état initial) · P6 deux échantillons de vérification sains ✅ · P7 `RESOLVED` ✅. Bilan de l'outil : `OK - detection, liberation par HELIOS et resolution` (libération attribuée à HELIOS : ni la garde ni le minuteur n'ont tiré), croissance des extents VMDK sur `C:` **+0 Mo**.

**Effets de bord contrôlés (lecture seule, 15:30)** : seul l'incident #12922 a été créé ou modifié, seule l'action #1556 a été ajoutée ; #1344, #1339 et #3858 inchangés ; 3 nœuds Ready, `DiskPressure=False` sans aucune transition pendant le test (le kubelet n'a pas réagi : seuil 85 %) ; CNPG 2/2 (primaire `postgres-8`) ; 52 pods lus : aucun pod recréé, aucun conteneur arrêté depuis 15:21:42 ; seuls événements Warning : `DNSConfigForming` (préexistant) ; Prometheus : 26 échantillons en 6 min 15 s au pas de 15 s, donc aucun trou (aucun gel d'hôte pendant l'exécution) ; drapeaux et image inchangés (`HOST_DISK_E2E_158_ENABLED=true`, ancien drapeau absent, `0.2.165-disk-e2e`). **1 e-mail d'alerte « SAFE_STOP »** envoyé (comportement normal de la détection). Vu en passant, NON investigué (CPU et VM_DOWN intouchés) : les actions `HOST_SERVICE_CPU_QUOTA` #1551 à #1554 (13-14 nov.) et `RESTART_VM` #1555 (14 nov. 11:38) sont en `FAILED` ; 3 pods `postgres-backup-b-*` en `Error` datant du 9 nov. (préexistants).

**Ce que cette exécution prouve et ne prouve pas.** Elle prouve le chemin complet `DÉTECTION → INCIDENT → HOST_RECLAIM_DISK → BRIDGE → VÉRIFICATION → RESOLVED` sur une métrique réelle (node-exporter → Prometheus → détecteur → Backend → Camunda → Bridge → `.158`), y compris **le routage `.158` → Bridge et le seuil de 77 % du Backend déployé** (le « non prouvé en réel » du § 12). Elle ne prouve PAS : (a) la reproductibilité (**n = 1**) ; (b) la libération d'un remplissage NON contrôlé : par conception l'action ne supprime que les fichiers exacts du jeton, un disque plein pour une autre cause se terminerait en `ESCALATE` / `RESOLUTION_FAILED` ; (c) le refus pour `.155` (tests seulement) ; (d) la stabilité de l'hôte (fenêtre de 48 h non propre, risque accepté ; aucun gel n'a eu lieu pendant ce test).

**État laissé.** Remédiation du scénario toujours ARMÉE (`HOST_DISK_E2E_158_ENABLED=true`, Bridge `00435e8f…`, Backend `0.2.165-disk-e2e`) ; `.158` revenue à son état initial (`/` 68,56 %, répertoire absent, 0 unité) ; surveillance de fond arrêtée (sans objet). Désarmement : § 10 / § 12 (`activer_drapeau.py --revert`). Preuves brutes : `C:\Users\houss\helios-state\disk-deploy-20261114\injection-reelle-20261114-152142` (`01-sortie-outil-helios_disk_test.txt`, `02-preuves-collecteur-P1-P7.txt`, `03-effets-de-bord-lecture-seule.txt`) + script `outils\effets_de_bord_disk.py`. Les tests Java contre les octets déployés (§ 12) restent non lancés.

## 15. Service local `helios-diskfill.service` (préparé dans le dépôt le 14 nov. 2026 — NON installé, jamais démarré)
Sur demande de l'exploitant, l'équivalent **local à `.158`** du lanceur (`sudo systemctl start helios-diskfill.service`, comme `helios-cpuload.service` pour le CPU) est écrit dans le dépôt : `infra/helios-sot/diskfill/` (programme du nœud + 2 unités systemd), `scripts/helios-ops/helios_diskfill_service.py` (pré-vol **conservé** + armement temporaire à usage unique), `deploy_diskfill_service_158.py` (installation, jamais exécutée) et 4 suites de tests (254 tests). **Le lanceur de ce runbook (`helios_disk_test.py`, `helios_disk_e2e.py`) est INCHANGÉ et reste le chemin validé.** Les deux chemins sont exclusifs : le service refuse de démarrer si le répertoire de test n'est pas vide ou si des unités du lanceur (`helios-diskfill-*`) existent. Tout est décrit dans `RUNBOOK-DISKFILL-SERVICE-158.md`.
