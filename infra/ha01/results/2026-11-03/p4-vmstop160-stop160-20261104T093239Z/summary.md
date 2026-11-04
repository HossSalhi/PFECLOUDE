# Arrêt réel de la VM .160 (k8s-monitoring) — 2026-11-04T09:33:40Z

| Critère | Résultat | Mesure |
|---|---|---|
| gels d'E/S de l'hôte pendant le test (informatif ; mesures concernées classées hors gel, E6) | ✅ | aucun |
| la VM .160 est bien arrêtée pendant la mesure (contrôle de l'injection) | ✅ | vmrun rc=0  ; VM absente de `vmrun list`: True |
| [Windows] web >= 99.0 % et série d'échecs <= 10 s (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 3078 ms / BRUT: 100.0 % / 0.0 s / p95 3078 ms |
| [Windows] api_app >= 99.0 % et série d'échecs <= 10 s (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 3082 ms / BRUT: 100.0 % / 0.0 s / p95 3082 ms |
| [Windows] kube_api >= 99.0 % et série d'échecs <= 15 s (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 38 ms / BRUT: 100.0 % / 0.0 s / p95 38 ms |
| [Windows] LB survivant .162 en direct (lb162_web) >= 99,5 % (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 73 ms / BRUT: 100.0 % / 0.0 s / p95 73 ms |
| [Windows] LB survivant .162 en direct (lb162_api) >= 99,5 % (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 29 ms / BRUT: 100.0 % / 0.0 s / p95 29 ms |
| [.161 sans cache] web >= 99.0 % et série d'échecs <= 10 s (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 64 ms / BRUT: 100.0 % / 0.0 s / p95 64 ms |
| [.161 sans cache] api_app >= 99.0 % et série d'échecs <= 10 s (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 58 ms / BRUT: 100.0 % / 0.0 s / p95 58 ms |
| [.161 sans cache] kube_api >= 99.0 % et série d'échecs <= 15 s (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 26 ms / BRUT: 100.0 % / 0.0 s / p95 26 ms |
| [.161 avec cache] web >= 99.0 % et série d'échecs <= 10 s (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 60 ms / BRUT: 100.0 % / 0.0 s / p95 60 ms |
| [.161 avec cache] api_app >= 99.0 % et série d'échecs <= 10 s (hors gels) | ✅ | hors gel: 99.832 % / 1.0 s / p95 64 ms / BRUT: 99.832 % / 1.0 s / p95 64 ms |
| [.161 avec cache] kube_api >= 99.0 % et série d'échecs <= 15 s (hors gels) | ✅ | hors gel: 99.832 % / 1.0 s / p95 17 ms / BRUT: 99.832 % / 1.0 s / p95 17 ms |
| canari ha01-canary-w1 : 0 écriture acquittée perdue | ✅ | perdues=0 acquittées=3276 |
| canari ha01-canary-w1 (survivant) : 0 échec hors gel | ✅ | échecs hors gel=0 (bruts 0) trou max hors gel=0.3s |
| canari ha01-canary-w2 : 0 écriture acquittée perdue | ✅ | perdues=0 acquittées=3263 |
| canari ha01-canary-w2 (survivant) : 0 échec hors gel | ✅ | échecs hors gel=0 (bruts 0) trou max hors gel=0.3s |
| nœuds : .153 et le survivant sans aucune transition NotReady | ✅ | aucune |
| etcd : 4/4 membres sains en <= 10 s après l'arrêt | ✅ | 2 s |
| etcd : <= 2 élections pendant le test (terme Raft) | ✅ | terme 1801 -> 1801, leader 8816046531553481068 -> 8816046531553481068 |
| mémoire : survivants >= 300 Mo et .153 >= 500 Mo (minimum mesuré) | ✅ | {'153': 640, '155': 1036, '158': 726} |
| 0 OOMKilled et 0 éviction pour mémoire pendant le test | ✅ | OOM=0 évictions=0 |
| RAM hôte >= 1500 Mo (minimum mesuré) | ✅ | 4937 Mo |
| placement : pods Pending documentés (anti-affinité = conséquence attendue, pas une panne) | ✅ | {'T+90s': [], 'T+300s': []} |
| retour : SSH, etcd 5/5 et HAProxy UP en <= 300 s après la relance | ✅ | ssh=55 etcd5=65 lb=65 |
| retour : DNS nominal (les 2 LB UP, publication nominale) <= 180 s après la relance | ✅ | 55 s |
| retour : aucun pod non sain (Pending résorbés) | ✅ | 38 s |
| stabilisation 1800 s : nœuds Ready, etcd 5/5, aucun pod non sain, sondes par nom OK | ✅ | aucune anomalie |
| pas d'abandon | ✅ | None |

## Bascule DNS (interrogation directe de dnsmasq toutes les secondes)
- mode du contrôleur : **enforce** ; sortie du LB .160 des réponses de dnsmasq .162 : **4 s**

## Points de vue (hors gel | brut)
- **Windows** — exclus E6 : 0 échantillons dont 0 échecs
  - `api_app` : 100.0 % / 0.0 s / p95 3082 ms | brut 100.0 % / 0.0 s / p95 3082 ms
  - `kube_api` : 100.0 % / 0.0 s / p95 38 ms | brut 100.0 % / 0.0 s / p95 38 ms
  - `lb160_api` : 0.5 % / 596.6 s / p95 109 ms | brut 0.5 % / 596.6 s / p95 109 ms
    - séries d'échecs (brut) : [['09:33:44', 596.6]]
  - `lb160_web` : 0.5 % / 596.6 s / p95 138 ms | brut 0.5 % / 596.6 s / p95 138 ms
    - séries d'échecs (brut) : [['09:33:44', 596.6]]
  - `lb162_api` : 100.0 % / 0.0 s / p95 29 ms | brut 100.0 % / 0.0 s / p95 29 ms
  - `lb162_web` : 100.0 % / 0.0 s / p95 73 ms | brut 100.0 % / 0.0 s / p95 73 ms
  - `web` : 100.0 % / 0.0 s / p95 3078 ms | brut 100.0 % / 0.0 s / p95 3078 ms
- **.161 sans cache** — exclus E6 : 0 échantillons dont 0 échecs
  - `api_app` : 100.0 % / 0.0 s / p95 58 ms | brut 100.0 % / 0.0 s / p95 58 ms
  - `kube_api` : 100.0 % / 0.0 s / p95 26 ms | brut 100.0 % / 0.0 s / p95 26 ms
  - `lb160_api` : 0.364 % / 594.3 s / p95 76 ms | brut 0.364 % / 594.3 s / p95 76 ms
    - séries d'échecs (brut) : [['09:33:44', 594.3]]
  - `lb160_web` : 0.364 % / 594.3 s / p95 95 ms | brut 0.364 % / 594.3 s / p95 95 ms
    - séries d'échecs (brut) : [['09:33:44', 594.3]]
  - `lb162_api` : 100.0 % / 0.0 s / p95 28 ms | brut 100.0 % / 0.0 s / p95 28 ms
  - `lb162_web` : 100.0 % / 0.0 s / p95 61 ms | brut 100.0 % / 0.0 s / p95 61 ms
  - `web` : 100.0 % / 0.0 s / p95 64 ms | brut 100.0 % / 0.0 s / p95 64 ms
- **.161 avec cache** — exclus E6 : 0 échantillons dont 0 échecs
  - `api_app` : 99.832 % / 1.0 s / p95 64 ms | brut 99.832 % / 1.0 s / p95 64 ms
    - séries d'échecs (brut) : [['09:34:03', 1.0]]
  - `kube_api` : 99.832 % / 1.0 s / p95 17 ms | brut 99.832 % / 1.0 s / p95 17 ms
    - séries d'échecs (brut) : [['09:34:03', 1.0]]
  - `web` : 100.0 % / 0.0 s / p95 60 ms | brut 100.0 % / 0.0 s / p95 60 ms

## Chronologie mesurée (secondes après l'arrêt, sauf mention)
- nœud mort NotReady après : None s ; etcd 4/4 après : 2 s ; baux de nœuds (âge max, s) : {'k8s-master': 10.3, 'k8s-worker1': 10.1, 'k8s-worker2': 10.2}
- CNPG pendant la panne : []
- retour (secondes après la relance) : {'ready_s': 38, 'pods_s': 38, 'ssh_s': 55, 'dns_s': 55, 'etcd5_s': 65, 'lb_s': 65}
- pods non Running à T+90 s / T+300 s : {'T+90s': [], 'T+300s': []}
