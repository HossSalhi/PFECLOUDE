# Rapport — Observation passive de 24 h (6 nov. 21:35 → 7 nov. 21:35 2026)

**Statut : TERMINÉE le 7 nov. 2026 à 21:53 — verdict brut FAIL — NON VALIDÉE comme preuve de stabilité (fenêtre contaminée par 8 arrêts de VM provoqués).**
Rédigé le 11 nov. 2026 (`[MODE : RO]` partout : lecture de fichiers, de journaux VMware, d'un SELECT et de Prometheus `.160` en GET). Aucune VM, aucun service, aucune ressource du cluster n'a été touché pour établir ce rapport. Détail généré : `ANNEXE-CLASSEMENT-OBSERVATION-24H.md` (outil `infra/helios-sot/tools/classify_observation.py`, 26 tests). Rapport automatique d'origine conservé tel quel : `C:\Users\houss\helios-state\observation\stabilite-24h-rapport.md`.

## 1. Chiffres : le brut d'abord, puis ce qui reste
| Outil | Relevés | **FAIL brut** | FAIL hors arrêts provoqués (A) | hors A + période de manipulations (B) | hors A + B + gels d'hôte (C) = **non expliqués** |
|---|---|---|---|---|---|
| Stabilité (`passive_observe.py`) | 99 | **40** | 20 | 4 | **1** |
| « Armé » (`shadow_observe.py --armed`) | 97 | **35** | 1 | 0 | **0** |
| Stabilité, **période propre** (avant la 1ʳᵉ manipulation, 15 h 31) | 63 | **4** | 4 | 4 | **1** |

Aucune de ces lignes n'est un « OK » : un FAIL expliqué reste une indisponibilité ou un écart réel, il est seulement attribué.

## 2. Ce que la fenêtre établit
1. **8 arrêts de VM provoqués** (vous l'avez confirmé) : 4 prouvés par la fin des journaux VMware (`VMAutomationPowerOff` + `cleanShutdown=TRUE` + `VMX exit (0)`) — `.155` 12:49Z, `.162` 14:50Z et 15:44Z, `.158` 19:40Z — et 4 datés par l'incident correspondant parce que les journaux ont roulé (`.162` ≈ 12:04Z et ≈ 12:09Z, `.158` ≈ 15:23Z et ≈ 15:54Z ; début estimé = création de l'incident − 90 s).
2. **Les 8 redémarrages du watchdog sont corrects** : chacun avec `state_before = POWERED_OFF` confirmé par `vmrun list` (`CONFIRME_DANS_VMRUN_LIST`), appelant = pod backend (`.155` ou `.158`), **un seul démarrage par incident, 0 démarrage superflu, 0 refus, aucune VM `.153` ni `.160` ciblée**. Un 9ᵉ démarrage (`.158`, incident 5861) a eu lieu le 9 nov. 10:56Z, hors fenêtre. Ce sont des preuves réelles supplémentaires du scénario VM_DOWN pour `.155` (1), `.158` (4 avec celui du 9 nov.) et `.162` (4) — obtenues par vos arrêts, hors protocole de pré-vol.
3. **Politique COHÉRENTE à 93 relevés sur 99** (les 6 écarts tombent dans la période de manipulations).
4. **Passivité de l'observateur** : analyse statique 8/8 (aucune écriture, un seul site de sous-processus, SQL en SELECT, HTTP en GET) ; les 8 démarrages viennent du backend, pas de l'outil.
5. **Aucune perte de données constatée à ce jour** : les 5 PVC sont `Bound`, aucune suppression ; dumps récents réussis. (Aucun contrôle d'intégrité du contenu de la base n'a été fait pour ce rapport.)

## 3. Ce que la fenêtre ne valide pas
1. **La stabilité de 24 h n'est pas démontrée** : 8 arrêts de VM sur 7 h 40 (13:06 → 20:43 locale) rendent la fenêtre inexploitable pour cet objectif. **Elle doit être refaite** sans intervention.
2. **Période propre (6 nov. 21:35 → 7 nov. 13:06 locale)** : 4 relevés FAIL sur 63. **3 coïncident avec des gels d'hôte mesurés** (22:37 : 32-33 s ; 22:52 : 45-50 s ; 10:08 : 43-57 s, **sur tous les chemins à la fois** : web, API, les deux HAProxy). **1 reste non expliqué** : retard de réplication CNPG de 11,6 Mo à 02:37 (au-dessus du seuil de 1 Mo, sans gel mesuré à cet instant). Les gels couvrent 5,3 % de la fenêtre : une coïncidence au hasard est improbable (≈ 5 % par item) mais elle n'est pas prouvée causale.
3. **Limite d'architecture** : l'actif/actif HAProxy et les 5 membres etcd **ne masquent pas un gel de l'hôte** (les 6 VM partagent le même disque : destin commun). Les gels de 30-57 s sont des indisponibilités réelles du web et de l'API.
4. **Effet de conception coûteux** : le cooldown de 30 min du backend a laissé `.162` éteinte **26 min** (incident 4937 créé 12:10Z ; redémarrée 12:36:30Z = action précédente + 30 min). Conforme à la conception, discutable en exploitation : décision à prendre (D, cf. rapport de clôture).
5. **CNPG** : bascule du primaire `postgres-7`/`.158` → `postgres-8`/`.155` et réplique absente ≈ 6,5 h (14:22 → 20:53) : séquelle probable des arrêts de `.158` (catégorie B), **non prouvée individuellement**.
6. **Défaut de l'observateur, corrigé** : le compteur cumulatif « N action(s) RESTART_VM… » rendait FAIL **tous** les relevés suivants : **13 relevés de l'outil de stabilité ne sont FAIL que par ce compteur** (7 nov. 15:37 → 21:52), et **les 35 relevés FAIL de l'outil « armé » ne le sont que par des compteurs de redémarrage** (ou l'écoute du watchdog), aucun autre motif. Désormais : WARN si chaque redémarrage est couvert par un arrêt VIX constaté, FAIL s'il y en a plus que d'arrêts constatés ou si aucun arrêt n'est constaté.
7. **Mémoire de l'hôte** : pendant la fenêtre, la RAM libre était de 1 730 Mo (min), 4 852 Mo (médiane), 6 269 Mo (max) : **pas de pression mémoire**. Celle du 11 nov. (110-430 Mo, ≈ 35 000 pages/s relues) est **postérieure** ; ce n'est pas la cause des FAIL de la fenêtre.

## 4. Corrections apportées aux outils (vérifiées)
- **Nouveau** `infra/helios-sot/tools/classify_observation.py` + `scripts/helios-ops/test_classify_observation.py` (26 tests) : classement A/B/C/D, chiffres bruts ET restants, aucun sous-processus, écriture seulement vers `--out`/`--json-out`, **n'émet jamais « VALIDÉ »**.
- `infra/helios-sot/tools/passive_observe.py` : relevés `arrets_vix` (fin des `vmware*.log`) et `hote.ram_disponible_mo` (`GlobalMemoryStatusEx`, stdlib), WARN « CONTAMINATION » et « RAM disponible < 1 500 Mo », section « Arrêts de VM par demande VIX » et mention « Fenêtre CONTAMINÉE » dans le rapport, verdict RESTART_VM affiné. Tests : `test_passive_observe.py` 24/24, `test_observe_passive.py` (passivité) 8/8.
- Conventions explicites (paramètres, publiés) : fenêtre d'impact d'un arrêt = [arrêt − 60 s ; redémarrage + 300 s] ; période de manipulations = jusqu'à 2 h après le dernier redémarrage ; gel d'hôte = fsync WAL **ou** RTT etcd p99 > 1 s sur ≥ 3 membres au même instant.

## 5. Suite
- **Nouvelle fenêtre de 24 h (C. EN COURS)** : à enregistrer (`Register-HeliosObservation24h.ps1 -Tool <shadow_armed|stabilite> -Apply`) **seulement** quand : RAM de l'hôte libérée, cluster stable ≥ 1 h (3 nœuds Ready, CNPG 2/2, etcd 5/5) et **engagement de ne pas arrêter de VM pendant 24 h** (sinon la fenêtre sera de nouveau classée CONTAMINÉE).
- Limites de cette analyse : 4 débuts d'arrêt sont estimés ; la catégorie B est une convention de prudence (séquelles probables) et non une preuve ; les fenêtres de gel dépendent de Prometheus `.160` (si injoignable : aucune exemption n'est appliquée, et le rapport le dit).
