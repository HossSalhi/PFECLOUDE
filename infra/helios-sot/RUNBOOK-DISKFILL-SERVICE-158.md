# Runbook — service local DISK de `.158` : `sudo systemctl start helios-diskfill.service`

> **⚠️ ÉTAT ACTUEL = § 18 (17 nov. 2026) : démarrage DIRECT, sans armement, sans dépendance de l'hôte Windows.** Les §§ 1 bis, 3, 4, 5, 7, 8, 10, 12, 13 et 15-17 décrivent l'ancien démarrage **par armement** (14 nov.), **supprimé** le 15 nov. : ils sont conservés comme historique et pour les preuves du scénario réel du 14 nov. (§ 17). L'agent Windows DISK (arrivé le 15 nov.) est **optionnel et désactivé par défaut** (§ 18).

*Écrit dans le dépôt le 14 nov. 2026 (étape 1 du plan validé par l'exploitant), précisé le même jour par une 2ᵉ passe (§ 1 bis et § 15 : « une commande suffit », résultat final dans le journal, comportement en cas d'urgence / d'arrêt / d'échec de remédiation vérifié sur un banc systemd). **Statut : code, deux unités systemd, tests et runbook CRÉÉS dans le dépôt. RIEN n'est installé sur `.158`, aucun service n'a été démarré, aucune VM n'a été jointe, aucune injection n'a été faite, aucun commit.** Le Bridge, le Backend, les BPMN, `Incident.java`, les politiques, les seuils, le scénario CPU, le scénario VM_DOWN et le lanceur DISK validé sont INCHANGÉS (vérifié par empreintes avant/après : `helios-state\diskfill-service-20261114\`).*

## 1. Objectif et principe
Reproduire pour DISK le fonctionnement du scénario CPU (`sudo systemctl start helios-cpuload.service`) : une commande **locale à la VM `.158`** qui déclenche une injection DISK contrôlée, observable en temps réel, que HELIOS détecte (`NODE_DISK_FULL`) puis résout seul (`HOST_RECLAIM_DISK` par le Bridge, vérification, `RESOLVED`).

- Le service remplit `/` par `fallocate` jusqu'à **81 %**, **ne supprime RIEN dans le cas nominal** (seul HELIOS libère), puis vérifie le retour à **≤ 77 %**. Il ne libère lui-même que par sécurité : urgence **83 %**, garde indépendante **83,5 %**, fin d'observation **720 s**, expiration **1 020 s** — toujours par noms exacts.
- Il n'est **pas** lié à `multi-user.target` (aucune section `[Install]`) : il ne démarre jamais au boot, ne survit pas à un redémarrage de la VM.
- Il passe par **le Bridge et la remédiation HELIOS** : il ne les contourne pas et ne touche à aucun composant HELIOS.

### 1 bis. Comportement fonctionnel précisé (14 nov. 2026, 2ᵉ passe) : UNE commande, le scénario complet
Sur `.158`, **`sudo systemctl start helios-diskfill.service` suffit** (aucune deuxième commande de lancement ; la garde indépendante est démarrée **avant**, par la dépendance `Requires=`/`After=`, et ne se lance jamais à la main). Précondition (hôte, ne lance rien) : pré-vol vert + armement à usage unique (§ 7-8).

| # | Ce que la commande déclenche | Qui le fait | Où c'est visible / prouvé |
|---|---|---|---|
| 1 | remplissage contrôlé (`fallocate`, 81 %, plafond 82 %) | le service, garde liée au jeton **avant la 1ʳᵉ allocation** | journal `bloc N : …` ; tests : la garde est vivante et liée au jeton à **chaque** allocation |
| 2 | détection de l'incident `NODE_DISK_FULL` | HELIOS (détecteur de `.160`) | journal `incident HELIOS visible dans Prometheus` (observation, jamais une condition) |
| 3 | `HOST_RECLAIM_DISK` via Camunda puis Bridge | HELIOS seul (le service ne les appelle pas, ne les contourne pas) | **hôte Windows** : base + journal du Bridge (`preuves_injection_reelle.py`) ; le nœud n'en voit que l'**effet** |
| 4 | libération des fichiers **exacts** du test | le Bridge (noms exacts, un seul jeton) ; le service seulement par sécurité | journal `fichiers de test liberes … par : helios` ; tests : seuls les noms du jeton disparaissent, un fichier étranger n'est jamais touché |
| 5 | vérification du retour (`/` ≤ 77 %, 2 mesures consécutives) | le service (+ la vérification HELIOS côté base) | journal `retour a NN % confirme` |
| 6 | résultat final vérifiable | le service | **une ligne** `RESULTAT : verdict=… code=…` dans `journalctl`, et `systemctl show -p Result -p ExecMainStatus` (§ 8) |

Conditions maintenues, **aucune protection retirée** : pré-vol hôte complet ; armement à usage unique et expirant ; si une condition manque, **refus avant toute allocation** (`systemctl start` échoue dans le terminal) ; **aucun lancement au boot**, **jamais `enable`** (aucune section `[Install]`, `UnitFileState=static` exigé) ; `Restart=no`.

**Limite structurelle** : depuis `.158`, le lien causal « Camunda → Bridge → `HOST_RECLAIM_DISK` » n'est pas observable (le Bridge n'est pas joignable depuis `.158` ; la base non plus). Le journal du nœud atteste l'**effet** (incident visible, fichiers du test disparus sans qu'aucun filet local n'ait tiré, retour ≤ 77 %) et le dit honnêtement : `liberee_par=helios_infere`. La **preuve causale** reste celle de l'hôte (§ 8, étape 5).

## 2. Décisions de l'exploitant (14 nov. 2026) et contraintes de l'étape 1
| | Décision |
|---|---|
| **D1 = B** | Pré-vol **obligatoire** avec **fichier d'armement temporaire, à usage unique et expirant**. Le pré-vol conserve les contrôles de sécurité de l'hôte du lanceur DISK existant. Les ORANGE déjà acceptés ne deviennent **pas** de nouvelles conditions bloquantes de 48 h. Aucun contournement d'un ROUGE. |
| **D2** | Python 3, bibliothèque standard seule. |
| **D3** | Cooldown **local de 5 min** (**30 min jusqu'au 17 nov. 2026**, ramené à 5 min : § 18.7), après vérification de la valeur **réellement** appliquée par le Backend (**2 min** : § 9). |
| **D4** | **Refus** en présence de fichiers orphelins ; nettoyage **manuel uniquement**, jamais automatique au démarrage. |

Contraintes de l'étape 1 respectées : rien installé sur `.158` ; aucun service démarré, aucune injection ; ni le Bridge, ni le Backend, ni les BPMN, ni `Incident.java`, ni les politiques, ni les seuils, ni CPU/VM_DOWN modifiés ; aucune modification locale écrasée (seul ajout à un fichier existant : le renvoi § 15 de `RUNBOOK-DISK-E2E-158.md`) ; lanceur DISK actuel préservé ; `StateDirectoryMode=0700` vérifié explicitement (§ 6) ; SHA, identité de `.158`, armement à usage unique et expiration vérifiés (§ 7) ; tests locaux non destructifs seulement (§ 11).

## 3. Chaîne complète
```
HÔTE WINDOWS (lecture seule)                       .158 (VM)
1. helios_diskfill_service.py --preflight          (lit : sha256sum des 3 fichiers + `diskfill.py etat --json`)
   = pré-vol du lanceur DISK_E2E_158 (INCHANGÉ : RAM, pages/s, gels VMware, C:, VMDK, cluster, CNPG, Backend,
     drapeaux, Bridge + sonde, cooldown Backend, incidents ouverts, identité) + contrôles du service
2. helios_diskfill_service.py --armer ...          -> UNE écriture : `diskfill.py armer --charge <base64>` -> /run/helios-diskfill/armement.json
                                                      (valide 10 min, usage unique, SHA + identité + paramètres figés)
3.                                                 sudo systemctl start helios-diskfill.service
                                                     ├─ Requires/After : helios-diskguard.service démarre AVANT (garde indépendante, 83,5 %)
                                                     ├─ ExecStartPre `verifier` (hors bac à sable) : 10 contrôles locaux, CONSOMME l'armement, crée jeton + état
                                                     ├─ ExecStart `run` : la garde confirme son armement POUR CE JETON, puis fallocate par blocs de 512 Mo
                                                     │    jusqu'à 81 % (jamais > 82 %), puis observation (mesure toutes les 2 s)
                                                     │    detecteur -> incident -> HOST_RECLAIM_DISK (Bridge) -> vérification -> RESOLVED  (HELIOS seul)
                                                     │    fichiers disparus + / ≤ 77 % confirmé 2 fois -> bilan local OK
                                                     └─ ExecStopPost `nettoyer` : libère SEULEMENT les fichiers de CE lancement (jeton + INVOCATION_ID) ;
                                                          si le démarrage a été REFUSÉ, signale à la garde (oisive) qu'elle peut sortir
4. journalctl -fu helios-diskfill -u helios-diskguard   (temps réel) ; `systemctl start` rend la main après ExecStartPre (Type=simple)
5. preuves_injection_reelle.py --depuis "..."      (preuve complète : base, Bridge, Prometheus — lecture seule)
```
Chaque fin laisse **une** ligne `RESULTAT` dans le journal : `verifier` (REFUS, rien alloué), `run` (verdict du scénario) ou `nettoyer` (`INTERROMPU` si `run` n'a rien rendu, par exemple après un SIGKILL).

## 4. Fichiers
| Fichier | Rôle |
|---|---|
| `infra/helios-sot/diskfill/diskfill.py` | programme du nœud (sous-commandes `verifier`, `run`, `garde`, `nettoyer`, `armer`, `etat`, `nettoyer-orphelins`) ; ASCII, stdlib ; constantes FIGÉES (ni argument, ni variable d'environnement) |
| `infra/helios-sot/diskfill/helios-diskfill.service` | unité principale (modèle `helios-cpuload.service`) |
| `infra/helios-sot/diskfill/helios-diskguard.service` | garde INDÉPENDANTE (processus et cgroup distincts) |
| `infra/helios-sot/diskfill/SHA256SUMS`, `.gitattributes` | empreintes (contenu LF) ; `* text eol=lf` (le poste est en `core.autocrlf=true`) |
| `scripts/helios-ops/helios_diskfill_service.py` | hôte : `--preflight`, `--armer`, `--statut`, `--sommes` |
| `scripts/helios-ops/deploy_diskfill_service_158.py` | `--verifier`, `--installer`, `--retirer` (**jamais exécuté** contre `.158`) |
| `scripts/helios-ops/test_helios_diskfill_{service,coherence,arm}.py`, `test_deploy_diskfill_service_158.py` | tests locaux (§ 11) |
| `scripts/helios-ops/test_helios_diskfill_systemd.py` | **banc systemd** piloté par les VRAIS fichiers d'unité : un seul `systemctl start`, refus, garde, urgence, arrêt, échec de remédiation, boot (§ 11) |

Sur `.158` (une fois installé, étape 3) : `/opt/helios-diskfill/diskfill.py`, `/etc/systemd/system/helios-diskfill.service`, `/etc/systemd/system/helios-diskguard.service` ; état volatil `/run/helios-diskfill/` (armement, état, battement de la garde, marqueurs) ; `/var/lib/helios-diskfill/` (**répertoire exclusif du Bridge**) ; `/var/lib/helios-diskfill-etat/` (horodatage du cooldown local, **hors** du répertoire du Bridge).

## 5. Protections du test validé → service local ; différences
| Test validé (lanceur Windows) | Service local |
|---|---|
| paramètres figés 81 / 82 / 83 / garde 83,5 / retour 77, blocs 512 Mo, ≤ 16 fichiers, ≤ 6 Gio, 720 s / 1 020 s | **mêmes constantes**, codées en dur ; l'armement doit les reproduire à l'identique (aucune surcharge, pas même un resserrement) ; test différentiel contre le lanceur et le Bridge |
| avant chaque bloc : mesure fraîche, < urgence, < cible, après-bloc ≤ 82 %, cumul ≤ 6 Gio, délai suffisant ; après : effet ≥ 90 % | **même arithmétique** (test différentiel sur une grille de 8 volumes × 19 occupations) |
| `fallocate` (aucune donnée écrite) | `/usr/bin/fallocate -l N -- chemin`, **aucun repli** (`dd`, `posix_fallocate`) |
| deux filets armés avant la 1re allocation | garde indépendante démarrée AVANT (`Requires=`) et **confirmée pour ce jeton** avant la 1re allocation ; `RuntimeMaxSec=1020` + `ExecStopPost` ; perte de la garde en cours de route = arrêt et libération |
| urgence 83 % → libération immédiate | contrôle toutes les 2 s (le lanceur : 5 s) et avant/après chaque bloc |
| libération par noms exacts, désarmement après libération vérifiée | `unlink` par descripteur de répertoire, `O_NOFOLLOW`, nom validé par motif exact, fichier régulier, root, 1 lien ; relecture, pas le code retour |
| répertoire root 0700, même volume que `/`, non vide = refus | `StateDirectory` + vérification `stat`/`findmnt` ; **orphelins = refus** (D4) |
| porte hôte obligatoire (`--go --confirm … --accepte-orange`) | **armement** obligatoire, déposé uniquement après le **même** pré-vol (D1) |

**Différences (déclarées)** : (1) le service ne voit ni Windows, ni la base, ni les drapeaux, ni le Bridge (relevé du 14 nov. : GET sans réponse depuis `.158` ; le pré-vol l'interroge en local sur `.161`) : ces contrôles restent à l'hôte (pré-vol → armement) ; (2) le verdict local est **partiel** (fichiers disparus sans déclenchement de la garde ni de l'échéance, `/` ≤ 77 %) : la preuve complète est le collecteur hôte ; (3) unités **statiques** installées d'avance (vs transitoires) ; (4) le répertoire de test **persiste vide** (`ProtectSystem=strict` interdit le `rmdir`) ; (5) pas de contrôle de croissance du VMDK (`fallocate` seul ; +0 Mo mesuré le 14 nov.) ; (6) un reboot en cours de lancement laisse des orphelins : **aucune suppression au boot**, démarrage refusé, nettoyage manuel (§ 8).

## 6. Contrat du Bridge et `StateDirectoryMode=0700`
Le Bridge (`HOST_RECLAIM_DISK`) exige `directory|root|700` pour `/var/lib/helios-diskfill` et **refuse toute suppression** sinon. Le défaut de systemd pour `StateDirectory=` est **0755** : `StateDirectoryMode=0700` est donc **explicite dans les DEUX unités** et vérifié à quatre niveaux :
1. **lint des unités** (tests) : `StateDirectoryMode=0700` présent dans les deux ;
2. **installation** : `deploy_diskfill_service_158.py` relit les propriétés CHARGÉES (`systemctl show`) et **annule** l'installation si le mode n'est pas `0700` ;
3. **pré-vol hôte** : contrôle `SVC_UNITE` (ROUGE) ;
4. **démarrage** : `verifier` relit `systemctl show` **et** le mode réel du répertoire (`anomalies_dossier` : `directory`, `root:root`, `700`, volume de `/`) **avant toute allocation**. Un répertoire en 0755 = refus, rien n'est alloué.

Ce que le service crée est accepté par le **vrai** `disque_plan()` du Bridge (tests : noms `helios-diskfill-<32 hex>-<001..016>.bin`, fichiers réguliers root, 1 lien, un seul jeton, ≤ 16 fichiers, ≤ 6 Gio ; les fichiers d'état sont HORS du répertoire pour ne pas devenir des « entrées étrangères »).

**Point de montage (constaté en RÉEL sur `.158`, 14 nov. 2026, systemd 255)** : dans l'espace de noms de l'unité — **y compris pour `ExecStartPre=+`** — le `StateDirectory=` est un *bind mount* de lui-même : `findmnt -T /var/lib/helios-diskfill` y rend `TARGET=/var/lib/helios-diskfill`, alors que dans l'espace racine (ssh, Bridge, lanceur, pré-vol hôte) il rend `ext4 /` avec le même `st_dev` que `/`. Le critère d'origine « point de montage = `/` » refusait donc TOUT démarrage réel (`[KO] DOSSIERS`). Règle actuelle (`anomalies_dossier`) : le point de montage est `/` **ou le répertoire de test lui-même**, et il reste exigé que le périphérique soit celui de `/`, ext4 ou xfs, `directory|root|700` ; tout autre montage (parent, répertoire d'état, `/run`…) ou un autre volume monté dessus reste **refusé**. C'est un **écart déclaré** avec le lanceur (qui tourne dans l'espace racine et garde `montage == "/"`) ; il est verrouillé par des tests de cohérence, de service et de banc, et par 5 mutations (sur-permissivité).

## 7. Armement : usage unique, expiration, SHA, identité
Fichier `/run/helios-diskfill/armement.json` (tmpfs : perdu au redémarrage), écrit atomiquement par `diskfill.py armer` :
- **identité** : `cible=k8s-worker1`, `ip=192.168.148.158` ; le nœud revérifie son nom d'hôte et la présence de l'IP (au dépôt de l'armement **et** à chaque démarrage). Les autres machines sont refusées (`.155`, `.153`…).
- **SHA-256** des trois fichiers (script + 2 unités) mesurés par l'hôte (`sha256sum`, pas l'auto-déclaration du programme) et comparés à `SHA256SUMS` ; le nœud les **recalcule** au dépôt et à chaque démarrage : un fichier modifié depuis le pré-vol = refus.
- **expiration** : 60 s à 900 s (défaut 10 min), comptée sur l'**horloge du nœud** depuis la réception ; horloges hôte/nœud à plus de 120 s = refus ; armement d'un autre démarrage du nœud, daté du futur ou expiré = refus.
- **usage unique** : consommé par renommage atomique au démarrage ; un 2e démarrage (même concurrent) ne le retrouve pas.
- **sceau** SHA-256 du contenu ; schéma strict (clé en trop ou en moins = refus) ; **paramètres figés** identiques ; ORANGE acquittés enregistrés (jamais `REMEDIATION_NON_ACTIVEE` ni le risque CNPG).
- un armement est **refusé** pendant un lancement et en présence d'orphelins.

**ORANGE** : inchangés — acquittables **code par code** (`--accepte-orange`), exactement comme `--go`. `GEL_HOTE_48H` reste un ORANGE acquittable (jamais une condition de 48 h). Les ORANGE acquittés sont écrits dans l'armement et **journalisés au démarrage**. Les contrôles propres au service sont des **ROUGE ou de l'INFO**, jamais des ORANGE.

## 8. Procédure d'emploi (une fois le service installé — étape 3, autorisation nominative)
```
# 1. hôte Windows, lecture seule
python scripts/helios-ops/helios_diskfill_service.py --preflight --image-backend pfe-backend:0.2.165-disk-e2e
# 2. hôte Windows : UNE écriture sur .158 (pré-vol vert requis ; ORANGE acquittés code par code)
python scripts/helios-ops/helios_diskfill_service.py --armer --confirm DISKFILL-158-81-82-83 --image-backend pfe-backend:0.2.165-disk-e2e [--accepte-orange GEL_HOTE_48H,E2E_INCIDENTS_DISQUE_ANCIENS] [--ttl-min 10]
# 3. sur .158 (dans les 10 minutes)
sudo systemctl start helios-diskfill.service
# 4. suivi (autre terminal sur .158)
journalctl -fu helios-diskfill -u helios-diskguard
# abandon à tout moment : libère aussitôt, par noms exacts, les fichiers de CE lancement
sudo systemctl stop helios-diskfill.service
# 5. preuves (hôte, lecture seule) ; l'heure de départ est dans le journal (UTC ; heure locale = UTC + 1 h)
python C:\Users\houss\helios-state\disk-deploy-20261114\outils\preuves_injection_reelle.py --depuis "AAAA-MM-JJ HH:MM:SS"
# état à tout moment
python scripts/helios-ops/helios_diskfill_service.py --statut
```
Déroulé attendu : `verifier` ≈ 5-10 s ; remplissage ≈ 25 s (10 blocs) ; détection au prochain cycle du détecteur (`minute % 5 == 2` UTC, **jusqu'à ≈ 5 min**) ; incident, action, vérification, `RESOLVED` ≈ 45 s ; bilan local `OK`. Aucune libération par le service dans ce cas. Si HELIOS ne répond pas : à 720 s le service libère lui-même, le bilan est `AUCUNE_DETECTION` ou `DETECTION_SEULE` (codes de sortie 4 ou 3 : **échec du scénario**, jamais un résultat attendu).

**Orphelins** (reboot ou libération incomplète) : le démarrage et l'armement les **refusent** et rien n'est supprimé. Après examen par l'exploitant : `sudo /usr/bin/python3 -I -B /opt/helios-diskfill/diskfill.py nettoyer-orphelins` (plan seulement), puis `… --confirmer SUPPRIMER-ORPHELINS-<8 premiers caractères du jeton>` (mêmes règles que le Bridge : un seul jeton, fichiers réguliers root à 1 lien ; entrées étrangères jamais touchées ; refusé tant qu'une unité est active).

### Lire le résultat final (journaux systemd)
```
# sur .158, une fois le lancement terminé (ou refusé)
sudo journalctl -u helios-diskfill -u helios-diskguard --since "-30 min" --no-pager | grep -E "RESULTAT|BILAN|REFUS"
systemctl show helios-diskfill.service -p ActiveState -p Result -p ExecMainStatus
```
La ligne finale a la forme `helios-diskfill : RESULTAT : verdict=OK code=0 etape=run jeton=ab12cd34 incident_vu=oui liberee_par=helios_infere usage_initial=68.60 usage_atteint=81.00 usage_final=68.60 recupere=oui liberation_verifiee=oui blocs=10 alloue_mo=4728 duree_s=51` (une seule par lancement ; ASCII ; un champ = un mot).

| `verdict` | code | unité après la fin | Signification | Suite |
|---|---|---|---|---|
| `OK` | 0 | `inactive`, `Result=success` | fichiers libérés **sans** que la garde ni l'échéance aient tiré, `/` ≤ 77 % confirmé ; attribution à HELIOS **inférée** | confirmer par le collecteur hôte (étape 5) |
| `REFUS` | 2 | `failed`, `exit-code` | `etape=verifier` : une condition manque, `systemctl start` **échoue dans le terminal**, **rien n'est alloué** ; `etape=run`, `blocs=0` : refus de sécurité avant la 1ʳᵉ allocation (garde non liée, plan impossible…) | lire les lignes `[KO]`, corriger, refaire pré-vol + armement (§ 13) |
| `DETECTION_SEULE` | 3 | `failed`, `exit-code` | **échec de remédiation** : incident visible mais fichiers non libérés par HELIOS pendant 720 s ; le service a libéré lui-même (sécurité) | enquêter côté HELIOS (Bridge : `UNSAFE_DIRECTORY`, `AMBIGUOUS_TOKENS`…, Camunda, drapeau) |
| `AUCUNE_DETECTION` | 4 | `failed`, `exit-code` | aucun incident visible dans Prometheus pendant la fenêtre ; fichiers libérés par le service | enquêter côté détecteur de `.160` |
| `URGENCE` | 5 | `failed`, `exit-code` | `/` ≥ 83 % (service) ou ≥ 83,5 % (garde indépendante) : fichiers libérés, test invalide | chercher la cause (autre écriture sur le disque ?) |
| `ARRET` | 6 | `inactive`, `Result=success` (`SuccessExitStatus=6`) | `systemctl stop` / SIGTERM : fichiers libérés aussitôt (`liberee_par=service`, ou `helios_infere` si HELIOS avait déjà libéré) | — |
| `ERREUR` | 1 | `failed` | erreur du nœud, retour ≤ 77 % non confirmé après libération, garde perdue, **échéance dépassée** (SIGTERM de `RuntimeMaxSec` : unité en `timeout`) | lire `BILAN` |
| `INTERROMPU` | 1 | `failed` (`Result=signal`…) | `run` tué (SIGKILL, plantage) : `nettoyer` a libéré par noms exacts ; si `liberation_verifiee=non` la garde reste armée, sinon nettoyage manuel | vérifier `/var/lib/helios-diskfill` |

**Comportement vérifié sur le banc systemd** (émulation, § 11 ; pas encore sur la VM) :
- **Urgence** : croissance externe pendant l'observation → à 83 % le **service** libère par noms exacts (`URGENCE`, code 5) ; à 83,5 % c'est la **garde** (`liberee_par=garde`) ; pendant le remplissage, plus **aucune** allocation après l'urgence ; service tué + `nettoyer` en échec → la garde reste armée et libère à son seuil.
- **Arrêt** : `stop` pendant le remplissage, pendant l'observation, juste après la libération par HELIOS, ou avant la 1ʳᵉ allocation → fichiers libérés, `ARRET`, unité propre, aucune allocation ensuite ; `kill -9` → `ExecStopPost` libère et le journal garde `INTERROMPU` ; `restart` n'est **pas** une échappatoire (l'armement est consommé : le 2ᵉ démarrage est refusé).
- **Échec de remédiation** : HELIOS absent, détection sans remédiation, Bridge qui refuse (répertoire non conforme, deux jetons), libération sans retour ≤ 77 %, remédiation trop tardive → le service attend la fenêtre de 720 s puis libère lui-même ; le disque n'est jamais laissé plein ; l'unité est `failed` avec le code du verdict.
- **Refus** : sans armement, armement expiré ou consommé, d'un autre démarrage, fichier modifié, cooldown, orphelins, garde absente ou qui meurt → `systemctl start` échoue, **aucune allocation**, l'armement n'est consommé que par un démarrage accepté.
- **Garde** : après un refus elle sort d'elle-même en quelques secondes (marqueur posé par `nettoyer`, ignoré si le marqueur est plus ancien qu'elle, si elle garde un lancement, ou si un nouveau démarrage est en cours) ; sinon au bout de 120 s.

## 9. Cooldown (D3) — valeur réellement appliquée par le Backend
Relevé en lecture seule le 14 nov. 2026 : `REMEDIATION_COOLDOWN_MINUTES = 2` dans la ConfigMap `backend-config` **et** dans l'environnement des deux pods (le défaut du code, `remediation.cooldown-minutes=${REMEDIATION_COOLDOWN_MINUTES:30}`, n'est donc **pas** celui de la production). `RemediationPolicyService.isCooldownBlocked` contourne le cooldown si le type d'incident change (« smart cooldown »).

- **Cooldown local = 5 min** (D3 ; **30 min jusqu'au 17 nov. 2026**, voir § 18.7), mesuré depuis le début du dernier lancement (horodatage dans `/var/lib/helios-diskfill-etat/`), plus long que celui du Backend : **pré-vol ROUGE `SVC_COOLDOWN_LOCAL`** si non écoulé, **refus** au démarrage sinon.
- Règle de cohérence : si un jour le cooldown du Backend dépasse celui du service (en minutes), le pré-vol est **ROUGE** (`SVC_COOLDOWN_INFERIEUR_BACKEND`). La valeur lue du Backend est enregistrée dans l'armement et journalisée.
- Le pré-vol du lanceur continue de contrôler, en base, l'âge de la dernière action `HOST_RECLAIM_DISK` (`cooldown + 2 min`).

## 10. Installation (étape 3 — NON exécutée) et exercice non destructif recommandé
`deploy_diskfill_service_158.py` : `--installer` (sans `--confirm` : **plan seulement**) ; avec `--confirm INSTALLER-DISKFILL-158` : pré-contrôles ROUGE (identité, SHA256SUMS, python ≥ 3.8, systemd ≥ 245, outils, aucune unité du lanceur, répertoire de test absent/vide, unités inactives), envoi des **octets LF** par `scp` vers un répertoire temporaire, vérification SHA avant/après, sauvegarde `.AVANT-DISKFILL-<UTC>` de l'existant, `install` (0644 root), `systemd-analyze verify`, `daemon-reload`, vérification des **propriétés chargées** (dont `StateDirectoryMode=0700`) et `diskfill.py verifier --a-blanc` ; **retour arrière** au moindre écart. **Jamais** `enable`, `start`, `restart`, `mask`. `--verifier` : lecture seule. `--retirer --confirm RETIRER-DISKFILL-158` : retire les trois fichiers (noms exacts).

**Exercice non destructif recommandé après installation (autorisation distincte)** : `sudo systemctl start helios-diskfill.service` **sans armement**. Attendu : la commande **échoue** (« Job … failed… control process exited with error code »), `ExecStartPre` journalise `[KO] ARMEMENT` et `RESULTAT : verdict=REFUS code=2 etape=verifier controles_ko=ARMEMENT alloue=non`, **aucune allocation**, `ExecStopPost` sans effet ; la garde, démarrée par la dépendance, **sort seule en quelques secondes** (journal `demarrage du service refuse, aucun lancement`) ; systemd crée `/var/lib/helios-diskfill` **en 0700** (à relire : `stat -c '%F|%U|%a' /var/lib/helios-diskfill` doit rendre `directory|root|700`).

Chaque hypothèse du banc systemd (§ 11) s'y vérifie sans rien remplir :
| Hypothèse du banc | Comment la lire sur `.158` pendant l'exercice |
|---|---|
| `Requires=`/`After=` : la garde est active **avant** `ExecStartPre` | dans `journalctl -u helios-diskguard -u helios-diskfill -o short-precise`, `garde : demarree` précède `VERIFICATION DE DEMARRAGE` ; ligne `[OK] GARDE : garde independante armee et libre` |
| `ExecStartPre=+` voit `/var/lib/helios-diskfill*` et `/run/helios-diskfill` (créés avant le contrôle) | lignes `[OK] DOSSIERS` (3 répertoires) ; si l'un est absent, `[KO] DOSSIERS` : la garde déclare les deux répertoires d'état pour couvrir ce cas |
| `systemctl show -p StateDirectoryMode` rend `0700` | ligne `[OK] INSTALLATION : … StateDirectoryMode=0700` |
| `UnitFileState=static`, aucun drop-in, `Restart=no` | lignes `[OK] INSTALLATION` |
| la garde peut interroger `systemctl is-active` dans son bac à sable | elle sort en quelques secondes ; si elle reste active ~120 s, cet appel a échoué (sans danger : repli sur le délai d'oisivete) |
| `systemctl start` échoue visiblement sur un refus | code de retour non nul et message dans le terminal |

Rien de plus ne peut être confirmé sans remplissage réel (`SuccessExitStatus=6`, `RuntimeMaxSec`, bac à sable de `run`) : cela relève d'une injection réelle, **autorisation nominative distincte**.

**Résultat de cet exercice, exécuté pour de vrai le 14 nov. 2026 (21:59 local, `.158`, systemd 255)** — preuves : `helios-state\mission-disk-e2e-20261114\04-test-refus\` :
| Hypothèse du banc | Réel |
|---|---|
| `Requires=`/`After=` : la garde est active avant `ExecStartPre` | **confirmée** (garde `Started` 13 ms avant `Starting` de la principale) |
| répertoires créés avant `ExecStartPre=+` | **confirmée** (`[OK] DOSSIERS` ×3, `directory|root|700`) |
| `systemctl show -p StateDirectoryMode` = `0700` | **confirmée** |
| `UnitFileState=static`, aucun drop-in, `Restart=no` | **confirmée** |
| `systemctl is-active` dans le bac à sable de la garde ; sortie seule | **confirmée** (garde arrêtée 2 s après le refus) |
| `systemctl start` échoue visiblement, `ExecStopPost` s'exécute, une seule ligne `RESULTAT` | **confirmées** |
| `ExecStartPre=+` voit les points de montage RÉELS | **RÉFUTÉE** : il voit ceux de l'espace de noms de l'unité (bind mount du `StateDirectory`) → critère corrigé (§ 6), banc rendu fidèle |

## 11. Tests exécutés (locaux, non destructifs ; chaque test échoue si le code tente un accès réseau, ssh, sous-processus ou sleep réel)
| Suite | Tests | Contenu |
|---|---|---|
| `test_helios_diskfill_service.py` | 121 | monde émulé (horloge simulée, HELIOS simulé avec le VRAI `disque_plan()` du Bridge, VRAIE garde à chaque pas) : vérification (refus un par un), armement (usage unique, expiration, SHA, identité), cycle complet, urgence, garde, nettoyage, orphelins, `MondeReel` sur couches `os` simulées ; **répertoire monté en bind sur lui-même accepté, tout autre montage ou volume refusé** |
| `test_helios_diskfill_coherence.py` | 51 | constantes identiques au lanceur, à la porte et au Bridge ; fonctions pures identiques (grille) ; contrat du Bridge ; lint des unités ; source verrouillé ; SHA256SUMS |
| `test_helios_diskfill_arm.py` | 51 | pré-vol du lanceur appelé tel quel ; 27 ROUGE du lanceur et 7 ROUGE du service jamais contournables (avec tous les acquittements) ; ORANGE acquittables et enregistrés ; une seule écriture ; bout en bout hôte → nœud |
| `test_deploy_diskfill_service_158.py` | 33 | installateur sur nœud fictif : plan, pré-contrôles, ordre, retours arrière, mise à jour, retrait, jeu fermé de commandes |
| `test_helios_diskfill_systemd.py` | 63 | **banc systemd** (voir ci-dessous) : un seul `systemctl start` de bout en bout (9 tests : hôte → armement → démarrage → HELIOS → libération → vérification → `RESULTAT`), 14 tests de refus (aucune allocation) et de garde oisive, 11 tests de garde avant la 1ʳᵉ allocation / premier démarrage / `StateDirectoryMode` / `[Install]`, urgence (4 : service, garde, remplissage, kill + `nettoyer` en échec), arrêt (7 : 5 moments + `kill -9` + `restart`), échec de remédiation (6, dont les deux refus réels du Bridge), `RuntimeMaxSec` et boot (3), unités et banc (5), **espace de noms de l'unité (4 : le répertoire d'état y est un bind mount — constaté sur `.158` —, démarrage accepté, seul `ARMEMENT` en échec sans armement, autre volume refusé)** |
| **Total** | **319** | tests de **mutation** sur copies jetables hors dépôt : **121/121 mutations détectées** (36 nœud, 25 hôte, 20 installateur, 35 pour les comportements de la 2ᵉ passe, **5 pour le correctif du point de montage**) |

**Le banc systemd** (`Systemd` dans `test_helios_diskfill_systemd.py`) est piloté par les VRAIS fichiers d'unité : `Requires=`/`After=` (la garde démarre avant), `ExecStartPre=+` → `ExecStart` → `ExecStopPost` (toujours, même après un refus ou un `SIGKILL`), `Type=simple`, `Restart=no`, `RuntimeMaxSec` (`timeout`), `TimeoutStartSec`/`TimeoutStopSec`, `SuccessExitStatus`, `StateDirectory`/`RuntimeDirectory` + `StateDirectoryMode` (défaut 0755 de systemd si absent), `ProtectSystem=strict` (écriture hors des répertoires déclarés = `EROFS` hors commande `+`), `UnitFileState` selon la présence de `[Install]`, redémarrage de la machine (`/run` vide, nouveau `boot_id`). Le programme du nœud est le vrai ; HELIOS est simulé sur le VRAI `disque_plan()` du Bridge ; la garde est le VRAI `garde_tour`. Chaque ligne de commande des unités est validée par le VRAI analyseur d'arguments. Des tests de sensibilité retirent un élément de l'unité (`Requires=`, `StateDirectoryMode`, `RuntimeMaxSec`, `SuccessExitStatus`, `[Install]`…) et vérifient que le résultat change : le banc ne « passe » pas par construction.

Non-régression (suites existantes, inchangées) : lanceur 210, porte E2E 117, Bridge disque 49, cohérence 45/45. Lancer : `python -I -B <fichier>` depuis `scripts/helios-ops`, un processus par fichier.

## 12. Limites connues — non prouvé (à ne pas affirmer)
- **Le chemin de REFUS et le chemin `run` ont chacun tourné pour de vrai, une seule fois chacun dans sa forme finale** (refus : exercice du § 10, `.158`, 14 nov. 2026, 22:20 local ; `run` : scénario complet du § 17, 14 nov. 2026, 22:24:30 local, `verdict=OK code=0`). C'est **un échantillon** (service local : n = 1 ; avec l'injection du lanceur de 15:21 le même jour : n = 2 injections contrôlées au total). **Jamais exercés en réel** : l'urgence (garde ≥ 83,5 %), `systemctl stop` pendant un remplissage (`SuccessExitStatus=6`), l'échéance (`RuntimeMaxSec`), l'échec de remédiation (`DETECTION_SEULE`), le `kill -9`, le redémarrage de la VM en cours de lancement : ces chemins ne reposent que sur le banc systemd (§ 11), une émulation dont une hypothèse a déjà été réfutée par le réel (§ 6). La garde n'a **jamais tiré** en réel (« aucun de nos filets n'a tiré : HELIOS attendu ») : sa capacité à libérer le disque n'est prouvée que par les tests.
- **Le banc systemd (§ 11) est une émulation, pas systemd.** Il applique ses hypothèses (listées en tête de `test_helios_diskfill_systemd.py`) aux VRAIS fichiers d'unité et au VRAI programme ; il détecte une unité incohérente avec elles, il ne prouve pas que systemd 255 les respecte. Variante prudente testée : si systemd ne créait les répertoires de l'unité principale qu'à `ExecStart`, ceux de la garde (démarrée avant) suffisent.
- Le verdict local n'atteste **pas** que c'est HELIOS qui a libéré (un tiers resterait indiscernable) : la ligne `RESULTAT` dit `liberee_par=helios_infere` ; la preuve est la base et le journal du Bridge (collecteur). Le lien Camunda → Bridge → `HOST_RECLAIM_DISK` n'est observable que depuis l'hôte. Preuve causale produite le 14 nov. 2026 : § 17. **Limite de lecture** : sur `.158`, le journal `sudo` attribue la commande `rm -f --` au compte `kube`, **le même compte** que celui des commandes de lecture de l'opérateur et du Bridge ; ce qui rattache cette suppression au Bridge est son propre journal (`.161` : `EXEC worker1 … sudo -n /usr/bin/rm -f -- …` avec les mêmes 10 noms, ≈ 0,29 s avant la ligne `sudo` du nœud : écart entre les horloges de deux VM) et l'absence de toute commande `rm` dans les commandes de l'opérateur.
- **Course connue (sans danger)** : un 2ᵉ `systemctl start` lancé dans la ou les deux secondes qui suivent un **refus** peut trouver la garde oisive en train de partir : refus `garde independante … non armee`, **armement non consommé** ; relancer la commande (une garde neuve démarre). Une garde ne part jamais pendant qu'un démarrage est en cours.
- Une exécution réelle est un **échantillon** ; la détection tombe sur le cycle du détecteur (6 s dans le meilleur cas, ≈ 5 min dans le pire).
- Le Bridge n'est pas joignable depuis `.158` : aucune sonde du Bridge côté nœud (la sonde `recommend_only` reste dans le pré-vol hôte).
- `/run` est volatil : un redémarrage de la VM annule l'armement et l'état (sans impact de sécurité : rien n'est alors alloué).

## 13. Refus fréquents
| Message / code | Cause | Remède |
|---|---|---|
| `aucun armement` | pré-vol non fait ou armement consommé | `--preflight` puis `--armer` |
| `armement EXPIRE` | plus de 10 min | refaire l'armement |
| `empreinte de … differente` | un fichier du service a changé depuis le pré-vol | réinstaller la source de vérité |
| `StateDirectoryMode` / `permissions … au lieu de 700` | l'unité n'a pas donné 0700 | réinstaller ; ne jamais lancer (le Bridge refuserait) |
| `ORPHELINS` | reste d'un lancement précédent | § 8 (nettoyage manuel) |
| `cooldown local … NON ecoule` | dernier lancement < 5 min (30 min jusqu'au 17 nov. 2026) | attendre le délai indiqué dans le journal, puis **relancer la même commande** |
| `helios_incident{cible=k8s-worker1}` actif | incident disque en cours | attendre sa fin |
| `garde independante … non armee` | `helios-diskguard.service` n'a pas démarré, ou relance dans la seconde qui suit un refus (§ 12) | `journalctl -u helios-diskguard` ; relancer (l'armement n'est pas consommé par ce refus) |
| `Job for helios-diskfill.service failed …` (terminal) | `ExecStartPre` a refusé : c'est le comportement voulu quand une condition manque | `journalctl -u helios-diskfill -n 60` : lignes `[KO]` puis `RESULTAT : verdict=REFUS` |
| `RESULTAT : verdict=DETECTION_SEULE` / `AUCUNE_DETECTION` | HELIOS n'a pas remédié / détecté en 720 s | § 8 (tableau des verdicts) ; le disque a été libéré par le service |
| `[ROUGE]` du pré-vol hôte | RAM, gel < 1 h, flags, Bridge… | voir `RUNBOOK-DISK-E2E-158.md` § 4 et § 11 |

## 14. Retour arrière
Arrêt d'un lancement : `sudo systemctl stop helios-diskfill.service`. Retrait du service : `deploy_diskfill_service_158.py --retirer --confirm RETIRER-DISKFILL-158` (les répertoires `/var/lib/helios-diskfill*` ne sont pas touchés). Dépôt : tous les fichiers de ce runbook sont **nouveaux** (aucun commit) ; seul `RUNBOOK-DISK-E2E-158.md` a reçu un renvoi (§ 15) ; sauvegarde d'origine : `helios-state\diskfill-service-20261114\avant\` ; état de fin de l'étape 1 (avant la 2ᵉ passe) : `helios-state\diskfill-service-20261114\avant-etape2\`.

## 15. 2ᵉ passe (14 nov. 2026) — ce qui a changé, et ce qu'il reste à décider AVANT toute installation
**Changé (dépôt seulement ; rien installé, aucun service démarré, aucune VM jointe, aucune injection, aucun commit)** :
| Fichier | Modification |
|---|---|
| `infra/helios-sot/diskfill/diskfill.py` | ligne finale `RESULTAT : verdict=… code=…` (refus de `verifier`, verdict de `run`, `INTERROMPU` de `nettoyer`) ; un SIGTERM reçu **après l'échéance** donne `ERREUR` (échéance), plus `ARRET` ; l'attribution de la libération est conservée même si un arrêt survient juste après ; la garde **oisive** sort en quelques secondes après un refus (marqueur `garde.arret` posé par `nettoyer`, effacé à la création de l'état d'un lancement, ignoré s'il est plus ancien que la garde, si elle garde un jeton ou si le service principal est occupé) ; en-tête mis à jour |
| `helios-diskfill.service` | `SuccessExitStatus=6` (un `systemctl stop` laisse l'unité propre ; tout autre code non nul la laisse en `failed`) ; en-tête mis à jour |
| `helios-diskguard.service` | déclare **les deux** répertoires d'état (`helios-diskfill helios-diskfill-etat`), comme l'unité principale |
| `SHA256SUMS` | régénéré (`helios_diskfill_service.py --sommes`) |
| `scripts/helios-ops/helios_diskfill_service.py` | l'hôte affiche en plus : une seule commande de lancement, jamais la garde à la main, lecture du résultat final dans `journalctl` / `systemctl show` |
| `scripts/helios-ops/test_helios_diskfill_systemd.py` | **nouveau** : banc systemd + 59 tests (§ 11) |
| ce runbook | §§ 1 bis, 3, 4, 8 (résultat final, verdicts), 10 (hypothèses à confirmer), 11, 12, 13, 15 |

**Inchangé** : Bridge, Backend, BPMN, `Incident.java`, CPU, VM_DOWN, politiques, seuils, lanceur DISK validé (`helios_disk_test.py`), porte E2E (`helios_disk_e2e.py`), `deploy_diskfill_service_158.py`, les quatre suites existantes (254 tests, aucune modification), toutes les protections (aucune retirée : 81 mutations antérieures toujours détectées).

**À faire avant toute installation (rien de ceci n'a été fait)** :
1. **Relecture du diff par l'exploitant**, puis décision de commit (l'exploitant seul : aucun commit n'a été fait). Les empreintes de `SHA256SUMS` ont **changé** : l'installateur et le pré-vol les relisent, aucune valeur n'est codée ailleurs.
2. **Autorisation nominative** de l'installation (`deploy_diskfill_service_158.py --installer --confirm INSTALLER-DISKFILL-158`, jamais exécuté) ; d'abord sans `--confirm` (plan seulement).
3. **Autorisation distincte** de l'exercice non destructif du § 10 (`systemctl start` sans armement), qui confirme les hypothèses du banc.
4. **Autorisation distincte** de toute injection réelle (pré-vol vert + armement + `systemctl start`).
5. Décision facultative, **non prise** : une sonde de **disponibilité de Camunda/Zeebe** dans le pré-vol hôte (la chaîne Camunda → Bridge n'est contrôlée, aujourd'hui, que par la remédiation elle-même ; si Camunda est indisponible le scénario se termine en `DETECTION_SEULE`, disque libéré par le service). Elle exige une lecture réelle (SSH en lecture seule sur `.161`) pour choisir un point de contrôle fiable : impossible dans cette étape.

## 16. 3ᵉ passe (14 nov. 2026, soir) — installation réelle, exercice de refus réel, faux positif du point de montage corrigé
**Réalisé sur ordre de l'utilisateur (mission finale)** : installation de `.158` par l'installateur (`--installer --confirm INSTALLER-DISKFILL-158`, 13 vérifications indépendantes conformes) ; exercice de refus RÉEL du § 10 (REFUS code 2, aucune allocation) qui a révélé le défaut du § 6 (`[KO] DOSSIERS`, bind mount du `StateDirectory` dans l'espace de noms de l'unité) ; arrêt, rapport, puis **« continuer »** de l'utilisateur ⇒ correctif appliqué.
| Fichier | Modification de la 3ᵉ passe |
|---|---|
| `infra/helios-sot/diskfill/diskfill.py` | `_anomalies_volume(info, racine, chemin=None)` : montage admis = `/` **ou** `chemin` (le répertoire de test lui-même, transmis par `anomalies_dossier`) ; périphérique = celui de `/`, ext4/xfs inchangés (+7/−4) |
| `SHA256SUMS` | régénéré : `diskfill.py` `7222bc75fef0…` (les deux unités sont INCHANGÉES : `ca3139ff…`, `0f89c28c…`) |
| `test_helios_diskfill_coherence.py` | écart déclaré avec le lanceur + refus de tout autre montage / volume (+9) |
| `test_helios_diskfill_service.py` | cas `dossier_montage_autre` : prenait le répertoire LUI-MÊME comme « montage étranger » (hypothèse réfutée) → `/var/lib` ; + cas « autre volume monté dessus » ; + classe `TestRepertoireMonteEnBind` (+21/−1) |
| `test_helios_diskfill_systemd.py` | banc rendu **fidèle** (espace de noms de l'unité : le `StateDirectory` y est un point de montage, `+` compris) ; + classe `TestEspaceDeNomsDeLUnite` (4 tests) (+57/−11) |
Preuve d'équivalence : copie jetable (hors dépôt) — banc fidèle + ancien code = 40 échecs (défaut reproduit), banc fidèle + correctif = tout vert, 5 mutations de sur-permissivité tuées ; le changement appliqué reproduit octet pour octet le patch présenté (`helios-state\mission-disk-e2e-20261114\candidat-correctif-montage\`). Originaux sauvegardés : `helios-state\mission-disk-e2e-20261114\avant-correctif\`.
**Inchangé** : les deux unités systemd, le Bridge, le Backend, les BPMN, `Incident.java`, CPU, VM_DOWN, politiques, seuils, lanceur DISK validé, porte E2E, installateur, module d'armement, toutes les autres protections.

## 17. Scénario réel du service (14 nov. 2026, 22:24:30 local = 21:24:30 UTC) — UNE commande, `verdict=OK`
**Déroulé** : pré-vol complet sans ROUGE (RAM disponible 3 617-3 673 Mo, pages lues/s 0-120, `/` de `.158` à 68,75 %) → `helios_diskfill_service.py --armer --confirm DISKFILL-158-81-82-83 --image-backend pfe-backend:0.2.165-disk-e2e --accepte-orange GEL_HOTE_48H,E2E_INCIDENTS_DISQUE_ANCIENS` (22:24:18, nonce `ed65258d…`, 600 s, usage unique) → **`sudo -n systemctl start helios-diskfill.service`** (22:24:30, retour 0, la seule commande de lancement ; la garde n'a jamais été lancée à la main). Seuls les deux ORANGE du pré-vol ont été acquittés, par leur code ; aucun ROUGE n'a été abaissé.
| Moment (UTC) | Constat réel | Source |
|---|---|---|
| 21:24:30.126 → .134 | `Started helios-diskguard` **avant** `Starting helios-diskfill` (dépendance `Requires=`/`After=` confirmée) | journal systemd |
| 21:24:30.36 | `VERIFICATION DE DEMARRAGE` : tous `[OK]` (dont `DOSSIERS` ×4 avec le correctif du § 16, `GARDE : armee et libre`, `ARMEMENT : valide`) ; armement **consommé**, jeton `e4b73054…`, échéance 21:41:30 | journal du service |
| 21:24:32 | garde armée pour ce jeton **avant** la 1ʳᵉ allocation ; 10 blocs `fallocate` (9 × 512 Mo + 63 Mo = **4 671 Mo**) en < 1 s : 68,75 % → **81,00 %** (cible 81, plafond 82, urgence 83) | journal du service |
| 21:24:44 (22:24:44 local) | 1ᵉʳ échantillon Prometheus > 80 % | collecteur hôte |
| 21:25:04 | **Incident #12924 `NODE_DISK_FULL` créé** par le Backend (ingestion hôte, source `PROMETHEUS`) ; action `HOST_RECLAIM_DISK` créée par `HELIOS-DETERMINISTIC` ; « DISK remediation workflow confirmed by Camunda » ; instance Camunda **#1444** | base + journal du Backend |
| 21:25:05 → 21:25:07 | le **Bridge** (`.161`) lit le répertoire, journalise `DISQUE SUPPRESSION worker1 jeton e4b73054 \| 10 fichier(s) exact(s)`, exécute `sudo -n /usr/bin/rm -f -- <10 noms exacts>`, puis `DISQUE worker1 ok=True` ; action **#1557** `COMPLETED` / `AUTO` / `CAMUNDA_WORKER` (« 10 fichier(s) du jeton e4b73054 supprimé(s) (4671 Mo) ; 81,00 % -> 68,75 % ») | journal du Bridge, base |
| 21:25:06 → :08 | le service constate la libération **par un tiers** (« par : helios »), 2 mesures consécutives ≤ 77 % | journal du service |
| 21:25:08.80 | `RESULTAT : verdict=OK code=0 etape=run jeton=e4b73054 incident_vu=oui liberee_par=helios_infere usage_initial=68.75 usage_atteint=81.00 usage_final=68.75 recupere=oui liberation_verifiee=oui blocs=10 alloue_mo=4671 duree_s=38` | journal du service |
| 21:25:09 → :10 | `ExecStopPost` : « lancement déjà terminé et libéré » ; service `Deactivated successfully` (`Result=success`) ; la garde sort seule (« lancement terminé et libéré : fin du filet ») | journal systemd |
| 21:25:48 (22:25:48 local) | vérification **#922 PASS** (2 échantillons Prometheus consécutifs, 68,75 % ≤ 77 %) ⇒ incident **RESOLVED**, workflow #1444 `COMPLETED`/`RESOLVED` | base |
**Délais** : lancement → incident visible dans Prometheus : 34 s ; création → RESOLVED : **44 s** ; lancement → RESOLVED : 78 s. **Effets de bord** : 1 incident, 1 action, 1 workflow créés (rien d'autre) ; nœuds `Ready`, CNPG 2/2, etcd 5/5 ; `.158` à 68 %, répertoire de test **vide**, armement consommé, horodatage de cooldown écrit (30 min) ; 0 unité en échec.
**Ce que ce scénario confirme sur `.158` (systemd 255)** : le bac à sable `ProtectSystem=strict` laisse `fallocate` écrire dans le `StateDirectory` ; `MemoryMax=64M` n'a pas gêné ; `Type=simple` + `ExecStopPost` + `Result=success` ; la garde démarre avant, s'arme avant la 1ʳᵉ allocation et se retire seule ; une seule ligne `RESULTAT` ; contrat du Bridge `directory|root|700` accepté en réel (validation seule puis suppression exacte).
**Preuves** : `helios-state\mission-disk-e2e-20261114\06-injection\` (armement, commande, journal temps réel) et `07-preuves\` (collecteur hôte P1-P7 « tous démontrés », preuves causales, intégrité, SHA-256, Git). Rapport complet : `helios-state\mission-disk-e2e-20261114\RAPPORT-FINAL-COMPLET.md`.

## 18. Démarrage DIRECT (15-17 nov. 2026) — état actuel : UNE commande dans la VM, comme `helios-cpuload.service`
**Décision de l'exploitant** (15 nov., confirmée le 17 nov.) : `sudo systemctl start helios-diskfill.service` doit suffire **directement dans la VM `.158`**, comme le scénario CPU : **aucun armement** à déposer, **aucun pré-vol à exécuter avant**, **aucune dépendance de l'hôte Windows**.

### 18.1 Ce qui se passe au démarrage (aucun fichier ni service externe requis)
`ExecStartPre=+ … diskfill.py verifier` exécute **à chaque démarrage**, dans cet ordre, les contrôles du nœud ; **un seul en échec = refus**, `systemctl start` échoue dans le terminal, **rien n'est créé ni alloué** :
| Contrôle | Ce qui est exigé (refus sinon) |
|---|---|
| `INVOCATION` | exécution par une unité systemd |
| `IDENTITE` | **nom d'hôte `k8s-worker1` ET IP `192.168.148.158`** (cible exactement `.158`) |
| `INSTALLATION` | chemin du script ; **empreintes SHA-256 des 2 unités = celles figées dans le programme** (limites `RuntimeMaxSec`/`MemoryMax`/`TasksMax`, durcissement, `ExecStart*`, dépendances : tout le contenu) ; unités chargées, sans drop-in, `static`, `StateDirectoryMode=0700`, `Restart=no`, **`NeedDaemonReload=no`** |
| `DOSSIERS` | `/var/lib/helios-diskfill` : `directory`, `root:root`, `0700`, même volume que `/`, ext4/xfs, montage `/` ou lui-même (bind mount systemd), **vide** (orphelins = refus, aucun nettoyage automatique) ; répertoires d'état `0700` |
| `RESIDUS` | aucune unité transitoire du lanceur |
| `SYSTEME` | `/` en ext4/xfs, `fallocate` présent, **plan de remplissage dans les limites** (≤ 16 fichiers, ≤ 6 Gio, plafond 82 %, occupation de base ≤ 77 % et < 80 %), marge ≥ 2 points sous le seuil du kubelet |
| `INODES` | **occupation des inodes < 85 %** (règle `NODE_INODES_FULL` du détecteur) **et ≥ 1 024 inodes libres** |
| `COOLDOWN` | dernier lancement local il y a ≥ 5 min (30 min jusqu'au 17 nov. 2026 17:52 : § 18.7) |
| `PROMETHEUS` | 1 série pour la racine, écart ≤ 1 point avec la mesure du nœud, **aucun incident HELIOS actif** sur la cible |
| `GARDE` | garde **indépendante** démarrée par la dépendance, armée et libre |
| `HOTE_WINDOWS` | **non requis par défaut** (ligne d'information) ; voir § 18.3 |
Si tout est conforme : jeton, état et horodatage du cooldown sont créés, puis `run` remplit (limites, garde, durée max, nettoyage par noms exacts : **inchangés**, § 5).

### 18.2 Protections conservées / différences avec l'ancien démarrage
**Conservé** : paramètres figés 81/82/83/83,5/77, blocs 512 Mo, ≤ 16 fichiers, ≤ 6 Gio, 720 s/1 020 s ; `RuntimeMaxSec` ; `MemoryMax`/`TasksMax` ; `ProtectSystem=strict` ; urgence, garde indépendante, nettoyage par noms exacts ; contrôle du montage et du répertoire ; contrat du Bridge ; cooldown local (5 min depuis le 17 nov. 2026 17:52, § 18.7 ; 30 min avant) ; ligne finale `RESULTAT`. **Ajouté** : contrôle des inodes, empreintes des unités vérifiées par le nœud (remplace la liaison que faisait l'armement), `NeedDaemonReload=no`. **Supprimé** : `armer`, le fichier d'armement, son usage unique et son expiration (le cooldown local empêche un 2ᵉ remplissage rapproché).
**Perdu (déclaré)** : l'armement portait aussi l'**attestation du pré-vol de l'hôte** (RAM et pagination, gels VMware, état Backend/Bridge/Camunda, ORANGE acquittés) que le nœud ne peut pas voir. Le démarrage direct ne la consulte plus : **lancer la commande accepte ces risques** (comme pour le scénario CPU). `helios_diskfill_service.py --preflight --image-backend TAG` les affiche, à titre facultatif et en lecture seule. Sans HELIOS opérationnel, le service libère lui-même à la fin de l'observation (`DETECTION_SEULE` / `AUCUNE_DETECTION`) : le disque ne reste jamais plein.
**L'exercice non destructif « refus sans armement » (§ 10) n'existe plus** : un `start` sans armement ne refuse plus. Preuve non destructive d'une installation : `sudo python3 -I -B /opt/helios-diskfill/diskfill.py verifier --a-blanc` (aucun effet : rien écrit, créé, alloué ni contacté).

### 18.3 Attestation de l'agent Windows DISK : OPTIONNELLE (code conservé)
Le 15 nov., un jeu de modifications a ajouté un **agent Windows DISK** (`scripts/helios-ops/helios_disk_host_agent.py`, `install_helios_disk_host_agent.ps1`, HMAC sur `192.168.148.1:19158`) dont l'attestation était **obligatoire** à chaque démarrage (VERT, ou ORANGE limité à `GEL_HOTE_48H` et `E2E_INCIDENTS_DISQUE_ANCIENS`) ; il a été installé sur `.158` le 15 nov. (clé `/etc/helios-diskfill/host-agent.key`) mais l'agent n'était **pas démarré** (tâche planifiée `Ready`, port 19158 fermé, RAM de l'hôte 1,2 Go) : la commande unique était donc **refusée**. Le 17 nov., pour respecter « comme le scénario CPU », la constante **`ATTESTATION_HOTE_REQUISE = False`** (`diskfill.py`, codée en dur) rend l'attestation **optionnelle**. Tout le code et les tests de l'agent sont conservés ; la clé sur `.158` et la tâche Windows ne sont pas touchées. **Pour la rendre de nouveau obligatoire** : `ATTESTATION_HOTE_REQUISE = True`, régénérer `SHA256SUMS` (`helios_diskfill_service.py --sommes`), réinstaller (`deploy_diskfill_service_158.py --installer --confirm INSTALLER-DISKFILL-158`), démarrer l'agent Windows avant chaque essai (échec fermé si absent).

### 18.4 Déploiement (17 nov. 2026) et retour arrière
Installateur existant : sauvegardes `*.AVANT-DISKFILL-<UTC>` à côté des fichiers remplacés, SHA-256 vérifiés avant/après, `systemd-analyze verify`, `daemon-reload` (**jamais** `enable`/`start`/`restart`), retour arrière au moindre écart. **Retour arrière manuel** : réinstaller depuis les sauvegardes `*.AVANT-DISKFILL-20261117T…Z` (`install -m 0644` des 3 fichiers, `daemon-reload`). Aucun service n'est démarré par le déploiement ; **le scénario n'a pas été lancé**.

### 18.5 Tests (17 nov. 2026)
Service 124, cohérence 62, hôte 50, installateur 36, banc systemd 62 (tous verts) ; non-régression : lanceur et porte E2E ont des échecs **préexistants** (fichiers modifiés le 15 nov. par le patch « orange v2 », tests non mis à jour : `test_helios_disk_test.py` 3 échecs + 1 erreur, `test_helios_disk_e2e.py` 1 échec déterministe + 1 erreur intermittente d'un test HTTP local), Bridge disque 49/49 ; agent Windows 13/13 (hors `-I`). **Mutations : 99/99 tuées** (`helios-state\direct-start-20261115\03-mutations\mutations-resultats-FINAL.txt`). **Déployé sur `.158` le 17 nov. à 17:12** (`diskfill.py` `42bd980c…`, unité `6cd2ae20…`) ; **premier démarrage réel : § 18.6**.

### 18.6 Premier démarrage direct RÉEL (17 nov. 2026, 17:28:10 local, lancé par l'utilisateur sur `.158`) — `verdict=OK`
Commande : `sudo systemctl start helios-diskfill.service` (aucun armement, aucun agent Windows : attestation « NON requise »). Chronologie (heure locale, T = début du service) : T+0 tous les contrôles du nœud `[OK]` (dont `INODES` 11 %, `INSTALLATION` : SHA des 3 fichiers) → T+1 s 10 blocs, 4 647 Mo, 68,81 → **81,00 %** → T+26 s le Bridge détecte `NODE_DISK_FULL` → T+53 s `helios_incident{cible=k8s-worker1}=1` dans Prometheus → T+73 s incident **#13188** (source PROMETHEUS) → T+77 s action **#1634 `HOST_RECLAIM_DISK`** `COMPLETED`, mode `AUTO`, par `CAMUNDA_WORKER` : le Bridge supprime les 10 fichiers **par noms exacts** (journal du Bridge + `rm -f --` dans le journal `sudo` de `.158`) → T+79 s retour à 68,81 % confirmé, `RESULTAT verdict=OK code=0` → T+118 s vérification **#1004 PASS**, incident **RESOLVED** (45 s après sa création ; ≈ 2 min après la commande). Workflow Camunda #1521 `COMPLETED`. Aucun filet du service n'a tiré. Effets de bord : 1 incident, 1 action, 1 vérification, 1 workflow ; aucun autre incident créé depuis.
**Les deux relances suivantes (17:32:43 et 17:32:49) ont été REFUSÉES — comportement voulu** : `COOLDOWN` « encore 25 min 28 s » puis « 25 min 22 s » ; `RESULTAT verdict=REFUS code=2 … alloue=non`, rien créé ni alloué, la garde se termine aussitôt, `nettoyer` ne trouve rien. Le cooldown de 30 min court depuis le **début** du dernier lancement (ici : prochain démarrage à **17:58:11**) et n'est pas prolongé par un refus. systemd n'affiche que « control process exited with error code » : la raison est dans `systemctl status helios-diskfill --no-pager -l` ou `journalctl -u helios-diskfill -n 30 -o cat`.
**Constats** : (1) l'unité est `Type=simple` : `systemctl start` **revient en ≈ 1 s sans message** quand tout va bien, le scénario (≈ 80 s) se déroule en arrière-plan — résultat : `journalctl -u helios-diskfill --since "-10 min" -o cat | grep -E "RESULTAT|BILAN"` ; (2) après un refus l'unité reste `failed` : cosmétique, sans effet sur le démarrage suivant, effacée par un démarrage accepté ou `systemctl reset-failed helios-diskfill.service` ; **aucune alerte ni incident** n'en découle (`.158` n'exporte pas `node_systemd_unit_state`, la règle `SystemdUnitFailed` du dépôt n'est pas chargée dans le Prometheus de `.160`, le type `SYSTEMD_UNIT_FAILED` n'existe ni dans le détecteur ni dans le Backend — vérifié) ; (3) `helios-cpuload.service` (`Type=simple`, aucune règle de relance dans `cpuload.py`) n'a pas de cooldown, le service DISK avait gardé le sien (D3, § 9) : **ramené de 30 à 5 min à la demande de l'utilisateur, voir § 18.7** (repères : lancement → RESOLVED = 119 s ; cooldown du Backend = 2 min ; le local doit rester supérieur). État de `.158` après coup : `/` 68 %, répertoire de test vide, 0 orphelin, SHA installés inchangés. Preuves : `helios-state\direct-start-20261115\06-lancement-utilisateur\` (`RAPPORT-LANCEMENT-REEL-17NOV.md`, `SHA256-06.txt`).

### 18.7 Cooldown local ramené de 30 à 5 min (17 nov. 2026, installé à 17:52 local, à la demande de l'utilisateur)
**Problème** : la commande unique `sudo systemctl start helios-diskfill.service` était **refusée pendant 30 min** après chaque lancement (`[KO] COOLDOWN` ; systemd : « control process exited with error code »), alors que `helios-cpuload.service` n'a pas de cooldown ; l'utilisateur s'y est heurté 3 fois (17:32:43, 17:32:49, 17:44:33). Tous les autres contrôles étaient `[OK]`.
**Correction** : une constante dans `diskfill.py` (`COOLDOWN_LOCAL_S = 5 * 60`, ex-`30 * 60`) et un message de refus explicite (« relancer la même commande ensuite : un 2ᵉ lancement plus rapproché ne serait pas remédié par HELIOS »). **Pourquoi 5 min et pas 0** : le cooldown protège la **démonstration**, pas le disque (que protègent les limites propres à chaque lancement : 81/82/83/83,5 %, garde, 720/1 020 s, nettoyage par noms exacts, inchangées). HELIOS doit remédier chaque essai ; or le Backend applique un cooldown de **2 min** (`REMEDIATION_COOLDOWN_MINUTES`, `backend/backend/infra/k8s/backend-configmap.yaml`) : sans cooldown local, un 2ᵉ lancement rapproché ne serait pas remédié (seul le filet du service libérerait les fichiers en fin d'observation). Invariant **verrouillé par test** (`test_helios_diskfill_coherence.py`) : cooldown local ≥ cooldown du Backend de la ConfigMap du dépôt + un lancement complet (≈ 2 min : 78 s le 14 nov., 119 s le 17 nov.) + 1 min de marge = **5 min** ; si le Backend dépasse 2 min, le test échoue (réaligner). Mesure : le signal `helios_incident` n'est resté actif que 45 s dans Prometheus, il n'allonge pas l'attente. Un refus ne prolonge pas le cooldown (test).
**Compromis déclaré** : le DÉFAUT du code Backend (30 min, appliqué seulement si la clé est absente de la ConfigMap) n'est plus couvert ; le pré-vol facultatif de l'hôte (`helios_diskfill_service.py --preflight`) le signale alors ROUGE `SVC_COOLDOWN_INFERIEUR_BACKEND` ; en production la clé vaut 2 min.
**Inchangé** : tous les autres contrôles et limites, les 2 unités (SHA `6cd2ae20…` / `0f89c28c…`, donc `SHA_UNITES`). **Déployé** : `diskfill.py` `82c55ef9db5f…` (avant : `42bd980c…`) ; sauvegardes sur `.158` : `*.AVANT-DISKFILL-20261117T165238Z` ; **retour arrière** : réinstaller depuis ces sauvegardes (`install -m 0644`, `daemon-reload`) ou remettre `30 * 60`, `helios_diskfill_service.py --sommes`, `deploy_diskfill_service_158.py --installer --confirm INSTALLER-DISKFILL-158`. **Tests** : service 127, cohérence 62, hôte 50, installateur 36, banc systemd 62, agent 13 ; **mutations : 103/103 tuées** (99 d'avant + 4 nouveaux, dont 8 liés au cooldown). Preuves : `helios-state\direct-start-20261115\07-cooldown-5min\`.
**Vérifié en réel (2ᵉ lancement direct, 17 nov. 2026 17:58:03 local, par l'utilisateur)** : accepté avec `cooldown local de 5 min ecoule` (l'ancien cooldown de 30 min l'aurait encore refusé : ≈ 7 s restantes), `RESULTAT verdict=OK code=0`, incident **#13189** → action **#1635** `HOST_RECLAIM_DISK` (AUTO, `CAMUNDA_WORKER`) → vérification **#1005 PASS** → **RESOLVED en 44 s** ; `.158` revenue à 68 %, répertoire de test vide (`helios-state\direct-start-20261115\08-second-lancement-5min\`). **Où voir la preuve dans la VM** : le **journal systemd** (le service n'écrit pas dans le terminal ; la VM est en **UTC**, 17:58 locale = 16:58 UTC) : `sudo journalctl -u helios-diskfill -u helios-diskguard --since "2026-11-17 16:58:00" --until "2026-11-17 17:00:00" --no-pager -o short-precise` ; verdicts de tous les lancements : `sudo journalctl -u helios-diskfill -o cat --no-pager | grep RESULTAT` ; suppression par HELIOS (Bridge) : `sudo journalctl --since "<début>" --until "<fin>" -o short-precise | grep "sudo.*rm -f"`. ⚠️ Journal plafonné à 200 Mo (`SystemMaxUse=200M`) = **≈ 19 h de couverture** le 17 nov. : sauvegarder la preuve (`… > ~/preuve-disk-AAAAMMJJ.txt`) ; la preuve du 14 nov. n'est déjà plus dans la VM.

### 18.8 Voir le scénario EN DIRECT dans la VM (équivalent du `top -d 1` du CPU) — 17 nov. 2026
`top` ne montre rien ici : `fallocate` réserve l'espace sans CPU ni écriture ; la grandeur utile est l'occupation de `/`. **Terminal 1, lancé AVANT** (le « top » du disque) : `watch -n 1 -d df -h /` (`-d` surligne ce qui change). Déroulé attendu : T+0 68 % → **T+2 s 81 %** (hausse quasi instantanée) → ≈ 85 s à 81 % → HELIOS libère → retour à **68 %**. **Vue complète** (heure, `/`, fichiers de test, état du service, 6 dernières lignes du journal dont « fichiers de test liberes … par : helios ») : `sudo watch -n 1 -d 'date +%T; echo; df -h / | tail -n 1; echo; echo "fichiers de test :"; ls -lh /var/lib/helios-diskfill | tail -n +2; echo; echo "service : $(systemctl is-active helios-diskfill)"; echo; journalctl -u helios-diskfill -n 6 -o cat --no-pager | sed "s/^helios-diskfill : //" | cut -c1-120'`. **Une ligne par seconde, conservable comme preuve horodatée** : `sudo bash -c 'while true; do echo "$(date +%T)  /: $(df -h --output=used,pcent / | tail -n 1)  fichiers=$(ls /var/lib/helios-diskfill | wc -l)  service=$(systemctl is-active helios-diskfill)"; sleep 1; done' | tee ~/disk-live.log`. **Terminal 2** : `sudo systemctl start helios-diskfill.service`. Ctrl+C pour quitter. ⚠️ **Un SEUL `sudo` pour toute la surveillance** (`sudo watch …`, `sudo bash -c 'while …'`) : un `sudo` par seconde écrirait une ligne de journal par seconde, noierait les traces `sudo` du Bridge (preuve de la suppression par HELIOS) et userait le journal plafonné à 200 Mo. Les 3 commandes ont été testées sur `.158` (état de repos, sans lancement ; `watch` sous pseudo-terminal).
