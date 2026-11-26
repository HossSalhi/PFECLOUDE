# Rapport — analyse des 78,5 s d'indisponibilité applicative lors de l'essai VM_DOWN de `.155` (6 nov. 2026)

Mission §6. Toutes les heures sont UTC (heure locale = UTC+1). **T0 = 18:24:52,2Z** (`vmrun stop hard` de `.155`). Sources : sondes HA-01 (`probe-baseline-p0.jsonl`), journaux des controller-manager (conteneurs courant et `--previous`), journaux HAProxy `.160`/`.162`, journaux etcd (`helios-etcd` sur `.162`, pod `etcd-k8s-master`), baux Kubernetes, configuration nginx/Traefik/HAProxy. Les données sont **mesurées** ; les inférences sont signalées.

## 1. Ce qui a été observé (sondes HA-01, fenêtre 18:24:00-18:33:59Z)

| Sonde | Disponibilité | Séries d'échecs |
|---|---|---|
| `api_app` (`https://helios.orange.tn/api/test/health-readiness`, frontend → backend) | 87,7 % (57 échecs/465) | **T0+36,6 s → T0+115,1 s : 78,5 s continues** (24 × `502 Bad Gateway`, 7 × délai de lecture), précédée d'échecs isolés dès T0+14 s ; puis 404 à T0+206-236 s ; puis délais TLS à T0+265-325 s |
| `web` (`https://helios.orange.tn/`) | 95,3 % | blips de 1 s (T0+6, +71, +86, +103 s) ; 404 de T0+206 à +221 s ; délais TLS 36 s + 16 s à T0+266 / +310 s |
| `kube_api` | 95,9 % | 1 série de **40,3 s** (T0+266,7 s) : `SSLEOFError` ×12, `HTTP 500` ×8 |
| `lb160_*` / `lb162_*` | 95,9-97,8 % | mêmes fenêtres |

## 2. Chronologie reconstituée et cause racine

| T0+ | Heure (Z) | Fait | Source |
|---|---|---|---|
| −110 s | 18:22:55 → 18:23:25 | **gel d'E/S de l'hôte de 30 s** (87 s AVANT l'arrêt) | `ha01_hoststall` |
| 0 | 18:24:52,2 | arrêt brutal de `.155`. **`.155` portait le LEADER du kube-controller-manager** (bail acquis à 12:43:53Z, jamais perdu) | journal `--previous` du controller-manager de `.155` |
| +4,8 s | 18:24:57 | HAProxy `.160` marque `ingress_https/node-155` et `ingress_http/node-155` **DOWN** (Layer4 timeout, 2 checks de 2 s) | journal HAProxy |
| +14 s | 18:25:06 | premiers `502` sur `api_app` | sondes |
| +16 s | 18:25:08 | HAProxy marque `k8s_masters/worker2` DOWN | journal HAProxy |
| +36,6 s | 18:25:28 | début de la série ininterrompue d'échecs `api_app` | sondes |
| **+62,5 s** | **18:25:54,7** | `.158` **acquiert le bail** du controller-manager : il fallait attendre l'expiration de `--leader-elect-lease-duration=60s` | journal du controller-manager de `.158` |
| +73 s | 18:26:05 | le contrôleur de nœuds du nouveau leader démarre (« Starting node controller ») ; « Missing timestamp for Node… Assuming now » : **le délai de grâce repart de zéro** | idem |
| **+113,7 s** | **18:26:45,856** | **`NodeNotReady` de `k8s-worker2`** (après `--node-monitor-grace-period`=40 s) ; tous les pods de `.155` passent NotReady en 0,6 s → EndpointSlices mis à jour | idem |
| **+115,1 s** | **18:26:46,5** | **fin de la série d'échecs `api_app`** (18:25:28 + 78,5 s) | sondes |
| +155 s | 18:27:27 | `vmrun start` automatique (VMX_PowerOn) ; +157 s : `poweredOn` | VMware / watchdog |
| +205 s | 18:28:17-18 | HAProxy remet `k8s_masters/worker2` puis `node-155` **UP** (`/readyz` 200 ; `/ping` 200) | journal HAProxy |
| +206 → +236 s | 18:28:18 → 18:28:48 | **404** sur `web`/`api_app`/`lb162_web` toutes les 3 s : Traefik de `.155` répond `/ping` avant d'avoir chargé ses routes | sondes + HAProxy |
| +270 s | 18:29:22,5 | **`slow fdatasync` de 8,59 s** sur etcd `.162` (expected 1 s) ; `.153` : « waiting for ReadIndex response took too long » dès 18:29:14 | journaux etcd |
| +285 s | 18:29:36-37 | élection raft (terme 1852) : le leader etcd passe de `.153` à `.162` ; `etcdserver: leader changed` (18:29:48-53) | journaux etcd/controller-manager |
| +266 → +306 s | 18:29:18 → 18:29:58 | `kube_api` : `SSLEOF`/`HTTP 500` pendant 40 s ; HAProxy marque `k8s_masters/worker1` DOWN à 18:30:24 (`/readyz` 500) puis UP à 18:30:46 | sondes + HAProxy |

### Cause racine (mesurée)
**La détection de la panne du nœud a pris 113,7 s au lieu de ≈ 40-45 s parce que la VM arrêtée hébergeait le leader du kube-controller-manager.** Tant que le leader est mort, personne ne marque le nœud NotReady ni ne retire ses pods des EndpointSlices : le délai est la somme du bail (60 s, réglage `--leader-elect-lease-duration=60s`), du démarrage du nouveau contrôleur de nœuds (11 s) et d'un nouveau délai de grâce de 40 s. La conception HA-01 supposait ≈ 45 s (écart E13/E16) ; elle n'avait pas prévu ce cas. **La série d'échecs se termine à la seconde près (18:26:46,5) au moment où `NodeNotReady` retire le pod mort des points d'accès.**

### Ce qui est PROUVÉ, ce qui ne l'est PAS
- Prouvé : (1) la couche HAProxy a réagi en 5 s (web) et 16 s (API) comme conçu ; (2) la durée du défaut de détection et son origine (leader du controller-manager sur la VM arrêtée) ; (3) la coïncidence temporelle exacte entre le retrait des points d'accès et la fin des échecs ; (4) la cause des 404 (route non chargée, HAProxy rend le serveur UP sur `/ping` seulement) ; (5) la corrélation des échecs TLS/API de T0+265-325 s avec un fsync etcd de 8,6 s et une ré-élection.
- **Non prouvé** : le saut précis qui renvoie 502 à chaque requête entre T0+36 et T0+115 s (Traefik → frontend, ou nginx du frontend → backend). Les journaux d'accès nginx (rotation à 10 Mo) et de Traefik (aucun journal d'accès, niveau INFO) ne conservent pas la fenêtre. La chaîne de reprise (Traefik : `dialTimeout=1s`, 3 tentatives ; nginx : `proxy_connect_timeout 1s`, 2 tentatives, DNS mis en cache 30 s) devrait absorber un pod mort ; pourquoi elle ne l'a pas fait pendant 78 s reste une hypothèse (cumul des délais de reprise, adresse morte tirée en premier pendant le cache DNS de 30 s). Aucune modification de ces délais n'est faite « au hasard ».
- Facteurs confondants à signaler : un gel d'E/S de l'hôte de 30 s s'est terminé 87 s avant l'arrêt (le pré-vol N3 ne contrôlait pas ce critère — voir §4), et le démarrage de la VM a coïncidé avec un fsync de 8,6 s.

## 3. Décomposition du temps (T0 → rétablissement)

| Phase | Durée | Observation |
|---|---|---|
| Détection par HAProxy (couche TCP/HTTP) | 5 s (web), 16 s (API) | conforme à la conception |
| **Détection par Kubernetes (NodeNotReady) et retrait des points d'accès** | **113,7 s** | **cause racine** (leader du controller-manager sur la VM arrêtée) |
| Détection par HELIOS et ouverture de l'incident | 150 s (incident `#4930` à 18:27:22) | dépend de la même détection Kubernetes (`NODE_NOT_READY` lu par l'API) |
| Démarrage de la VM par le watchdog | +155 s après T0 (`vmrun start`), VM `poweredOn` +157 s | un seul démarrage |
| Disponibilité de l'API Kubernetes de `.155` / retour dans HAProxy | +205 s | `/readyz` 200 |
| Retour dans HAProxy du Traefik de `.155` et fenêtre de 404 | +206 → +236 s | route non chargée |
| Rétablissement complet des sondes | +325 s | après la ré-élection etcd |
| Vérification HELIOS (2 relevés effectifs) et `RESOLVED` | +366 s (18:30:58) | `#705` PASS |

## 4. Corrections

**Appliquée à la production : aucune** (toute modification de timeouts du plan de contrôle ou du répartiteur est soumise à autorisation ; voir ci-dessous). **Appliquées au dépôt** : voir §13 du rapport final (garde sur le pré-vol).

| # | Correction préparée | Effet attendu (ESTIMATION, non mesurée) | Risque / pourquoi pas appliquée sans accord |
|---|---|---|---|
| C1 | `kube-controller-manager` (3 manifestes statiques) : `--leader-elect-lease-duration=20s --leader-elect-renew-deadline=15s --leader-elect-retry-period=3s` et `--node-monitor-grace-period=20s` (kubelets : `nodeStatusUpdateFrequency` par défaut 10 s) | détection : 20 + 11 + 20 ≈ **51 s** au lieu de 113,7 s quand le leader est sur la VM arrêtée (−62 s) ; 20 s au lieu de 40 s sinon | **Les 60/40/10 s ont été posés exprès** (écart « CrashLoop → Endpoints figés » du 31 juil.). Les gels d'E/S de l'hôte (7 en ≈ 30 h relevés le 4 nov. ; encore observés le 6 nov. à 11:39Z, 18:22Z et 20:46Z, de 30 à 35 s) dépassent 15 s : le leader démissionnerait (redémarrage du controller-manager, ré-élection 20 s) à chaque gel au lieu de les traverser. Redémarrage des 3 controller-manager, un à la fois. **Autorisation requise** |
| C2 | HAProxy `.160`/`.162`, backends `ingress_http`/`ingress_https` : `rise 20 fastinter 2s` (serveur remis en service seulement après ≈ 38 s de `/ping` consécutifs) | supprime la fenêtre de **404 d'environ 30 s** observée au retour d'un nœud (50 % des requêtes) ; coût : un nœud sain met 38 s au lieu de ≈ 3 s à reprendre sa part | rechargement graceful d'HAProxy (aucune connexion coupée) sur les deux répartiteurs, un à la fois ; fichier canonique `infra/ha01/haproxy/haproxy.cfg` + sauvegarde + `haproxy -c` + retour arrière. **Préparée dans le dépôt, application soumise à autorisation (production)** |
| C3 | Journaux d'accès de Traefik activés (`--accesslog=true`, format JSON, `--accesslog.filters.statuscodes=500-599`, `--accesslog.filters.minduration=1s`) | rend la prochaine analyse concluante (identifier le saut qui renvoie 502) | redémarrage progressif des 2 pods Traefik ; **ne pas le faire pendant la fenêtre d'observation de 24 h** (annoté) |
| C4 | Pré-vol `n3_preflight.py` : critère bloquant « aucun gel d'E/S ou réseau de l'hôte dans les **60** dernières minutes » (fsync WAL ou RTT etcd dégradés sur ≥ 3 membres ; Prometheus `.160` injoignable = NO-GO, fail-closed). **Implémenté le 11 nov. 2026** (60 min pour toutes les cibles, pas seulement `.162`) | évite de lancer un essai juste après un gel (cas du 6 nov. 18:23) | code du dépôt uniquement ; `test_n3_preflight.py` 64/64 |

**Temps d'indisponibilité après correction : NON MESURABLE sans nouvel arrêt réel de VM (interdit sans autorisation). Estimation si C1 était appliquée : durée de la série ≈ 16 s au lieu de 78,5 s, SOUS RÉSERVE que le lien « fin des échecs = retrait des points d'accès » soit bien causal (coïncidence exacte mesurée, causalité non démontrée).**

## 5. Verdict
| Tâche | Verdict |
|---|---|
| Chronologie, première défaillance, cause racine de la détection tardive | **VALIDÉ** (mesuré, recoupé par 4 sources) |
| Cause exacte des 502 continus entre Traefik/nginx/backend | **NON VALIDÉ — PREUVE MANQUANTE** (journaux d'accès absents) ; C3 les produira |
| Correction appliquée en production | **BLOQUÉ / AUTORISATION REQUISE** (C1, C2, C3) |
| Garde supplémentaire du pré-vol (C4) | **CORRIGÉ ET REVÉRIFIÉ** (dépôt, 11 nov. : critère de gel d'hôte sur 60 min, fail-closed ; 64 tests OK) |

## 6. Mise à jour du 11 nov. 2026 — ce que 8 nouveaux arrêts de VM (provoqués par l'utilisateur) ajoutent
`[MODE : RO]` : sources = `helios-watchdog.log`, `vm-power-state.json`, fin des `vmware*.log`, SELECT sur `incident`/`incident_action`. Les 8 arrêts du 7 nov. (13:06 → 20:43 locale) et le 9ᵉ du 9 nov. ont tous été suivis d'**un seul** démarrage du watchdog (`state_before = POWERED_OFF` confirmé). Détail et classement des échecs : `RAPPORT-OBSERVATION-24H.md`.

| Incident | VM | Arrêt (UTC) | Démarrage watchdog (UTC) | Éteinte | Remarque |
|---|---|---|---|---|---|
| 4936 | `.162` | 12:04:33 (estimé) | 12:06:21 | ≈ 108 s | |
| 4937 | `.162` | 12:08:55 (estimé) | 12:36:30 | **≈ 27 min 35 s** | **cooldown backend de 30 min** après l'action du 12:06:14Z |
| 4938 | `.155` | 12:49:08 | 12:51:02 | 114 s | |
| 4976 | `.162` | 14:50:38 | 14:51:47 | 69 s | |
| 4977 | `.158` | 15:23:30 (estimé) | 15:25:53 | ≈ 143 s | |
| 4978 | `.162` | 15:44:35 | 15:45:31 | 56 s | |
| 4979 | `.158` | 15:53:56 (estimé) | 15:56:20 | ≈ 144 s | |
| 5051 | `.158` | 19:40:46 | 19:43:23 | 157 s | |
| 5861 | `.158` | 9 nov. 10:54:54 | 9 nov. 10:56:42 | 108 s | hors fenêtre d'observation |

- **Délai arrêt → démarrage** (cas mesurés par les journaux VMware : 56, 69, 114, 157 s ; les 4 autres sont estimés à partir de la création de l'incident) : cohérent avec la détection par deux observations espacées de ≥ 30 s puis l'appel au watchdog. **Aucun démarrage superflu, aucun double démarrage.**
- **Le cooldown de 30 min a un coût** : le 2ᵉ arrêt de `.162` quatre minutes après le 1ᵉʳ démarrage (incident 4937) l'a laissée éteinte ≈ 27 min 35 s (le watchdog lui-même n'impose que 240 s). C'est le comportement voulu par la conception (anti-battement) mais il rallonge l'indisponibilité d'un répartiteur ; les séries d'échecs `lb162_api`/`lb162_web` de 801 s et 939 s correspondent à cette période. **Décision à prendre (D)** : conserver, ou rendre le cooldown conditionnel (par exemple le lever si la VM est restée éteinte à la demande de l'administrateur) — toute modification est du code backend à redéployer (hors périmètre de cette phase : Docker/CI).
- Séries d'échecs maximales sur la fenêtre de 24 h (sondes cumulées) : `api_app` 697,2 s ; `kube_api` 123,3 s ; `lb160_web` 268,5 s ; `web` 270,4 s ; `lb162_*` 939 s : ordres de grandeur très au-delà des 78,5 s de l'essai du 6 nov., dus à l'accumulation d'arrêts en cascade (non reproductibles par un essai isolé) et, avant 13:06, à des **gels d'hôte** sans arrêt de VM.
- **Cause la plus probable des gels** (avant toute manipulation ; compteurs du SSD relevés le 4 nov. : latence maximale cumulée 5,6 s en lecture et 5,9 s en écriture ; non re-mesurés ici) : le disque de l'hôte (`C:` à 93 %, 6 VM sur le même SSD) ; **pas** la mémoire pendant la fenêtre (RAM libre 1,7-6,3 Go), mais **oui** le 11 nov. (110-430 Mo disponibles, ≈ 35 000 pages/s relues, `.158` presque entièrement paginée : charge 138, NotReady). Aucun réglage HAProxy/etcd ne masque ces gels : décision structurelle (D) sur la mémoire/disque de l'hôte.
