# Runbook — tester la chaîne DISK d'HELIOS (`NODE_DISK_FULL`) par un remplissage contrôlé d'un worker

> **MISE À JOUR 14 nov. 2026 — `.158` = scénario `DISK_E2E_158`.** Pour la cible `.158`, `--preflight` et `--go` passent désormais **obligatoirement** par la porte du scénario (`helios_disk_e2e.py`) : option **`--image-backend <TAG>` requise** (image du Backend qui doit tourner), collecte réelle en lecture seule du Bridge, du Backend, des drapeaux (`HOST_DISK_E2E_158_ENABLED`), de la protection systemd, du cooldown, des incidents ouverts, de la RAM/pagination de l'hôte ; **aucun lancement en détection seule** (`--accepte-orange REMEDIATION_NON_ACTIVEE` et `--accepte-risque-primaire-cnpg` y sont refusés). Les passages ci-dessous sur `REMEDIATION_NON_ACTIVEE` / `HOST_DISK_REMEDIATION_ENABLED` ne valent donc plus que pour **`.155`**. Voir `RUNBOOK-DISK-E2E-158.md` (référence pour `.158`).

*Préparé le 13 nov. 2026 (étape 2), **corrigé le 13 nov. 2026 après la revue de sécurité** (B1-B3, M2-M4 : § 12). **Statut : PRÉPARÉ, JAMAIS EXÉCUTÉ contre l'infrastructure.** Seuls la simulation, les 187 tests unitaires (hors réseau, hors VM) et 94 mutations sur copies de l'outil ont tourné ; le programme de la garde a aussi été joué avec `awk` en local sur des données synthétiques. Aucun remplissage, aucune suppression, aucun déploiement, aucune commande ssh / kubectl / Prometheus / PostgreSQL / systemd réelle. Un lancement réel exige une autorisation écrite NOMINATIVE distincte (cible + acte), et le choix de la cible reste en attente (§ 8).*

## 1. Les commandes (toutes depuis `scripts/helios-ops`)
```
python helios_disk_test.py --target 158                          # SIMULATION (défaut) : ni réseau, ni ssh, ni VM, ni fichier
python helios_disk_test.py --target 155 --accepte-risque-primaire-cnpg   # idem ; sans l'option, .155 est REFUSÉE (§ 8)
python helios_disk_test.py --target 158 --preflight              # PRÉ-VOL réel, LECTURE SEULE (ssh de lecture, Prometheus, kubectl get, SELECT) — GO distinct requis
python helios_disk_test.py --target 158 --go --confirm DISK-158-81-82-83 [--accepte-orange CODE[,CODE]]   # LANCEMENT RÉEL — GO nominatif requis (§ 10)
```
La cible est **obligatoire et explicite** (`155` ou `158`) ; `.153 .160 .161 .162` sont refusées. `--go` exige le texte exact `--confirm DISK-<cible>-81-82-83` (affiché par la simulation), un pré-vol sans ROUGE **et chaque ORANGE acquitté explicitement, code par code** (`--accepte-orange`, codes au § 4 ; le risque CNPG n'a que son propre drapeau). Les options ne peuvent pas être abrégées (`--accepte` est refusé). Codes de sortie : `0` OK · `3` DETECTION_SEULE · `1` AUCUNE_DETECTION / URGENCE / REFUS / ERREUR / pré-vol ROUGE / ORANGE non acquitté · `2` usage refusé (cible, paramètre, `--confirm`, code d'acquittement).

## 2. Paramètres validés (on peut les resserrer, jamais les desserrer — le code les verrouille)
| Paramètre | Valeur | Pourquoi |
|---|---|---|
| Détection (règle `NODE_DISK_FULL`) | `> 80 %` | inchangée ; occupation = `(1 − disponible / taille) × 100` de `/`, même formule que le détecteur |
| **Cible** | **81 %** | ≥ 0,3 point au-dessus de la détection (borne basse 80,3 %) |
| **Plafond strict** | **82 %** | jamais dépassé par une allocation : mesure fraîche + garde AVANT chaque bloc |
| **Arrêt d'urgence** | **83 %** | libération immédiate ; marge ≥ 2 points sous le **85 %** du kubelet (DiskPressure ET ramasse-miettes d'IMAGES : air-gap, images non retéléchargeables) |
| **Garde côté nœud** | **83,5 %** (urgence + 0,5 point) | libère sans l'outil ; reste ≥ 1,5 point sous le 85 % du kubelet |
| **Récupération** | **≤ 77 %** | la base du nœud doit déjà être ≤ 77 % (sinon pré-vol ROUGE : récupération impossible) |
| Blocs / limites | 512 Mo (64-1 024), ≤ 16 fichiers, **≤ 6 Gio CUMULÉS, appliqués dans la boucle** | un fichier = un `fallocate` |
| Observation / expiration | 720 s (60-1 800) / observation + 300 s (**≥ observation + 240 s**, ≤ 3 600 s) | **budget d'allocation = expiration − observation − 120 s (180 s par défaut), compté depuis l'armement RÉEL du minuteur** ; l'observation et la libération doivent tenir avant l'échéance la plus proche du minuteur (marge de fin 120 s) |
| Surveillance | occupation mesurée **toutes les 5 s**, incident lu toutes les 15 s, **remesure juste après chaque appel lent** | une écriture externe est vue en ≤ 5 s ; la garde couvre les périodes où l'outil est aveugle |

## 3. Garanties (chacune verrouillée par un test)
- **Simulation par défaut** : aucun accès réseau / ssh / VM / fichier ; `Dependances` n'est même pas construite.
- **Identité** : le nom d'hôte répondu en ssh, le nom du nœud Kubernetes et son InternalIP doivent concorder avec la cible — au pré-vol, puis le nom d'hôte est **relu au lancement, avant toute écriture** (une IP réattribuée inverserait la porte CNPG).
- **Un seul répertoire** : `/var/lib/helios-diskfill`, fichiers `helios-diskfill-<jeton 32 hex>-NNN.bin` créés par `fallocate` (extents NON écrits). **Son volume réel est vérifié** : même périphérique et même point de montage que `/` (le volume que mesurent ssh et Prometheus), ext4 ou xfs, vrai répertoire (pas un lien), `root:root`, `0700` — d'abord sur le parent `/var/lib` si le répertoire est absent, puis sur le répertoire réellement créé. **Aucun nettoyage général** (`/tmp`, logs, données, images) ; jamais `rm -r`, `find`, `dd`, `truncate`, joker, `crictl`.
- **Suppression = noms exacts du lancement en cours**, après `--` : `rm -f -- <chemins exacts>`. Un résidu d'un autre lancement dans le répertoire ⇒ REFUS sans y toucher.
- **Avant CHAQUE allocation** : mesure fraîche, usage < urgence, usage < cible, usage après allocation ≤ plafond, **total cumulé ≤ 6 Gio**, **délai restant suffisant**. **Après chaque allocation** : nouvelle mesure ; l'espace disponible doit avoir baissé d'au moins **90 %** de la taille allouée (sinon REFUS : les fichiers ne sont pas sur le volume mesuré) ; urgence contrôlée aussitôt.
- **Deux filets côté nœud, armés AVANT la première allocation et vérifiés actifs (sinon rien n'est alloué)** : (1) le **minuteur d'expiration** (`helios-diskfill-expire-<jeton>.timer`, `rm -f --` des 16 noms exacts) ; (2) la **garde** (`helios-diskfill-garde-<jeton>.service`) : `/usr/bin/awk` avec un programme à **modèle FIXE** (pas de `sh -c`, pas de `$`, pas de `%`) qui lit `df -P -k /` toutes les 2 s et retire les 16 noms exacts dès que l'occupation atteint 83,5 % ; vie bornée par `RuntimeMaxSec` = expiration + 60 s. Ils libèrent même si l'outil est mort, ralenti ou coupé du réseau.
- **Libération vérifiée** (relecture du répertoire, 3 essais) ; le minuteur, la garde et le répertoire ne sont désarmés / retirés **qu'après** libération vérifiée. Sinon : alerte « LIBERATION IMPOSSIBLE », les DEUX filets restent armés.
- **Attribution de la libération** : « HELIOS » n'est crédité que si la garde ET le minuteur sont encore actifs et que l'échéance n'est pas atteinte ; garde tirée ⇒ verdict `URGENCE` ; minuteur tiré pendant l'observation ⇒ `ERREUR` (test invalide) ; état illisible ⇒ « indéterminée », jamais `OK`.
- **Échec sûr** : tout fait absent ou illisible (ssh, Prometheus, kubectl, base, kubelet, journaux VMware, VMDK, RAM, `C:`, nom d'hôte, volume du répertoire, drapeau de remédiation) ⇒ ROUGE ⇒ refus. **Une sonde illisible en cours de test (Prometheus, base)** ⇒ `ERREUR` + libération, jamais « aucun incident ». Tout `finally` (erreur, urgence, Ctrl+C) libère.
- **ORANGE = refus tant qu'il n'est pas acquitté code par code** (`--accepte-orange`) ; le risque CNPG se règle par `--accepte-risque-primaire-cnpg` uniquement.
- **Lecture seule côté cluster** : `kubectl get` / `kubectl exec … psql` pour des `SELECT` uniquement ; `systemctl` limité à `is-active` et `stop` de CES deux unités ; `/usr/bin/awk` n'est lancé que par la garde.
- **Volatilité** : les deux filets sont des unités transitoires (`/run/systemd/transient`) : **perdus si le nœud redémarre** — les fichiers restent alors, sans filet (§ 6).

## 4. Pré-vol (ROUGE = refus, ORANGE = à acquitter pour `--go`, VERT)
| Porte | ROUGE si… | ORANGE si… |
|---|---|---|
| Nœud | injoignable ; `stat -f` illisible ; fs ≠ ext4/xfs ; `fallocate` absent ; répertoire de test non vide ou illisible | — |
| **Identité** | nom d'hôte ssh ≠ nœud Kubernetes ; InternalIP Kubernetes ≠ IP ssh ; nom Kubernetes lu ≠ attendu ; l'un des trois illisible | — |
| **Volume du répertoire** | existant : pas un répertoire (lien) / propriétaire ≠ root:root / droits ≠ 0700 / périphérique ≠ celui de `/` / fs ≠ ext4-xfs / point de montage ≠ `/` ; absent : le parent `/var/lib` n'est pas sur le volume de `/` ; état de `/` ou du répertoire illisible | — |
| Occupation de base | ≥ 83 % ; ≥ 80 % (incident naturel) ; **> 77 %** ; remplissage > 6 Gio | — |
| Kubelet | seuils inconnus ; marge < 2 points sous le déclenchement | — |
| **Prometheus** | ≠ 1 série pour `/` ; écart > 1 point avec le nœud ; `helios_incident` actif ; **toute requête illisible** (HTTP, JSON, statut ≠ success, valeur non numérique) | — |
| Cluster | nœuds Ready < total ; cible non Ready ; DiskPressure actif ou inconnu | — |
| CNPG | rôle/santé inconnus ; non sain ; **primaire sur la cible sans acquittement explicite** | primaire sur la cible, risque accepté par `--accepte-risque-primaire-cnpg` (`PRIMAIRE_CNPG_ACCEPTE`) |
| Base `pfe_db` | incident disque actif ou de < 10 min (le backend le corrélerait) ; `NODE_NOT_READY` < 30 min | `NODE_NOT_READY` < 2 h (`NOTREADY_2H`) |
| Remédiation | **drapeau `HOST_DISK_REMEDIATION_ENABLED` illisible** | drapeau absent / `false` (`REMEDIATION_NON_ACTIVEE`) : le test ne validera que la DÉTECTION |
| Hôte Windows | RAM disponible < 1 500 Mo ; `C:` libre < 25 Go ; journaux `vmware.log` illisibles ; gel VMware ≥ 60 s dans l'heure ; VMDK avec parent ou snapshot | gel VMware < 48 h (`GEL_HOTE_48H`) ; type de VMDK inattendu (`VMDK_INATTENDU`) |

Codes à passer à `--accepte-orange` (la simulation et le pré-vol affichent la ligne exacte) : `REMEDIATION_NON_ACTIVEE`, `GEL_HOTE_48H`, `NOTREADY_2H`, `VMDK_INATTENDU`. `PRIMAIRE_CNPG_ACCEPTE` n'est PAS acquittable par cette option.

## 5. Chronologie attendue (exemple `.155` : 70,47 % → 81,0 % = +4 016 Mo = 8 blocs)
| Temps | Événement |
|---|---|
| avant | pré-vol réel (lecture seule) ; références `C:` et extents VMDK relevées ; ORANGE relus et acquittés |
| T+0 | identité relue (nom d'hôte) ; volume et droits du répertoire vérifiés ; répertoire créé puis revérifié ; **minuteur puis garde armés et vérifiés actifs** : c'est de cet instant que se compte le budget (180 s) |
| ≈ 1 min | 8 blocs : mesure fraîche → garde (plafond, cumul, délai) → `fallocate` → mesure d'effet (≥ 90 %) → urgence ; 80 % franchi au dernier bloc ; cible 81,00 % |
| ≤ ≈ 105 s après 80 % | détecteur 30 s + scrape 15 s + poll d'ingestion 60 s ⇒ incident `NODE_DISK_FULL` (source PROMETHEUS) |
| ensuite | **si la remédiation est câblée** : action, libération par HELIOS, vérification ≤ 77 %, `RESOLVED` ⇒ verdict `OK`. **Sinon** (état du 13 nov.) : l'incident reste ouvert jusqu'à la fin de la fenêtre ⇒ `DETECTION_SEULE` |
| fin au plus tard à min(T+720 s ; échéance du minuteur − 120 s) | l'outil libère lui-même (noms exacts), vérifie, désarme minuteur et garde, retire le répertoire |
| T+17 min (1 020 s) | filet : le minuteur libère si l'outil est mort ; la garde libère à tout moment dès 83,5 % |

## 6. Si quelque chose tourne mal
| Situation | Ce que fait l'outil / ce qu'il faut faire |
|---|---|
| Occupation ≥ 83 % (croissance externe, ou saut pendant l'allocation) | `ARRET D'URGENCE` : plus aucune allocation, libération immédiate, vérification |
| Occupation > 82 % après la dernière allocation | idem (plafond), avant tout suivi |
| **Garde côté nœud déclenchée** (verdict `URGENCE`, « GARDE COTE NOEUD DECLENCHEE ») | l'occupation a atteint 83,5 % sans que l'outil réagisse à temps (ssh lent, outil mort) : fichiers retirés par la garde ; identifier l'écriture externe avant tout nouveau test |
| **Allocation sans effet mesuré** (`REFUS`) | les fichiers ne sont pas sur le volume mesuré, ou `fallocate` est sans effet : libération ; vérifier `findmnt -T /var/lib/helios-diskfill` ; ne pas relancer sans comprendre |
| **Délai insuffisant** (`REFUS`) | ssh trop lent pour tenir dans le budget : libération ; relancer plus tard, ou augmenter `--expiry-s` (≤ 3 600) |
| **Sonde illisible** (`ERREUR`) | Prometheus ou PostgreSQL illisible pendant le suivi : libération et arrêt ; vérifier `kubectl exec … psql` et Prometheus avant de relancer |
| **Minuteur tiré pendant l'observation** (`ERREUR`) | horloge du nœud ou lenteur anormale : test invalide ; les fichiers ont été retirés par le minuteur |
| Erreur ssh / mesure illisible en cours de route | verdict `ERREUR`, libération, arrêt |
| Libération impossible depuis l'outil | alerte `!!! LIBERATION IMPOSSIBLE`, **minuteur ET garde laissés armés** : ils retirent les 16 noms exacts. À la main (noms exacts, jamais de joker) : `ls -A /var/lib/helios-diskfill` · `sudo rm -f -- /var/lib/helios-diskfill/helios-diskfill-<jeton>-001.bin …` · `sudo rmdir /var/lib/helios-diskfill` · puis `sudo systemctl stop helios-diskfill-expire-<jeton>.timer helios-diskfill-garde-<jeton>.service` |
| **Nœud redémarré pendant le test** (relance VM_DOWN, panne) | les DEUX filets sont perdus (volatils) ; les fichiers restent, sans filet : même procédure à la main ; le pré-vol suivant est ROUGE tant que le répertoire n'est pas vide |
| `DETECTION_SEULE` laisse un incident `NODE_DISK_FULL` OUVERT | la clôture est une **écriture en base : autorisation distincte** ; l'outil ne la fait jamais. Tant qu'il reste actif, le pré-vol suivant est ROUGE |
| `AUCUNE_DETECTION` | lire `python3 /opt/helios/detecter.py` sur `.160` (`type`, `matches`), Prometheus (`node_filesystem_avail_bytes`), `helios_incident` ; ne pas relancer sans comprendre |

## 7. Risque pour le disque de l'hôte (`C:` à 92 %) — vérifié à l'étape 1 (lecture seule)
Les 6 VM sont en `twoGbMaxExtentSparse` (disque **croissant**, 11 extents, autonomes, 0 snapshot). `fallocate` sur ext4 (extents, sans `discard`, LVM linéaire) **n'écrit aucune donnée** : croissance attendue des extents ≈ 0 (quelques Mo de métadonnées). Pire cas **si des données étaient réellement écrites** (ce que l'outil n'a pas le droit de faire : jamais `dd`) : ≤ ≈ 4 Go, borné par l'espace non alloué (`.155` 7,07 Gio, `.158` 7,49 Gio), **irréversible** (la suppression dans l'invité ne réduit pas le vmdk ; compactage interdit) + rafale d'écriture sur le SSD (candidat des gels d'hôte). Contrôles : `C:` libre ≥ 25 Go (référence 36,10 Go le 13 nov. 16:03) ; extents alloués relevés avant/après (> 50 Mo ⇒ alerte au bilan) ; références dans `C:\Users\houss\helios-state\disk-preflight-20261113\`.

## 8. Choix de la cible — EN ATTENTE (le risque CNPG n'est jamais présumé accepté)
| | `.155` (`k8s-worker2`) | `.158` (`k8s-worker1`) |
|---|---|---|
| Rôle CNPG | **héberge le primaire `postgres-8`** (1,4 Go) + PVC postgres-5, postgres-9, postgres-backups-b sur le même `/` | réplique `postgres-7` |
| Occupation | 70,47 % (+4 016 Mo pour 81 %) | ≈ 66,9 % (Prometheus) : ≈ +5,4 Go à remplir, proche du cumul de 6 Gio |
| Fragilité | marge avant le 85 % du kubelet : 1 525 Mo à 81 %, 763 Mo à 83 % | VM la plus fragile : 46 % résidente en RAM, `NODE_NOT_READY` le 13 nov. à 12:24 et 15:42 |
| L'outil | **REFUSE** sans `--accepte-risque-primaire-cnpg` ; avec : pré-vol ORANGE | accepte (le pré-vol reste ROUGE si le nœud ou CNPG est instable) |
La décision (et l'éventuelle acceptation écrite du risque) appartient à l'exploitant.

## 9. Ce que le test valide — et ce qu'il ne validera PAS tant que rien n'est câblé
Valide : `fallocate` → node_exporter → Prometheus (15 s) → détecteur `.160` (30 s) → `helios_incident` → poller backend (60 s) → incident `NODE_DISK_FULL`. Valide la **remédiation** seulement si elle est câblée. Audit du 13 nov. (tout inchangé) :
| Verrou | État |
|---|---|
| `HOST_DISK_REMEDIATION_ENABLED` (défaut `false`) absent du ConfigMap `backend-config` | remédiation non démarrée ⇒ `DETECTION_SEULE` attendu (ORANGE `REMEDIATION_NON_ACTIVEE` à acquitter) |
| PodSecurity `enforce=baseline` sur `pfe` vs pod `hostPID` privilégié du backend | rejet d'admission probable, jamais re-testé |
| Action du backend = `journalctl --vacuum-size=200M` (journald déjà plafonné à 200 Mo) | ≈ 0 Mo libérés ; le Bridge `_DISK` n'est appelé par personne |
| Vérification : récupéré si ≤ 85 % (défaut) alors que détection > 80 % | à aligner (D4 : ≤ 77 %) |
Décisions déjà prises : D1 = A (journal + répertoire de test seulement), D2 = Bridge, D4 = seuils avec marge. Chaque changement de production (ConfigMap + redémarrage du backend, Bridge, PodSecurity) = **GO nominatif distinct**, hors de cette étape.

## 10. Autorisations nécessaires, dans l'ordre
1. `--preflight` : GO distinct (lecture seule, mais touche l'infrastructure).
2. Choix de la cible ; si `.155` : acceptation écrite du risque CNPG.
3. **Notes de conception à valider** : les deux filets sont des unités systemd **TRANSITOIRES** (`systemd-run`) ; systemd écrit des fichiers volatils sous `/run/systemd/transient` (tmpfs) — **seule dérogation** à « fichiers uniquement dans le répertoire dédié ». La garde exécute `/usr/bin/awk` avec un programme fixe : c'est le seul interpréteur du jeu de commandes (`system()` d'awk lance `df`, `rm -f --` de noms exacts, `sleep`). **Jamais éprouvé contre un vrai systemd** : prérequis `/usr/bin/awk`, `df`, `systemd-run` sur le nœud (si une unité n'est pas active, l'outil refuse et n'alloue rien) ; recommandation : valider les deux unités sur une VM jetable avant le premier lancement sur un worker.
4. Pré-vol sans ROUGE (le 13 nov. : RAM de l'hôte ≈ 1 000 Mo < 1 500 Mo ⇒ ROUGE) et chaque ORANGE relu avant de l'acquitter.
5. `--go --confirm DISK-<cible>-81-82-83 [--accepte-orange …]` : GO nominatif (cible + acte).

## 11. Fichiers
`scripts/helios-ops/helios_disk_test.py` (outil) · `scripts/helios-ops/test_helios_disk_test.py` (**187 tests** : `cd scripts/helios-ops && python -I -B test_helios_disk_test.py -v`, ≈ 1,3 s ; tout accès `subprocess`, `urllib`, `socket`, `sleep`, `os.system` y fait échouer le test) · ce runbook. Couverture : paramètres et limites, calculs, parsing (kubelet, Prometheus, VMDK, journaux VMware, `stat`, `findmnt`, `hostname`), pré-vol (chaque fait manquant = ROUGE), allocation / expiration / urgence / erreurs sur un nœud simulé (latence ssh, écritures externes, libérations externes, sondes en panne), **jeu fermé de commandes** sur un émulateur de nœud (verbes, noms exacts, aucun joker, grammaire indépendante du programme de garde, `SELECT` seulement), ligne de commande, absence d'effets de bord. Par famille : volume et effet 18 · identité 12 · délai et expiration 9 · acquittements ORANGE 13 · sondes illisibles 6 · garde côté nœud 17 · commandes statiques 14.

## 12. Revue de sécurité du 13 nov. 2026 : défauts, corrections, tests
| Défaut relevé | Correction | Tests |
|---|---|---|
| **B1** fichiers créés sur un AUTRE volume que celui mesuré (démontré : 16 blocs = 8 192 Mo, usage inchangé) ; `MAX_TOTAL` appliqué au plan seulement | volume / type / propriétaire / droits vérifiés (pré-vol, puis sur le répertoire créé) ; effet de chaque allocation ≥ 90 % ; plafond cumulé dans la boucle | `ControleDuVolumeTests` |
| **B2** correspondance IP ↔ nom du nœud jamais vérifiée (la porte CNPG repose dessus) | nom d'hôte ssh, nom Kubernetes, InternalIP croisés au pré-vol ; nom d'hôte relu au lancement | `IdentiteDuNoeudTests` |
| **B3** fenêtre du minuteur non reliée à la durée réelle ; attribution relative à la mauvaise origine ; garantie « n'expire jamais » fausse | `t_arm` pris avant l'armement ; budget d'allocation ; suivi borné par l'échéance ; attribution par l'état des deux unités | `DelaiEtExpirationTests` |
| **M2** ORANGE accepté en silence par `--go` ; drapeau illisible = ORANGE ; options abrégeables | codes ORANGE + `--accepte-orange` ; drapeau illisible = ROUGE ; `allow_abbrev=False` | `AcquittementsOrangeTests` |
| **M3** sonde illisible lue comme « aucun incident » | `SondeIllisible` ⇒ `ERREUR` ; Prometheus illisible = ROUGE, requête par requête | `SondesIllisiblesTests` |
| **M4** latence d'urgence de 15 à 25 s pendant le suivi, aucune garde côté nœud | mesure toutes les 5 s, remesure après chaque appel lent, contrôle aussitôt après chaque bloc ; **garde awk côté nœud à 83,5 %** | `GardeCoteNoeudTests` |

**Restent ouverts (non demandés dans cette passe)** : validateurs `$` des noms de fichier et du jeton (`RE_FICHIER`, `RE_JETON` : un saut de ligne final passe ; noms et jeton sont générés en interne, les nouvelles validations utilisent `fullmatch`) · nom du pod primaire (lu dans le JSON CNPG) non validé avant `kubectl` · `psql` exécuté en superutilisateur (SELECT statiques) · `StrictHostKeyChecking=no`, `ssh` / `kubectl` résolus par leur nom.
**Limites connues** : entre la mesure et le `fallocate` restent deux appels ssh (marge plafond → urgence : 1 point ≈ 381 Mo) ; la garde contrôle toutes les 2 s (une écriture externe de plus de ≈ 286 Mo/s pourrait franchir 1,5 point entre deux contrôles) ; les deux filets sont perdus si le nœud redémarre ; le comportement réel de `systemd-run`, `awk`, `sudo` et ssh n'est vérifié que sur des émulateurs.

## 13. Annexe — contrôle de mutation (copies de l'outil hors dépôt)
Méthode : pour chaque ligne, UNE règle de sécurité est cassée dans une **copie** de l'outil (dossier temporaire du job, jamais le dépôt) et la suite complète (187 tests) est rejouée ; la mutation est « tuée » si la suite échoue. Le script n'est pas versionné (hors dépôt) ; ce tableau en est la trace, générée à partir de sa sortie. **Résultat : 94 mutations, 94 tuées, 0 survivante.** Historique : la 1re campagne (étape 2) comptait 30 mutations (29 tuées + M28, redondante) ; les mutations M24 et M25 avaient survécu avant l'ajout de leurs tests, M28 est tuée depuis qu'un test exige le libellé exact du refus ; B2-07 et M3-02 ont survécu à la 1re passe de cette campagne (identité non transmise à `--go`, statut Prometheus d'erreur avec liste vide) et ont conduit à deux tests de plus. Les mutations sont choisies par l'auteur de l'outil : elles ne prouvent pas l'absence d'autres défauts.

| ID | Règle cassée | Premiers tests qui l'attrapent (nombre d'échecs) |
|---|---|---|
| M01 | cible .160 acceptee | `cibles_hors_perimetre_et_alias_inconnus`, `seules_155_et_158_sont_autorisees` (6 echecs) |
| M02 | mode par defaut = lancement reel | `defaut_simulation_155_refuse_car_primaire_cnpg`, `defaut_simulation_158` (11 echecs) |
| M03 | --confirm ignore | `go_exige_la_confirmation_exacte` (4 echecs) |
| M04 | seuils desserrables | `on_peut_resserrer_jamais_desserrer`, `seuils_desserres_refuses` (6 echecs) |
| M05 | motif de nom = joker | `noms_de_fichiers_stricts`, `noms_invalides_refuses_avant_tout_appel_ssh` (6 echecs) |
| M06 | primaire CNPG presume accepte | `defaut_simulation_155_refuse_car_primaire_cnpg`, `le_risque_cnpg_ne_s_acquitte_que_par_son_propre_drapeau` (5 echecs) |
| M07 | journaux VMware illisibles = INFO | `tout_fait_absent_ou_illisible_est_rouge` (1 echec) |
| M08 | ssh sans shlex.quote | `ssh_quote_les_arguments_et_n_execute_que_ssh` (1 echec) |
| M09 | pas de liberation dans finally | `allocation_sans_effet_mesure_s_arrete_des_le_premier_bloc`, `arret_d_urgence_par_croissance_organique` (49 echecs) |
| M10 | minuteur desarme meme si liberation echouee | `la_garde_reste_armee_tant_que_la_liberation_n_est_pas_verifiee`, `liberation_impossible_le_minuteur_reste_arme_puis_libere` (3 echecs) |
| M11 | minuteur actif non verifie | `minuteur_inactif_aucune_allocation`, `ssh_tres_lent_refuse_avant_la_premiere_allocation` (2 echecs) |
| M12a | urgence ignoree pendant le suivi (1re mesure) | `l_outil_vivant_reagit_en_moins_de_5_secondes_apres_le_franchissement_de_83_pct` (1 echec) |
| M12b | urgence ignoree pendant le suivi (remesure apres la sonde) | `urgence_detectee_aussitot_apres_une_sonde_lente` (1 echec) |
| M13 | rm -rf | `creation_allocation_liberation_arguments_exacts`, `le_noeud_ssh_n_emet_que_les_verbes_du_jeu_ferme` (2 echecs) |
| M14 | rmdir remplace par rm -rf | `aucune_commande_hors_du_jeu_ferme`, `creation_allocation_liberation_arguments_exacts` (8 echecs) |
| M15 | minuteur jamais arme | `acquittement_partiel_refuse_l_orange_restant`, `allocation_avec_un_effet_suffisant_est_acceptee` (57 echecs) |
| M16 | mesure non rafraichie avant chaque bloc | `acquittement_partiel_refuse_l_orange_restant`, `allocation_avec_un_effet_suffisant_est_acceptee` (43 echecs) |
| M17 | plafond non controle apres allocation | `ecriture_externe_apres_le_dernier_bloc_au_dela_du_plafond` (1 echec) |
| M18 | residu du repertoire ignore | `residu_dans_le_repertoire_de_test_refus_sans_toucher_aux_fichiers` (1 echec) |
| M19 | simulation .155 : primaire CNPG non signale | `chaque_orange_a_un_code_connu_et_un_seul`, `defaut_simulation_155_refuse_car_primaire_cnpg` (5 echecs) |
| M20 | acquittement CNPG toujours actif | `defaut_simulation_155_refuse_car_primaire_cnpg`, `le_risque_cnpg_ne_s_acquitte_que_par_son_propre_drapeau` (4 echecs) |
| M21 | repertoire de test = /tmp | `acquittement_partiel_refuse_l_orange_restant`, `armer_garde_commande_exacte_et_verifiee` (10 echecs) |
| M22 | codes de sortie a 0 | `acquittement_partiel_refuse_l_orange_restant`, `go_accepte_l_orange_acquitte_code_par_code` (6 echecs) |
| M23 | minuteur d'expiration avec joker | `acquittement_partiel_refuse_l_orange_restant`, `execution_complete_sur_l_emulateur_jeu_ferme` (17 echecs) |
| M24 | liberation non verifiee | `une_liberation_non_verifiee_n_est_pas_comptee_comme_reussie` (1 echec) |
| M25 | pre-vol : serie Prometheus absente non ROUGE | `prometheus_serie_unique_et_coherente` (1 echec) |
| M26 | garde d'allocation : plafond non verifie | `garde_avant_chaque_allocation` (1 echec) |
| M27 | planifier : base > recuperation toleree | `le_plafond_n_est_jamais_depasse_sans_croissance_externe`, `plan_refuse_une_base_hors_limites` (5 echecs) |
| M28 | evaluer : base > recuperation toleree | `base_au_dessus_de_la_recuperation_est_expliquee_par_le_pre_vol_lui_meme` (1 echec) |
| M29 | VMDK avec snapshot tolere | `vmdk_a_risque_et_cas_neutres` (1 echec) |
| M30 | marge kubelet ignoree | `base_hors_limites_et_kubelet` (1 echec) |
| B1-01 | repertoire cree non verifie (type/proprietaire/droits/volume) | `repertoire_cree_non_conforme_est_refuse_avant_toute_allocation_puis_retire`, `repertoire_preexistant_non_conforme_est_refuse_sans_rien_toucher` (8 echecs) |
| B1-02 | parent du repertoire non verifie | `parent_sur_un_autre_volume_est_refuse_avant_la_creation` (1 echec) |
| B1-03 | effet de l'allocation non verifie | `allocation_sans_effet_mesure_s_arrete_des_le_premier_bloc` (3 echecs) |
| B1-04 | effet minimal ramene a 10 % | `allocation_sans_effet_mesure_s_arrete_des_le_premier_bloc` (2 echecs) |
| B1-05 | plafond cumule retire de la garde d'allocation | `plafond_cumule_applique_dans_la_boucle_apres_une_liberation_externe`, `plafond_cumule_de_la_garde_d_allocation` (2 echecs) |
| B1-06 | cumul non transmis a la garde d'allocation | `plafond_cumule_applique_dans_la_boucle_apres_une_liberation_externe` (1 echec) |
| B1-07 | incoherence ls / stat toleree | `incoherence_entre_ls_et_stat_est_refusee` (1 echec) |
| B1-08 | peripherique different tolere | `anomalies_cumulees`, `anomalies_du_dossier_une_par_une` (5 echecs) |
| B1-09 | permissions non verifiees | `anomalies_cumulees`, `anomalies_du_dossier_une_par_une` (9 echecs) |
| B1-10 | proprietaire non verifie | `anomalies_cumulees`, `anomalies_du_dossier_une_par_une` (7 echecs) |
| B1-11 | lien symbolique tolere (dossier) | `anomalies_cumulees`, `anomalies_du_dossier_une_par_une` (5 echecs) |
| B1-12 | point de montage different tolere | `anomalies_cumulees`, `anomalies_du_dossier_une_par_une` (5 echecs) |
| B1-13 | pre-vol : etat du dossier illisible non ROUGE | `preflight_faits_du_volume_illisibles_ou_absents_sont_rouges` (1 echec) |
| B1-14 | pre-vol : etat de / illisible non ROUGE | `preflight_faits_du_volume_illisibles_ou_absents_sont_rouges` (1 echec) |
| B1-15 | inspecter : chemin libre accepte | `noeud_ssh_hostname_et_inspecter_commandes_exactes` (8 echecs) |
| B2-01 | hostname discordant tolere (pre-vol) | `go_refuse_sans_aucune_ecriture_si_l_identite_discorde`, `les_trois_sources_doivent_concorder` (3 echecs) |
| B2-02 | hostname illisible non ROUGE | `les_trois_sources_doivent_concorder`, `preflight_hostname_illisible_est_rouge` (2 echecs) |
| B2-03 | InternalIP discordante toleree | `go_refuse_sans_aucune_ecriture_si_l_identite_discorde`, `les_trois_sources_doivent_concorder` (4 echecs) |
| B2-04 | InternalIP absente toleree | `etat_du_cluster_et_du_noeud`, `les_trois_sources_doivent_concorder` (6 echecs) |
| B2-05 | nom du noeud K8s different tolere | `les_trois_sources_doivent_concorder` (1 echec) |
| B2-06 | executer_test ne reverifie pas le hostname | `executer_test_reverifie_le_nom_d_hote_avant_toute_ecriture`, `go_reverifie_l_identite_au_lancement_meme_si_le_pre_vol_etait_bon` (2 echecs) |
| B2-07 | --go ne transmet pas l'identite | `go_reverifie_l_identite_au_lancement_meme_si_le_pre_vol_etait_bon` (1 echec) |
| B2-08 | verifier_identite sans comparaison | `executer_test_reverifie_le_nom_d_hote_avant_toute_ecriture`, `go_reverifie_l_identite_au_lancement_meme_si_le_pre_vol_etait_bon` (2 echecs) |
| B2-09 | parse_hostname accepte tout | `parse_hostname_et_nom_court`, `sorties_illisibles_du_noeud_sont_des_erreurs` (11 echecs) |
| B2-10 | hostname non lu au pre-vol | `acquittement_partiel_refuse_l_orange_restant`, `go_accepte_l_orange_acquitte_code_par_code` (13 echecs) |
| B3-01 | budget compte depuis le lancement et non depuis l'armement | `ssh_lent_arrete_les_allocations_bien_avant_l_echeance_du_minuteur` (1 echec) |
| B3-02 | budget d'allocation non controle | `ssh_lent_arrete_les_allocations_bien_avant_l_echeance_du_minuteur`, `ssh_tres_lent_refuse_avant_la_premiere_allocation` (2 echecs) |
| B3-03 | attribution : minuteur tire => helios | `attribution_de_la_liberation_ne_credite_jamais_helios_par_defaut`, `un_minuteur_qui_tire_pendant_l_observation_donne_une_erreur_et_jamais_helios` (2 echecs) |
| B3-04 | attribution : echeance atteinte ignoree | `attribution_de_la_liberation_ne_credite_jamais_helios_par_defaut` (1 echec) |
| B3-05 | attribution : garde tiree => helios | `attribution_de_la_liberation_ne_credite_jamais_helios_par_defaut`, `la_garde_prend_le_relais_quand_l_outil_est_ralenti` (2 echecs) |
| B3-06 | suivi non borne par l'echeance du minuteur | `fenetre_d_observation_insuffisante_est_refusee`, `suivi_borne_par_l_echeance_la_plus_proche_du_minuteur` (2 echecs) |
| B3-07 | fenetre d'observation insuffisante toleree | `fenetre_d_observation_insuffisante_est_refusee` (1 echec) |
| B3-08 | expiration sans budget d'allocation ni marge | `l_expiration_doit_couvrir_budget_observation_et_marge` (3 echecs) |
| B3-09 | minuteur tire pendant l'observation non signale | `un_minuteur_qui_tire_pendant_l_observation_donne_une_erreur_et_jamais_helios` (1 echec) |
| B3-10 | horloge reelle non monotone | `horloge_reelle_est_monotone` (1 echec) |
| B3-11 | instant d'armement = lancement | `ssh_lent_arrete_les_allocations_bien_avant_l_echeance_du_minuteur` (1 echec) |
| B3-12 | estimation de la duree d'un bloc supprimee | `ssh_lent_arrete_les_allocations_bien_avant_l_echeance_du_minuteur` (1 echec) |
| B3-13 | MARGE_FIN_S a 0 | `l_expiration_doit_couvrir_budget_observation_et_marge`, `ssh_lent_arrete_les_allocations_bien_avant_l_echeance_du_minuteur` (3 echecs) |
| M2-01 | ORANGE non acquittes tolere par --go | `acquittement_partiel_refuse_l_orange_restant`, `go_refuse_un_orange_non_acquitte_sans_aucune_ecriture` (2 echecs) |
| M2-02 | tout ORANGE considere acquitte | `acquittement_partiel_refuse_l_orange_restant`, `go_refuse_un_orange_non_acquitte_sans_aucune_ecriture` (3 echecs) |
| M2-03 | le risque CNPG s'acquitte par --accepte-orange | `analyser_acquittements`, `l_aide_en_ligne_liste_chaque_code_acquittable_avec_sa_description` (3 echecs) |
| M2-04 | options abregeables | `les_options_ne_peuvent_pas_etre_abregees` (2 echecs) |
| M2-05 | drapeau illisible = ORANGE | `un_drapeau_de_remediation_illisible_est_rouge_alors_que_false_et_absent_sont_orange` (3 echecs) |
| M2-06 | code ORANGE inconnu accepte | `analyser_acquittements`, `code_orange_inconnu_est_refuse_avant_toute_collecte` (8 echecs) |
| M2-07 | code d'acquittement non affiche au pre-vol | `la_simulation_et_le_pre_vol_affichent_les_acquittements_a_donner` (1 echec) |
| M2-08 | codes a acquitter absents de la commande suggeree | `la_simulation_et_le_pre_vol_affichent_les_acquittements_a_donner` (1 echec) |
| M3-01 | Prometheus illisible non signale | `chaque_requete_prometheus_illisible_rend_le_pre_vol_rouge` (8 echecs) |
| M3-02 | statut Prometheus non verifie | `lire_prom_distingue_vide_et_illisible` (3 echecs) |
| M3-03 | sonde en echec = aucun incident | `go_complet_avec_sonde_en_panne_donne_erreur_et_libere`, `sonde_reelle_distingue_aucune_ligne_et_echec` (2 echecs) |
| M3-04 | aucun nouvel essai de la sonde | `sonde_reelle_distingue_aucune_ligne_et_echec` (1 echec) |
| M3-05 | reponse inattendue de la base tolere | `sonde_reelle_distingue_aucune_ligne_et_echec` (1 echec) |
| M4-01 | mesure du suivi toutes les 15 s | `surveillance_toutes_les_5_secondes_et_remesure_apres_chaque_sonde_lente` (1 echec) |
| M4-02 | pas de remesure apres la sonde lente | `surveillance_toutes_les_5_secondes_et_remesure_apres_chaque_sonde_lente`, `urgence_detectee_aussitot_apres_une_sonde_lente` (2 echecs) |
| M4-03 | garde jamais armee | `acquittement_partiel_refuse_l_orange_restant`, `allocation_avec_un_effet_suffisant_est_acceptee` (55 echecs) |
| M4-04 | garde non verifiee active | `garde_inactive_aucune_allocation`, `ssh_tres_lent_refuse_avant_la_premiere_allocation` (2 echecs) |
| M4-05 | garde a urgence + 2,5 points (apres le kubelet) | `la_garde_prend_le_relais_quand_l_outil_est_ralenti`, `seuil_de_la_garde_entre_l_urgence_et_le_kubelet` (2 echecs) |
| M4-06 | garde a l'urgence meme (sans marge) | `arret_d_urgence_par_croissance_organique`, `l_outil_vivant_reagit_en_moins_de_5_secondes_apres_le_franchissement_de_83_pct` (4 echecs) |
| M4-07 | garde jamais desarmee apres une liberation verifiee | `garde_inactive_aucune_allocation`, `ordre_des_operations_filets_avant_la_premiere_allocation` (2 echecs) |
| M4-08 | urgence non controlee aussitot apres chaque bloc | `arret_d_urgence_pendant_l_allocation` (1 echec) |
| M4-09 | programme de garde : chemin libre accepte | `programme_de_garde_refuse_les_entrees_invalides` (5 echecs) |
| M4-10 | programme de garde : seuil libre | `armer_garde_parametres_invalides_refuses_avant_tout_appel_ssh`, `programme_de_garde_refuse_les_entrees_invalides` (6 echecs) |
| M4-11 | jeton de garde avec saut de ligne accepte | `armer_garde_parametres_invalides_refuses_avant_tout_appel_ssh` (2 echecs) |
| M4-12 | duree de garde libre | `armer_garde_parametres_invalides_refuses_avant_tout_appel_ssh` (4 echecs) |
