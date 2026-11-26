# Classement de l'observation passive (généré par classify_observation.py, LECTURE SEULE)

Fenêtre : 2026-11-06 21:35:51 → 2026-11-07 21:35:51 (heure locale).

**Verdict : FENÊTRE CONTAMINÉE : 8 arrêt(s) de VM provoqué(s) pendant la fenêtre. NON VALIDÉE comme preuve de stabilité de 24 h ; ré-observation requise sur une fenêtre sans intervention.**

## Arrêts de VM provoqués (preuves croisées)
| Incident | VM | Arrêt (UTC) | Démarrage (UTC) | Appelant | Source |
|---|---|---|---|---|---|
| 4936 | k8s-lb2 | 11-07 12:04:33Z (estimé) | 11-07 12:06:21Z | 192.168.148.155 | watchdog (POWERED_OFF confirmé) ; journal VMware roulé, début estimé |
| 4937 | k8s-lb2 | 11-07 12:08:55Z (estimé) | 11-07 12:36:30Z | 192.168.148.155 | watchdog (POWERED_OFF confirmé) ; journal VMware roulé, début estimé |
| 4938 | k8s-worker2 | 11-07 12:49:08Z | 11-07 12:51:02Z | 192.168.148.158 | journal VMware (vmware-1.log) + watchdog |
| 4976 | k8s-lb2 | 11-07 14:50:38Z | 11-07 14:51:47Z | 192.168.148.158 | journal VMware (vmware-2.log) + watchdog |
| 4977 | k8s-worker1 | 11-07 15:23:30Z (estimé) | 11-07 15:25:53Z | 192.168.148.155 | watchdog (POWERED_OFF confirmé) ; journal VMware roulé, début estimé |
| 4978 | k8s-lb2 | 11-07 15:44:35Z | 11-07 15:45:31Z | 192.168.148.158 | journal VMware (vmware-1.log) + watchdog |
| 4979 | k8s-worker1 | 11-07 15:53:56Z (estimé) | 11-07 15:56:20Z | 192.168.148.155 | watchdog (POWERED_OFF confirmé) ; journal VMware roulé, début estimé |
| 5051 | k8s-worker1 | 11-07 19:40:46Z | 11-07 19:43:23Z | 192.168.148.155 | journal VMware (vmware-2.log) + watchdog |

Démarrages du watchdog SANS arrêt prouvé (superflus) : **0**.

Fenêtres d'impact fusionnées (UTC) : 11-07 12:03:33Z → 11-07 12:41:30Z; 11-07 12:48:08Z → 11-07 12:56:02Z; 11-07 14:49:38Z → 11-07 14:56:47Z; 11-07 15:22:30Z → 11-07 15:30:53Z; 11-07 15:43:35Z → 11-07 15:50:31Z; 11-07 15:52:56Z → 11-07 16:01:20Z; 11-07 19:39:46Z → 11-07 19:48:23Z.
Période de manipulations (UTC, marge de 2 h = convention) : 11-07 12:03:33Z → 11-07 21:43:23Z.
Gels d'hôte mesurés (Prometheus .160) : 72 fenêtre(s) fusionnées (fsync 16, RTT 66) couvrant 4610 s, soit 5.3 % de la fenêtre : un FAIL classé C avait donc environ 5 % de chances de coïncider par hasard.

## Outil de stabilité — chiffres bruts ET restants
| Mesure | Valeur |
|---|---|
| relevés | 99 |
| FAIL brut | 40 |
| FAIL hors A | 20 |
| FAIL hors A+B | 4 |
| FAIL hors A+B+C (non expliqués) | 1 |

Items de FAIL par catégorie : A=80, B=117, C=16, D=1.

## Outil « armé » — chiffres bruts ET restants
| Mesure | Valeur |
|---|---|
| relevés | 97 |
| FAIL brut | 35 |
| FAIL hors A | 1 |
| FAIL hors A+B | 0 |
| FAIL hors A+B+C (non expliqués) | 0 |

Items de FAIL par catégorie : A=68, B=1, C=0, D=0.

## Période propre (avant la première manipulation) — outil de stabilité
| Mesure | Valeur |
|---|---|
| relevés | 63 |
| FAIL brut | 4 |
| FAIL hors A | 4 |
| FAIL hors A+B | 4 |
| FAIL hors A+B+C (non expliqués) | 1 |

Relevés FAIL de la période propre, item par item :
- 11-06 22:37:27 (local) :
  - [C, coïncide avec un gel] sonde kube_api : série d'échecs de 32.4 s
  - [C, coïncide avec un gel] sonde lb162_api : série d'échecs de 33.5 s
- 11-06 22:52:27 (local) :
  - [C, coïncide avec un gel] sonde api_app : série d'échecs de 48.6 s
  - [C, coïncide avec un gel] sonde kube_api : série d'échecs de 48.8 s
  - [C, coïncide avec un gel] sonde lb160_api : série d'échecs de 48.7 s
  - [C, coïncide avec un gel] sonde lb160_web : série d'échecs de 48.6 s
  - [C, coïncide avec un gel] sonde lb162_api : série d'échecs de 49.7 s
  - [C, coïncide avec un gel] sonde lb162_web : série d'échecs de 44.8 s
  - [C, coïncide avec un gel] sonde web : série d'échecs de 44.8 s
- 11-07 02:37:26 (local) :
  - [D] CNPG : retard de postgres-8 = 11657280 o
- 11-07 10:08:09 (local) :
  - [C, coïncide avec un gel] sonde api_app : série d'échecs de 56.1 s
  - [C, coïncide avec un gel] sonde kube_api : série d'échecs de 42.8 s
  - [C, coïncide avec un gel] sonde lb160_api : série d'échecs de 57.1 s
  - [C, coïncide avec un gel] sonde lb160_web : série d'échecs de 48.1 s
  - [C, coïncide avec un gel] sonde lb162_api : série d'échecs de 53.3 s
  - [C, coïncide avec un gel] sonde lb162_web : série d'échecs de 53.6 s
  - [C, coïncide avec un gel] sonde web : série d'échecs de 52.4 s

## RAM libre de l'hôte pendant la fenêtre (relevés de l'outil armé)
n=95, min 1730 Mo, médiane 4852 Mo, max 6269 Mo ; relevés sous 1 000 Mo : 0 ; sous 2 500 Mo : 1.

## Détail des relevés FAIL (outil de stabilité)
| Relevé (local) | Catégories | Coïncide avec un gel | Items |
|---|---|---|---|
| 11-06 22:37:27 | C | oui | 2 |
| 11-06 22:52:27 | C | oui | 7 |
| 11-07 02:37:26 | D | non | 1 |
| 11-07 10:08:09 | C | oui | 7 |
| 11-07 13:07:26 | A | oui | 4 |
| 11-07 13:22:26 | A | oui | 10 |
| 11-07 13:37:26 | A | oui | 3 |
| 11-07 13:53:07 | A | oui | 9 |
| 11-07 14:07:27 | B | oui | 5 |
| 11-07 14:22:27 | A/B | non | 11 |
| 11-07 14:37:27 | A/B | non | 11 |
| 11-07 14:52:27 | A/B | non | 11 |
| 11-07 15:08:00 | A/B | oui | 10 |
| 11-07 15:22:27 | A/B | oui | 2 |
| 11-07 15:37:46 | A | non | 1 |
| 11-07 15:52:50 | A | oui | 2 |
| 11-07 16:07:39 | A/B | oui | 8 |
| 11-07 16:22:28 | A/B | non | 8 |
| 11-07 16:37:29 | A | non | 1 |
| 11-07 16:53:05 | A | oui | 14 |
| 11-07 17:07:27 | A | oui | 4 |
| 11-07 17:22:27 | A | non | 1 |
| 11-07 17:38:00 | A/B | oui | 12 |
| 11-07 17:52:29 | A/B | non | 11 |
| 11-07 18:07:49 | A/B | oui | 15 |
| 11-07 18:22:27 | A/B | oui | 8 |
| 11-07 18:37:27 | A | non | 1 |
| 11-07 18:52:27 | A | non | 1 |
| 11-07 19:07:30 | A | non | 1 |
| 11-07 19:22:29 | A | non | 1 |
| 11-07 19:37:27 | A | non | 1 |
| 11-07 19:52:30 | A | non | 1 |
| 11-07 20:07:56 | A | non | 1 |
| 11-07 20:22:28 | A/B | oui | 4 |
| 11-07 20:37:28 | A | non | 1 |
| 11-07 20:53:15 | A/B | oui | 11 |
| 11-07 21:07:30 | A/B | oui | 3 |
| 11-07 21:22:36 | A/B | oui | 8 |
| 11-07 21:37:28 | A | non | 1 |
| 11-07 21:52:28 | A | non | 1 |

Légende : A — arrêt de VM provoqué (fenêtre d'impact) ; B — période de manipulations (séquelle probable, non prouvée) ; C — gel d'hôte mesuré (fsync WAL ou RTT etcd, >= 3 membres) ; D — NON EXPLIQUÉ.
