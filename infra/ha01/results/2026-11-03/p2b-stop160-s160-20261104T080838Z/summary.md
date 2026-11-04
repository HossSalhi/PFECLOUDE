# Injection stop sur .160 (s160) — 2026-11-04T08:09:33Z -> 2026-11-04T08:14:35Z

| Critère | Résultat | Mesure |
|---|---|---|
| gels d'E/S de l'hôte pendant le test (informatif ; mesures concernées classées hors gel, E6) | ✅ | aucun |
| [Windows] web >= 99.0 % et série d'échecs <= 10 s (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 2060 ms / BRUT: 100.0 % / 0.0 s / p95 2060 ms |
| [Windows] api_app >= 99.0 % et série d'échecs <= 10 s (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 2061 ms / BRUT: 100.0 % / 0.0 s / p95 2061 ms |
| [Windows] kube_api >= 99.0 % et série d'échecs <= 15 s (hors gels) | ❌ | hors gel: 96.108 % / 25.2 s / p95 26 ms / BRUT: 96.108 % / 25.2 s / p95 26 ms |
| [Windows] LB survivant .162 en direct (lb162_web) >= 99,5 % (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 27 ms / BRUT: 100.0 % / 0.0 s / p95 27 ms |
| [Windows] LB survivant .162 en direct (lb162_api) >= 99,5 % (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 28 ms / BRUT: 100.0 % / 0.0 s / p95 28 ms |
| [Windows] contrôle de l'injection : LB visé .160 en direct (lb160_web) en échec pendant >= 80 % de la panne | ✅ | BRUT: 24.103 % / 297.9 s / p95 32 ms |
| [Windows] contrôle de l'injection : LB visé .160 en direct (lb160_api) en échec pendant >= 80 % de la panne | ✅ | BRUT: 24.103 % / 297.9 s / p95 40 ms |
| [.161 sans cache] web >= 99.0 % et série d'échecs <= 10 s (hors gels) | ❌ | hors gel: 97.708 % / 8.0 s / p95 24 ms / BRUT: 97.708 % / 8.0 s / p95 24 ms |
| [.161 sans cache] api_app >= 99.0 % et série d'échecs <= 10 s (hors gels) | ❌ | hors gel: 97.708 % / 8.0 s / p95 26 ms / BRUT: 97.708 % / 8.0 s / p95 26 ms |
| [.161 sans cache] kube_api >= 99.0 % et série d'échecs <= 15 s (hors gels) | ❌ | hors gel: 97.701 % / 8.0 s / p95 36 ms / BRUT: 97.701 % / 8.0 s / p95 36 ms |
| [.161 avec cache] web >= 99.0 % et série d'échecs <= 10 s (hors gels) | ❌ | hors gel: 95.115 % / 17.0 s / p95 24 ms / BRUT: 95.115 % / 17.0 s / p95 24 ms |
| [.161 avec cache] api_app >= 99.0 % et série d'échecs <= 10 s (hors gels) | ❌ | hors gel: 95.115 % / 17.0 s / p95 31 ms / BRUT: 95.115 % / 17.0 s / p95 31 ms |
| [.161 avec cache] kube_api >= 99.0 % et série d'échecs <= 15 s (hors gels) | ❌ | hors gel: 95.115 % / 17.0 s / p95 24 ms / BRUT: 95.115 % / 17.0 s / p95 24 ms |
| canari ha01-canary-w1 : 0 échec hors gel, 0 écriture acquittée perdue | ✅ | acquittées=1719 échecs hors gel=0 (bruts 0) perdues=0 trou max hors gel=0.3s (brut 0.3s) |
| canari ha01-canary-w2 : 0 échec hors gel, 0 écriture acquittée perdue | ✅ | acquittées=1711 échecs hors gel=0 (bruts 0) perdues=0 trou max hors gel=0.3s (brut 0.3s) |
| nœuds : 0 transition NotReady | ✅ | aucune |
| etcd : au moins 5 membres sains pendant tout le test | ✅ | min=5 |
| etcd : <= 2 élections pendant le test (terme Raft) | ✅ | terme 1801 -> 1801, leader 8816046531553481068 -> 8816046531553481068 |
| mémoire : workers >= 300 Mo et .153 >= 500 Mo (minimum mesuré) | ✅ | {'153': 647, '155': 968, '158': 922} |
| 0 OOMKilled et 0 éviction pour mémoire pendant le test | ✅ | OOM=0 évictions=0 |
| RAM hôte >= 1500 Mo (minimum mesuré) | ✅ | 3436 Mo |
| retour : LB sain, HAProxy UP et nœuds Ready en <= 300 s | ✅ | 7 s |
| retour : DNS revenu à son état nominal en <= 180 s | ✅ | 14 s (mode enforce) |
| pas d'abandon | ✅ | None |

## Bascule DNS observée (interrogation directe de dnsmasq toutes les secondes)
- mode du contrôleur : **enforce** ; temps entre l'injection et la sortie du LB visé des réponses de dnsmasq .162 : **7 s**
- temps entre le rétablissement et le retour du LB visé dans les réponses : **14 s**

## Points de vue (hors gel | brut)
- **Windows** — exclus E6 : 0 échantillons dont 0 échecs
  - `api_app` : 100.0 % / 0.0 s / p95 2061 ms | brut 100.0 % / 0.0 s / p95 2061 ms
  - `kube_api` : 96.108 % / 25.2 s / p95 26 ms | brut 96.108 % / 25.2 s / p95 26 ms
    - séries d'échecs (brut) : [['08:09:36', 25.2]]
  - `lb160_api` : 24.103 % / 297.9 s / p95 40 ms | brut 24.103 % / 297.9 s / p95 40 ms
    - séries d'échecs (brut) : [['08:09:36', 297.9]]
  - `lb160_web` : 24.103 % / 297.9 s / p95 32 ms | brut 24.103 % / 297.9 s / p95 32 ms
    - séries d'échecs (brut) : [['08:09:36', 297.9]]
  - `lb162_api` : 100.0 % / 0.0 s / p95 28 ms | brut 100.0 % / 0.0 s / p95 28 ms
  - `lb162_web` : 100.0 % / 0.0 s / p95 27 ms | brut 100.0 % / 0.0 s / p95 27 ms
  - `web` : 100.0 % / 0.0 s / p95 2060 ms | brut 100.0 % / 0.0 s / p95 2060 ms
- **.161 sans cache** — exclus E6 : 0 échantillons dont 0 échecs
  - `api_app` : 97.708 % / 8.0 s / p95 26 ms | brut 97.708 % / 8.0 s / p95 26 ms
    - séries d'échecs (brut) : [['08:09:34', 8.0]]
  - `kube_api` : 97.701 % / 8.0 s / p95 36 ms | brut 97.701 % / 8.0 s / p95 36 ms
    - séries d'échecs (brut) : [['08:09:34', 8.0]]
  - `lb160_api` : 14.04 % / 300.0 s / p95 31 ms | brut 14.04 % / 300.0 s / p95 31 ms
    - séries d'échecs (brut) : [['08:09:34', 300.0]]
  - `lb160_web` : 13.754 % / 301.0 s / p95 52 ms | brut 13.754 % / 301.0 s / p95 52 ms
    - séries d'échecs (brut) : [['08:09:34', 301.0]]
  - `lb162_api` : 100.0 % / 0.0 s / p95 16 ms | brut 100.0 % / 0.0 s / p95 16 ms
  - `lb162_web` : 100.0 % / 0.0 s / p95 21 ms | brut 100.0 % / 0.0 s / p95 21 ms
  - `web` : 97.708 % / 8.0 s / p95 24 ms | brut 97.708 % / 8.0 s / p95 24 ms
    - séries d'échecs (brut) : [['08:09:34', 8.0]]
- **.161 avec cache** — exclus E6 : 0 échantillons dont 0 échecs
  - `api_app` : 95.115 % / 17.0 s / p95 31 ms | brut 95.115 % / 17.0 s / p95 31 ms
    - séries d'échecs (brut) : [['08:09:34', 17.0]]
  - `kube_api` : 95.115 % / 17.0 s / p95 24 ms | brut 95.115 % / 17.0 s / p95 24 ms
    - séries d'échecs (brut) : [['08:09:34', 17.0]]
  - `web` : 95.115 % / 17.0 s / p95 24 ms | brut 95.115 % / 17.0 s / p95 24 ms
    - séries d'échecs (brut) : [['08:09:34', 17.0]]

## Baux de nœuds (âge max du dernier renouvellement, s) — un nœud passe NotReady vers 40 s
- {'k8s-master': 36.7, 'k8s-worker1': 10.1, 'k8s-worker2': 10.2}
