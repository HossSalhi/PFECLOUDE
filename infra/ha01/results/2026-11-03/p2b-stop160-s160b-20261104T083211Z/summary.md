# Injection stop sur .160 (s160b) — 2026-11-04T08:33:03Z -> 2026-11-04T08:38:05Z

| Critère | Résultat | Mesure |
|---|---|---|
| gels d'E/S de l'hôte pendant le test (informatif ; mesures concernées classées hors gel, E6) | ✅ | aucun |
| [Windows] web >= 99.0 % et série d'échecs <= 10 s (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 2057 ms / BRUT: 100.0 % / 0.0 s / p95 2057 ms |
| [Windows] api_app >= 99.0 % et série d'échecs <= 10 s (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 2065 ms / BRUT: 100.0 % / 0.0 s / p95 2065 ms |
| [Windows] kube_api >= 99.0 % et série d'échecs <= 15 s (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 28 ms / BRUT: 100.0 % / 0.0 s / p95 28 ms |
| [Windows] LB survivant .162 en direct (lb162_web) >= 99,5 % (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 28 ms / BRUT: 100.0 % / 0.0 s / p95 28 ms |
| [Windows] LB survivant .162 en direct (lb162_api) >= 99,5 % (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 27 ms / BRUT: 100.0 % / 0.0 s / p95 27 ms |
| [Windows] contrôle de l'injection : LB visé .160 en direct (lb160_web) en échec pendant >= 80 % de la panne | ✅ | BRUT: 24.873 % / 298.1 s / p95 39 ms |
| [Windows] contrôle de l'injection : LB visé .160 en direct (lb160_api) en échec pendant >= 80 % de la panne | ✅ | BRUT: 24.873 % / 298.1 s / p95 34 ms |
| [.161 sans cache] web >= 99.0 % et série d'échecs <= 10 s (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 21 ms / BRUT: 100.0 % / 0.0 s / p95 21 ms |
| [.161 sans cache] api_app >= 99.0 % et série d'échecs <= 10 s (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 29 ms / BRUT: 100.0 % / 0.0 s / p95 29 ms |
| [.161 sans cache] kube_api >= 99.0 % et série d'échecs <= 15 s (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 28 ms / BRUT: 100.0 % / 0.0 s / p95 28 ms |
| [.161 avec cache] web >= 99.0 % et série d'échecs <= 10 s (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 31 ms / BRUT: 100.0 % / 0.0 s / p95 31 ms |
| [.161 avec cache] api_app >= 99.0 % et série d'échecs <= 10 s (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 40 ms / BRUT: 100.0 % / 0.0 s / p95 40 ms |
| [.161 avec cache] kube_api >= 99.0 % et série d'échecs <= 15 s (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 34 ms / BRUT: 100.0 % / 0.0 s / p95 34 ms |
| canari ha01-canary-w1 : 0 échec hors gel, 0 écriture acquittée perdue | ✅ | acquittées=1726 échecs hors gel=0 (bruts 0) perdues=0 trou max hors gel=0.2s (brut 0.2s) |
| canari ha01-canary-w2 : 0 échec hors gel, 0 écriture acquittée perdue | ✅ | acquittées=1720 échecs hors gel=0 (bruts 0) perdues=0 trou max hors gel=0.3s (brut 0.3s) |
| nœuds : 0 transition NotReady | ✅ | aucune |
| etcd : au moins 5 membres sains pendant tout le test | ✅ | min=5 |
| etcd : <= 2 élections pendant le test (terme Raft) | ✅ | terme 1801 -> 1801, leader 8816046531553481068 -> 8816046531553481068 |
| mémoire : workers >= 300 Mo et .153 >= 500 Mo (minimum mesuré) | ✅ | {'153': 652, '155': 955, '158': 922} |
| 0 OOMKilled et 0 éviction pour mémoire pendant le test | ✅ | OOM=0 évictions=0 |
| RAM hôte >= 1500 Mo (minimum mesuré) | ✅ | 3000 Mo |
| retour : LB sain, HAProxy UP et nœuds Ready en <= 300 s | ✅ | 7 s |
| retour : DNS revenu à son état nominal en <= 180 s | ✅ | 15 s (mode enforce) |
| pas d'abandon | ✅ | None |

## Bascule DNS observée (interrogation directe de dnsmasq toutes les secondes)
- mode du contrôleur : **enforce** ; temps entre l'injection et la sortie du LB visé des réponses de dnsmasq .162 : **0 s**
- temps entre le rétablissement et le retour du LB visé dans les réponses : **—**

## Points de vue (hors gel | brut)
- **Windows** — exclus E6 : 0 échantillons dont 0 échecs
  - `api_app` : 100.0 % / 0.0 s / p95 2065 ms | brut 100.0 % / 0.0 s / p95 2065 ms
  - `kube_api` : 100.0 % / 0.0 s / p95 28 ms | brut 100.0 % / 0.0 s / p95 28 ms
  - `lb160_api` : 24.873 % / 298.1 s / p95 34 ms | brut 24.873 % / 298.1 s / p95 34 ms
    - séries d'échecs (brut) : [['08:33:06', 298.1]]
  - `lb160_web` : 24.873 % / 298.1 s / p95 39 ms | brut 24.873 % / 298.1 s / p95 39 ms
    - séries d'échecs (brut) : [['08:33:06', 298.1]]
  - `lb162_api` : 100.0 % / 0.0 s / p95 27 ms | brut 100.0 % / 0.0 s / p95 27 ms
  - `lb162_web` : 100.0 % / 0.0 s / p95 28 ms | brut 100.0 % / 0.0 s / p95 28 ms
  - `web` : 100.0 % / 0.0 s / p95 2057 ms | brut 100.0 % / 0.0 s / p95 2057 ms
- **.161 sans cache** — exclus E6 : 0 échantillons dont 0 échecs
  - `api_app` : 100.0 % / 0.0 s / p95 29 ms | brut 100.0 % / 0.0 s / p95 29 ms
  - `kube_api` : 100.0 % / 0.0 s / p95 28 ms | brut 100.0 % / 0.0 s / p95 28 ms
  - `lb160_api` : 14.04 % / 300.0 s / p95 22 ms | brut 14.04 % / 300.0 s / p95 22 ms
    - séries d'échecs (brut) : [['08:33:04', 300.0]]
  - `lb160_web` : 14.04 % / 300.0 s / p95 33 ms | brut 14.04 % / 300.0 s / p95 33 ms
    - séries d'échecs (brut) : [['08:33:04', 300.0]]
  - `lb162_api` : 100.0 % / 0.0 s / p95 16 ms | brut 100.0 % / 0.0 s / p95 16 ms
  - `lb162_web` : 100.0 % / 0.0 s / p95 32 ms | brut 100.0 % / 0.0 s / p95 32 ms
  - `web` : 100.0 % / 0.0 s / p95 21 ms | brut 100.0 % / 0.0 s / p95 21 ms
- **.161 avec cache** — exclus E6 : 0 échantillons dont 0 échecs
  - `api_app` : 100.0 % / 0.0 s / p95 40 ms | brut 100.0 % / 0.0 s / p95 40 ms
  - `kube_api` : 100.0 % / 0.0 s / p95 34 ms | brut 100.0 % / 0.0 s / p95 34 ms
  - `web` : 100.0 % / 0.0 s / p95 31 ms | brut 100.0 % / 0.0 s / p95 31 ms

## Baux de nœuds (âge max du dernier renouvellement, s) — un nœud passe NotReady vers 40 s
- {'k8s-master': 10.3, 'k8s-worker1': 10.3, 'k8s-worker2': 10.2}
