# Injection stop sur .162 (s162) — 2026-11-04T08:02:04Z -> 2026-11-04T08:07:06Z

| Critère | Résultat | Mesure |
|---|---|---|
| gels d'E/S de l'hôte pendant le test (informatif ; mesures concernées classées hors gel, E6) | ✅ | aucun |
| [Windows] web >= 99.0 % et série d'échecs <= 10 s (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 34 ms / BRUT: 100.0 % / 0.0 s / p95 34 ms |
| [Windows] api_app >= 99.0 % et série d'échecs <= 10 s (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 41 ms / BRUT: 100.0 % / 0.0 s / p95 41 ms |
| [Windows] kube_api >= 99.0 % et série d'échecs <= 15 s (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 28 ms / BRUT: 100.0 % / 0.0 s / p95 28 ms |
| [Windows] LB survivant .160 en direct (lb160_web) >= 99,5 % (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 36 ms / BRUT: 100.0 % / 0.0 s / p95 36 ms |
| [Windows] LB survivant .160 en direct (lb160_api) >= 99,5 % (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 27 ms / BRUT: 100.0 % / 0.0 s / p95 27 ms |
| [Windows] contrôle de l'injection : LB visé .162 en direct (lb162_web) quasi indisponible | ❌ | BRUT: 24.103 % / 298.1 s / p95 26 ms |
| [Windows] contrôle de l'injection : LB visé .162 en direct (lb162_api) quasi indisponible | ❌ | BRUT: 24.103 % / 298.0 s / p95 24 ms |
| [.161 sans cache] web >= 99.0 % et série d'échecs <= 10 s (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 24 ms / BRUT: 100.0 % / 0.0 s / p95 24 ms |
| [.161 sans cache] api_app >= 99.0 % et série d'échecs <= 10 s (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 31 ms / BRUT: 100.0 % / 0.0 s / p95 31 ms |
| [.161 sans cache] kube_api >= 99.0 % et série d'échecs <= 15 s (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 39 ms / BRUT: 100.0 % / 0.0 s / p95 39 ms |
| [.161 avec cache] web >= 99.0 % et série d'échecs <= 10 s (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 44 ms / BRUT: 100.0 % / 0.0 s / p95 44 ms |
| [.161 avec cache] api_app >= 99.0 % et série d'échecs <= 10 s (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 50 ms / BRUT: 100.0 % / 0.0 s / p95 50 ms |
| [.161 avec cache] kube_api >= 99.0 % et série d'échecs <= 15 s (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 45 ms / BRUT: 100.0 % / 0.0 s / p95 45 ms |
| canari ha01-canary-w1 : 0 échec hors gel, 0 écriture acquittée perdue | ✅ | acquittées=1716 échecs hors gel=0 (bruts 0) perdues=0 trou max hors gel=0.3s (brut 0.3s) |
| canari ha01-canary-w2 : 0 échec hors gel, 0 écriture acquittée perdue | ✅ | acquittées=1710 échecs hors gel=0 (bruts 0) perdues=0 trou max hors gel=0.3s (brut 0.3s) |
| nœuds : 0 transition NotReady | ✅ | aucune |
| etcd : au moins 5 membres sains pendant tout le test | ✅ | min=5 |
| etcd : <= 2 élections pendant le test (terme Raft) | ✅ | terme 1801 -> 1801, leader 8816046531553481068 -> 8816046531553481068 |
| mémoire : workers >= 300 Mo et .153 >= 500 Mo (minimum mesuré) | ✅ | {'153': 638, '155': 958, '158': 946} |
| 0 OOMKilled et 0 éviction pour mémoire pendant le test | ✅ | OOM=0 évictions=0 |
| RAM hôte >= 1500 Mo (minimum mesuré) | ✅ | 3312 Mo |
| retour : LB sain, HAProxy UP et nœuds Ready en <= 300 s | ✅ | 7 s |
| retour : DNS revenu à son état nominal en <= 180 s | ✅ | 14 s (mode enforce) |
| pas d'abandon | ✅ | None |

## Bascule DNS observée (interrogation directe de dnsmasq toutes les secondes)
- mode du contrôleur : **enforce** ; temps entre l'injection et la sortie du LB visé des réponses de dnsmasq .160 : **1 s**
- temps entre le rétablissement et le retour du LB visé dans les réponses : **—**

## Points de vue (hors gel | brut)
- **Windows** — exclus E6 : 0 échantillons dont 0 échecs
  - `api_app` : 100.0 % / 0.0 s / p95 41 ms | brut 100.0 % / 0.0 s / p95 41 ms
  - `kube_api` : 100.0 % / 0.0 s / p95 28 ms | brut 100.0 % / 0.0 s / p95 28 ms
  - `lb160_api` : 100.0 % / 0.0 s / p95 27 ms | brut 100.0 % / 0.0 s / p95 27 ms
  - `lb160_web` : 100.0 % / 0.0 s / p95 36 ms | brut 100.0 % / 0.0 s / p95 36 ms
  - `lb162_api` : 24.103 % / 298.0 s / p95 24 ms | brut 24.103 % / 298.0 s / p95 24 ms
    - séries d'échecs (brut) : [['08:02:07', 298.0]]
  - `lb162_web` : 24.103 % / 298.1 s / p95 26 ms | brut 24.103 % / 298.1 s / p95 26 ms
    - séries d'échecs (brut) : [['08:02:07', 298.1]]
  - `web` : 100.0 % / 0.0 s / p95 34 ms | brut 100.0 % / 0.0 s / p95 34 ms
- **.161 sans cache** — exclus E6 : 0 échantillons dont 0 échecs
  - `api_app` : 100.0 % / 0.0 s / p95 31 ms | brut 100.0 % / 0.0 s / p95 31 ms
  - `kube_api` : 100.0 % / 0.0 s / p95 39 ms | brut 100.0 % / 0.0 s / p95 39 ms
  - `lb160_api` : 100.0 % / 0.0 s / p95 19 ms | brut 100.0 % / 0.0 s / p95 19 ms
  - `lb160_web` : 100.0 % / 0.0 s / p95 22 ms | brut 100.0 % / 0.0 s / p95 22 ms
  - `lb162_api` : 13.545 % / 300.0 s / p95 16 ms | brut 13.545 % / 300.0 s / p95 16 ms
    - séries d'échecs (brut) : [['08:02:05', 300.0]]
  - `lb162_web` : 13.793 % / 300.0 s / p95 16 ms | brut 13.793 % / 300.0 s / p95 16 ms
    - séries d'échecs (brut) : [['08:02:05', 300.0]]
  - `web` : 100.0 % / 0.0 s / p95 24 ms | brut 100.0 % / 0.0 s / p95 24 ms
- **.161 avec cache** — exclus E6 : 0 échantillons dont 0 échecs
  - `api_app` : 100.0 % / 0.0 s / p95 50 ms | brut 100.0 % / 0.0 s / p95 50 ms
  - `kube_api` : 100.0 % / 0.0 s / p95 45 ms | brut 100.0 % / 0.0 s / p95 45 ms
  - `web` : 100.0 % / 0.0 s / p95 44 ms | brut 100.0 % / 0.0 s / p95 44 ms

## Baux de nœuds (âge max du dernier renouvellement, s) — un nœud passe NotReady vers 40 s
- {'k8s-master': 10.3, 'k8s-worker1': 10.0, 'k8s-worker2': 10.2}
