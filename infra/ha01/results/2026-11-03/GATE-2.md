# GATE-2 — VALIDÉ le 4 novembre 2026, 07:52 UTC (08:52 heure locale) : **capacité démontrée**

Deux répétitions de reprise **sans couper de VM** (taint `ha01-rehearsal=1:NoExecute` sur le worker, sans cordon), sous **20 requêtes/s** pendant 10 min,
avec filet de sécurité côté nœud (minuterie systemd) et abandon automatique. Évaluation par `ops/p2a_rehearsal.py` (critères du tableau §F, E6 appliqué : brut = hors gel, aucun gel détecté).

| Répétition | Ce qui a été retiré | Critères | Web / API / API K8s | Canari (survivant) | Mémoire mini (survivant / .153) | RAM hôte mini | Récupération |
|---|---|---|---|---|---|---|---|
| `w2` (`p2a-w2-20261104T072357Z`) | **.155** (leader etcd, réplique PostgreSQL) | **13/13 ✅** | 100 % / 100 % / 100 %, 0 s d'échec | 0 échec, trou max 0,34 s, **0 écriture perdue** | 650 Mo / 666 Mo | 1844 Mo | 59 s |
| `w1b` (`p2a-w1b-20261104T073803Z`) | **.158** (primaire PostgreSQL, après **switchover contrôlé** vers `postgres-5` en 29 s) | **13/13 ✅** | 100 % / 100 % / 100 %, 0 s d'échec | trou max 15,6 s (le switchover), **0 écriture perdue** | 939 Mo / 616 Mo | 3280 Mo | 37 s ; primaire rétabli sur `postgres-7` en 26 s |

## Lecture
- **Un seul worker suffit à porter la charge de production** (20 req/s sur web + API applicative) avec 650 à 939 Mo de mémoire disponible sur le survivant : le « cas serré » redouté pour `.158` (84 % de mémoire) tient avec marge.
- Les pods qui ne tiennent pas sur un seul worker (anti-affinité *required*) restent `Pending` pendant la répétition et se résorbent à la remise en service : conséquence attendue, pas une panne.
- Les deux contrôles ci-dessus sont des **répétitions gracieuses** : ils démontrent la **capacité**, pas la tenue à une vraie perte de VM, qui sera mesurée en Phase 4.

## Incident de préflight (consigné)
Une première tentative de `w1` (07:37:01Z) a été refusée par le préflight : RAM hôte 1848 Mo < 2000 Mo (le working set des VM avait grossi de ~1,3 Go pendant `w2`). Aucune action n'a été effectuée.
Remède non destructif et documenté : `ops/trim_host_ram.ps1` (+1,9 Go disponibles, processus VMware jamais touchés). `w1` a été relancée sous le nom `w1b`.

## Écarts appliqués : E6 (accepté), E7, E8, E10 (décision de l'utilisateur). Aucun nouvel écart.

**Suite : Phase 2b** (injections de service sur les répartiteurs de charge), sans la référence B0 (décision de l'utilisateur de tout faire aujourd'hui, écart E11).
