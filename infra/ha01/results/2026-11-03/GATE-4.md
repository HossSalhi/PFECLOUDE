# GATE-4 — Phase 4 : preuves RÉELLES de la perte d'UNE VM (`.160`, `.155`, `.158`)

**Verdict (4 nov. 2026, 15:58 UTC) : VALIDÉ AVEC RÉSERVES — pour TROIS VM seulement (`.160`, `.155`, `.158`). La perte réelle de `.162` n'a PAS été testée (réserve R9) : ne pas déduire de ce document que « les quatre » sont validées.** Pour chacune des trois VM, un arrêt brutal réel (`vmrun stop hard`, pas un drain, pas une simulation) a été mesuré pendant 10 min
depuis 3 points de vue (poste Windows ; `.161` sans cache DNS ; `.161` avec cache DNS), suivi de la relance et de 30 min de stabilisation. **Tous les critères de service du §F du plan sont tenus
dans l'essai final de chaque VM** : web, API applicative, API Kubernetes, écritures PostgreSQL, quorum etcd, mémoire, retour à l'état sain. Les chiffres détaillés (brut ET « hors gel »,
tous les critères de tous les essais, y compris les échecs) sont dans [`GATE-4-mesures.md`](GATE-4-mesures.md), généré par `ops/gate4_report.py` sans saisie manuelle.

## 1. Résultats

| VM arrêtée | Essai final | Score | Web (Windows) | `api_app` | API Kubernetes | Écritures PostgreSQL | Retour |
|---|---|---|---|---|---|---|---|
| `.160` (HAProxy, DNS, monitoring, membre etcd) | 09:33Z | **29/29** ✅ | 100 % / série 0 s | 100 % | 100 % | 0 échec, 0 perdue | SSH 55 s, etcd 5/5 65 s, DNS nominal 55 s |
| `.155` (leader etcd, réplique PostgreSQL) | 3e essai 14:21Z | **27/27** ✅ | 99,83 % / série 1,0 s | 100 % | 100 % | 0 échec du survivant (trou 0,3 s), 0 perdue | Ready 53 s, etcd 5/5 39 s, CNPG 2/2 96 s |
| `.158` (PRIMAIRE PostgreSQL) | 15:13Z | **26/27** | 99,66 % / série 6,0 s | 99,83 % | 100 % | **reprise en 54 s** (critère ≤ 120 s), **0 écriture acquittée perdue** | Ready 49 s, etcd 5/5 59 s, CNPG 2/2 276 s |

Dans les trois essais : nœud mort `NotReady` à 45-46 s, quorum etcd 4/4 en 1-2 s, aucune transition `NotReady` sur les autres nœuds, mémoire minimale mesurée : `.153` ≥ 572 Mo et workers survivants ≥ 659 Mo,
0 OOMKilled, 0 éviction. Pour `.158`, la base bascule vers `postgres-5` (perte du primaire vue à T+41 s, bascule à T+51 s) ; côté application, le backend Java survivant ne journalise que 6 avertissements
Hikari (connexions fermées, 15:13:30-15:13:59Z) puis plus aucune erreur JDBC : le pool se reconnecte au nouveau primaire sans intervention (`p4_appdb_evidence.py`).

**Le seul ❌ de l'essai `.158` n'est pas une défaillance de service** : critère de stabilisation « aucun pod non sain » — l'opérateur CNPG (épinglé sur `.153`) a redémarré 4 fois en perdant son bail
(sortie code 1) après un **gel d'E/S de l'hôte de 45 s (15:24:39-15:25:24Z)** puis une **rafale CPU de `.153`** (15:28:26Z). Il n'a eu aucun effet sur les services (la bascule avait eu lieu à T+51 s, bien avant),
mais voir la réserve R3.

## 2. Ce que les essais réels ont révélé (et que les répétitions « graceful » n'avaient pas vu)

| # | Défaut réel | Mesure | Correctif | Preuve |
|---|---|---|---|---|
| W1/E16 | Le contrôle de santé HAProxy `GET /` traversait Traefik puis le frontend : à la mort d'un worker, une fois sur deux il tombait sur le pod mort (timeout 3 s) et le **survivant était déclaré DOWN** → « no server available ». En plus : (a) **les délais de Traefik (`serversTransport.*`) ne s'appliquent PAS aux services du fournisseur Ingress NGINX** (connexion 60 s par défaut) ; (b) nginx du frontend → IP de cluster `backend` : un seul candidat, aucune reprise, connexion 10 s | `.155` essai 1 : web 93 % / série 44,7 s ; essai 2 : 96 % / 11-32 s | HAProxy `GET /ping` :8082 + connexion 1 s ; Traefik `proxyconnecttimeout=1` + 4 tentatives ; nginx → Service headless `backend-headless` (connexion 1 s, reprise, `keepalive_timeout 0`) | reproduit SANS éteindre de VM (`p4_webpath_blackhole.py`), puis essai réel : web 99,83 % / 1,0 s |
| W2/E14 | La sonde de vivacité du primaire CNPG (contrôle d'isolement) échouait 3 × 10 s quand le nœud de la réplique mourait → le kubelet **redémarrait le primaire** (9 s d'écritures en échec) | `.155` essai 1 : 19 échecs, trou 9,2 s | `failureThreshold` 3 → 9 | essais 2 et 3 : 0 échec du canari survivant |
| E13 (erreur corrigée) | `dialTimeout`/`retry` posés à l'écart E13 n'avaient aucun effet | test de Traefik seul avec un pod muet : 9/16 requêtes bloquées > 8 s | voir E16 | 24/24 réussies, max 3,0 s |
| E15(e) | Défaut d'outillage : `p4_vmstop.py` abandonnait à T+14 s car il lisait « canari muet » quand les 3 dernières lignes de son journal n'étaient pas des accusés (normal pendant un basculement) | 1re tentative `.158` (15:07Z) : VM éteinte ~15 s, rallumée, cluster sain en 2,5 min | durée réelle depuis le dernier accusé | essai `.158` 15:13Z mené à terme |

## 3. Réserves (à lire avant de parler de « HA acquise »)

- **R1 — Un seul essai final par VM.** `.160` et `.158` : un essai chacun ; `.155` : le 3e essai est le bon, après deux échecs instructifs et trois correctifs. La reproductibilité repose sur des scripts déterministes
  (`ops/p4_vmstop.py`), sur 6 injections du chemin web sans éteindre de VM, et sur la répétition de `.155`, mais pas sur une série statistique d'arrêts.
- **R2 — Réplication ASYNCHRONE (RPO ≈ lag, non nul).** 0 écriture acquittée perdue dans les trois essais, mais la garantie contractuelle n'est pas « 0 » (la réplication synchrone bloquerait les écritures si la réplique tombe — hors périmètre).
- **R3 — L'opérateur CNPG reste fragile (épinglé sur `.153`).** Il perd son bail et redémarre lors d'un gel d'E/S de l'hôte ou d'une rafale CPU de `.153` (33 rafales distinctes en 6 h, load1 jusqu'à 41). Pas de conséquence service constatée,
  mais si le primaire mourait PENDANT que l'opérateur est en redémarrage, la bascule serait retardée jusqu'à son retour (double panne : non testée, hors périmètre).
- **R4 — Gels d'E/S de l'hôte** (SSD NVMe, `C:` à 93 %) : **7 gels détectés en ~30 h** (3 nov. 10:02 et 13:55 ; 4 nov. 05:00, 09:05, 10:28, 12:14, 15:24), de 30 à 40 s chacun. Deux des cinq relances de VM (10:27Z et 15:23Z) ont été suivies d'un gel dans la minute : le démarrage d'une VM semble en déclencher. Toujours publiés bruts ET hors gel (E6) ; **aucun n'a chevauché la fenêtre de mesure de 10 min d'un essai final**, mais ils dégradent le retour et la stabilisation (cas de `.158`). Cause matérielle probable, non résolue.
- **R5 — Fenêtre de dégradation de ~45 s** après la mort d'un worker (les pods morts restent dans les endpoints jusqu'au passage `NotReady`) : dans les injections, latence p95 de 2-4 s et 2,5 % (web) à 4,7 % (`api_app`) d'échecs sous 10 req/s pendant ces 45 s, jamais deux de suite ; dans les essais réels finaux, sur 10 min, p95 de 57 ms à 1,05 s (`api_app`, essai `.155` n°3) et au plus 0,35 % d'échecs ; les 6 s de série web de `.158` sont la pire valeur des essais finaux.
- **R6 — Hors périmètre / non fait** : panne de `.153` (abandonnée), double panne, mise à niveau Kubernetes (1.29 hors support), PITR/barman, stockage répliqué. **ingress-nginx n'est pas décommissionné** (accord explicite requis). Le fichier `hosts` Windows n'a pas été modifié.
- **R7 — Dérives constatées, non corrigées** : le manifeste `backend/backend/infra/k8s/frontend-nginx-configmap.yaml` ne correspond pas au ConfigMap vivant d'origine ; l'agent hôte HELIOS (`:9999`, qui peut rallumer un worker) a été suspendu pendant chaque essai (D7) puis rétabli.
- **R9 — `.162` NON testée par arrêt réel de VM.** Seul l'arrêt du SERVICE HAProxy de `.162` a été injecté en Phase 2b (`s162`, 25/25, alors que `.160` était le LB actif du DNS). Depuis l'écart E12, `.162` est le LB ACTIF du DNS : sa perte réelle fait basculer le DNS vers `.160`
  et, d'après la seule mesure de ce type (perte du LB actif `.160` avant E12), un client DNS est coupé **8 s sans cache, 17 s avec cache à TTL, 25 s avec le cache DNS de Windows** — soit au-dessus du seuil du plan (série ≤ 10 s) pour les clients avec cache. Non mesuré sur `.162` : scénario à tester (avec confirmation explicite), et possible amélioration (détection/TTL plus courts).
- **R8 — Topologie PostgreSQL après l'essai `.158`** : le primaire est maintenant `postgres-5` sur `.155` et `postgres-7` (`.158`) est la réplique (CNPG est symétrique ; revenir à l'ancienne répartition = un switchover contrôlé, non nécessaire).

## 4. Retours arrière (tous testés ou à commande unique)
`p4_lbcfg.py --lb 160|162 --rollback` (HAProxy) · `p4_webchange.py --what traefik-provider|frontend-nginx-v2 --rollback` (= `kubectl rollout undo`) · `p3_cnpg_liveness.py --rollback` (seuil 3) ·
sauvegardes `/etc/haproxy/haproxy.cfg.bak-ha01-p4-*` sur les LB · sauvegardes YAML dans chaque dossier `p4-webchange-*`.

## 5. Décisions qui vous reviennent
1. **Décommissionner ingress-nginx** (inactif, retiré par l'amont depuis mars 2026) : irréversible, non fait.
2. Retirer les 2 lignes `helios.orange.tn` du fichier `hosts` Windows (le NRPT existant suffit) : geste UAC, non fait.
3. Traiter la fragilité de `.153` / de l'opérateur CNPG (déplacer l'opérateur, élargir son bail, alléger `.153`) : hors périmètre HA-01.
4. **Tester la perte réelle de `.162`** (LB actif du DNS), avec votre confirmation explicite avant l'arrêt de la VM : non fait (R9).
