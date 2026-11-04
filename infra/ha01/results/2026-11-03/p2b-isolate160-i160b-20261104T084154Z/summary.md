# Injection isolate sur .160 (i160b) — 2026-11-04T08:44:36Z -> 2026-11-04T08:48:37Z

| Critère | Résultat | Mesure |
|---|---|---|
| gels d'E/S de l'hôte pendant le test (informatif ; mesures concernées classées hors gel, E6) | ✅ | aucun |
| [Windows] web >= 99.0 % et série d'échecs <= 10 s (hors gels) | ❌ | hors gel: 98.611 % / 1.0 s / p95 3038 ms / BRUT: 98.611 % / 1.0 s / p95 3038 ms |
| [Windows] api_app >= 99.0 % et série d'échecs <= 10 s (hors gels) | ❌ | hors gel: 98.611 % / 1.0 s / p95 3043 ms / BRUT: 98.611 % / 1.0 s / p95 3043 ms |
| [Windows] kube_api >= 99.0 % et série d'échecs <= 15 s (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 37 ms / BRUT: 100.0 % / 0.0 s / p95 37 ms |
| [Windows] LB survivant .162 en direct (lb162_web) >= 99,5 % (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 26 ms / BRUT: 100.0 % / 0.0 s / p95 26 ms |
| [Windows] LB survivant .162 en direct (lb162_api) >= 99,5 % (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 27 ms / BRUT: 100.0 % / 0.0 s / p95 27 ms |
| [Windows] contrôle de l'injection : LB visé .160 en direct (lb160_web) en échec pendant >= 80 % de la panne | ✅ | BRUT: 43.357 % / 238.5 s / p95 28 ms |
| [Windows] contrôle de l'injection : LB visé .160 en direct (lb160_api) en échec pendant >= 80 % de la panne | ✅ | BRUT: 41.259 % / 241.5 s / p95 28 ms |
| [.161 sans cache] web >= 99.0 % et série d'échecs <= 10 s (hors gels) | ✅ | hors gel: 99.67 % / 1.0 s / p95 22 ms / BRUT: 99.67 % / 1.0 s / p95 22 ms |
| [.161 sans cache] api_app >= 99.0 % et série d'échecs <= 10 s (hors gels) | ✅ | hors gel: 99.67 % / 1.0 s / p95 24 ms / BRUT: 99.67 % / 1.0 s / p95 24 ms |
| [.161 sans cache] kube_api >= 99.0 % et série d'échecs <= 15 s (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 30 ms / BRUT: 100.0 % / 0.0 s / p95 30 ms |
| [.161 avec cache] web >= 99.0 % et série d'échecs <= 10 s (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 31 ms / BRUT: 100.0 % / 0.0 s / p95 31 ms |
| [.161 avec cache] api_app >= 99.0 % et série d'échecs <= 10 s (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 35 ms / BRUT: 100.0 % / 0.0 s / p95 35 ms |
| [.161 avec cache] kube_api >= 99.0 % et série d'échecs <= 15 s (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 29 ms / BRUT: 100.0 % / 0.0 s / p95 29 ms |
| canari ha01-canary-w1 : 0 échec hors gel, 0 écriture acquittée perdue | ✅ | acquittées=1513 échecs hors gel=0 (bruts 0) perdues=0 trou max hors gel=0.2s (brut 0.2s) |
| canari ha01-canary-w2 : 0 échec hors gel, 0 écriture acquittée perdue | ✅ | acquittées=1511 échecs hors gel=0 (bruts 0) perdues=0 trou max hors gel=0.3s (brut 0.3s) |
| nœuds : 0 transition NotReady | ✅ | aucune |
| etcd : au moins 4 membres sains pendant tout le test | ✅ | min=4 |
| etcd : <= 2 élections pendant le test (terme Raft) | ✅ | terme 1801 -> 1801, leader 8816046531553481068 -> 8816046531553481068 |
| mémoire : workers >= 300 Mo et .153 >= 500 Mo (minimum mesuré) | ✅ | {'153': 635, '155': 932, '158': 900} |
| 0 OOMKilled et 0 éviction pour mémoire pendant le test | ✅ | OOM=0 évictions=0 |
| RAM hôte >= 1500 Mo (minimum mesuré) | ✅ | 3156 Mo |
| retour : LB sain, HAProxy UP et nœuds Ready en <= 300 s | ✅ | 32 s |
| retour : DNS revenu à son état nominal en <= 180 s | ✅ | 26 s (mode enforce) |
| pas d'abandon | ✅ | None |

## Bascule DNS observée (interrogation directe de dnsmasq toutes les secondes)
- mode du contrôleur : **enforce** ; temps entre l'injection et la sortie du LB visé des réponses de dnsmasq .162 : **3 s**
- temps entre le rétablissement et le retour du LB visé dans les réponses : **—**

## Points de vue (hors gel | brut)
- **Windows** — exclus E6 : 0 échantillons dont 0 échecs
  - `api_app` : 98.611 % / 1.0 s / p95 3043 ms | brut 98.611 % / 1.0 s / p95 3043 ms
    - séries d'échecs (brut) : [['08:48:37', 1.0]]
  - `kube_api` : 100.0 % / 0.0 s / p95 37 ms | brut 100.0 % / 0.0 s / p95 37 ms
  - `lb160_api` : 41.259 % / 241.5 s / p95 28 ms | brut 41.259 % / 241.5 s / p95 28 ms
    - séries d'échecs (brut) : [['08:44:39', 241.5]]
  - `lb160_web` : 43.357 % / 238.5 s / p95 28 ms | brut 43.357 % / 238.5 s / p95 28 ms
    - séries d'échecs (brut) : [['08:44:39', 238.5]]
  - `lb162_api` : 100.0 % / 0.0 s / p95 27 ms | brut 100.0 % / 0.0 s / p95 27 ms
  - `lb162_web` : 100.0 % / 0.0 s / p95 26 ms | brut 100.0 % / 0.0 s / p95 26 ms
  - `web` : 98.611 % / 1.0 s / p95 3038 ms | brut 98.611 % / 1.0 s / p95 3038 ms
    - séries d'échecs (brut) : [['08:48:37', 1.0]]
- **.161 sans cache** — exclus E6 : 0 échantillons dont 0 échecs
  - `api_app` : 99.67 % / 1.0 s / p95 24 ms | brut 99.67 % / 1.0 s / p95 24 ms
    - séries d'échecs (brut) : [['08:44:41', 1.0]]
  - `kube_api` : 100.0 % / 0.0 s / p95 30 ms | brut 100.0 % / 0.0 s / p95 30 ms
  - `lb160_api` : 43.151 % / 240.0 s / p95 13 ms | brut 43.151 % / 240.0 s / p95 13 ms
    - séries d'échecs (brut) : [['08:44:40', 240.0]]
  - `lb160_web` : 44.521 % / 237.9 s / p95 25 ms | brut 44.521 % / 237.9 s / p95 25 ms
    - séries d'échecs (brut) : [['08:44:40', 237.9]]
  - `lb162_api` : 100.0 % / 0.0 s / p95 16 ms | brut 100.0 % / 0.0 s / p95 16 ms
  - `lb162_web` : 100.0 % / 0.0 s / p95 21 ms | brut 100.0 % / 0.0 s / p95 21 ms
  - `web` : 99.67 % / 1.0 s / p95 22 ms | brut 99.67 % / 1.0 s / p95 22 ms
    - séries d'échecs (brut) : [['08:44:41', 1.0]]
- **.161 avec cache** — exclus E6 : 0 échantillons dont 0 échecs
  - `api_app` : 100.0 % / 0.0 s / p95 35 ms | brut 100.0 % / 0.0 s / p95 35 ms
  - `kube_api` : 100.0 % / 0.0 s / p95 29 ms | brut 100.0 % / 0.0 s / p95 29 ms
  - `web` : 100.0 % / 0.0 s / p95 31 ms | brut 100.0 % / 0.0 s / p95 31 ms

## Baux de nœuds (âge max du dernier renouvellement, s) — un nœud passe NotReady vers 40 s
- {'k8s-master': 10.2, 'k8s-worker1': 10.1, 'k8s-worker2': 10.3}

## Décision C4 (kubelet .153 : garder `api.helios.orange.tn` ou revenir à l'IP locale)
- **.153 est resté Ready** pendant l'isolation de .160 (bail max 10.2 s) -> **GARDER** `api.helios.orange.tn`.
