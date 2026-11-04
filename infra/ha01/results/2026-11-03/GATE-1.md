# GATE-1 — VALIDÉ le 4 novembre 2026, 07:23 UTC (08:23 heure locale)

Évaluation automatique et rejouable : `python infra/ha01/ops/gate1_check.py --since-change-min 30 --shadow-hours 8` → code retour 0, **36 critères ✅ / 0 ❌**
(`GATE-1-mesures.md`). Les chiffres sont recalculés à chaque exécution.

## Écarts appliqués à ce GATE (tous documentés dans `README-HA-01.md`)
| # | Écart | Qui l'a décidé / pourquoi |
|---|---|---|
| E6 | Les échecs qui chevauchent un **gel d'E/S de l'hôte** (≥ 3 membres etcd, VM distinctes, p99 fsync WAL > 1 s en même temps) sont classés « hors gel » ; le chiffre **brut** est toujours publié à côté | Acceptée par l'utilisateur le 4 nov. (« ok j'accepte E6, continue ») |
| E7 | Les canaris DB des **workers** (`ha01-canary-w1/-w2`, sans DNS) font foi ; le canari de `.153` est informatif | Instrument de `.153` invalide (nœud le plus chargé) |
| E8 | Correctifs d'outillage (UTF-8, copie vivante de `.161`, ordre journaux/id, fenêtre RAM de 10 min bornée à l'avant-évaluation) | Défauts de mes scripts trouvés par l'essai à blanc |
| **E10** | **Durée du shadow DNS : ≥ 8 h au lieu de 24 h** (observé : `.160` 8,5 h, `.162` 8,7 h, **0 passage DOWN** sur les deux) | **Décision explicite de l'utilisateur** : « je ne veux pas attendre toute cette période, je veux tout faire aujourd'hui 04/11/2026 » |

## Mesures clés
- **Sondes (28 400 échantillons par sonde et par point de vue, depuis la fin de la Phase 1)** : hors gels d'E/S hôte 100,0 % et 0,0 s d'échec (Windows et `.161`) ;
  brut : 99,989 % (série max 3,6 s) côté Windows, 99,986 % (3,7 s) côté `.161`. Les 5 + 10 échecs bruts sont tous dans **un seul gel de l'hôte** (4 nov. 04:59:56 → 05:00:36 UTC,
  p99 fsync WAL etcd 3,3 à 7,0 s sur 4 membres sur 5) : `HTTP 500` de `/readyz` pendant ~3 s ; **web et API applicative : 0 échec**, latence max 437 ms.
- **Canaris des workers** : 0 échec hors gel, **0 écriture acquittée perdue**, trou max 0,3 s (49 min de mesure). Canari `.153` (informatif) : 0 écriture perdue.
- **Système** : etcd 5/5, CNPG 2/2, opérateur CNPG sans redémarrage depuis 7 h, 3 nœuds Ready, RAM hôte min 2177 Mo sur 10 min.

## Décision C8 (timings etcd) : **NON RETENU**
Données de stabilisation (8 h, `baseline-prometheus-gate1.txt`) : **0 changement de leader** sur les 5 membres, 8 échecs de heartbeat (leader `.155`), 0 proposition échouée,
0 redémarrage de conteneur, 0 transition de nœud, fsync WAL p99 max (fenêtres de 5 min) ≤ 51 ms. Rien n'indique de toucher aux timings par défaut (100 ms / 1000 ms).

## Réserves à garder en tête
- Un faux DOWN du contrôleur DNS survenant plus tard dans la journée n'a pas été observé sur 24 h complètes (E10) ; `p2b_enforce.py` garde son retour arrière automatique.
- `.160` (monitoring, 2 Go) est la VM la plus juste en mémoire : MemAvailable min 217 Mo sur 8 h.

**Suite : Phase 2a** (répétitions de reprise sans couper de VM) — `w2` lancée à 07:24 UTC.
