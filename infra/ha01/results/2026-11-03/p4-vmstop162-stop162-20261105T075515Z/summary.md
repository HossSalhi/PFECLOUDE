# Arrêt réel de la VM .162 (k8s-lb2) — 2026-11-05T07:56:18Z

| Critère | Résultat | Mesure |
|---|---|---|
| gels d'E/S de l'hôte pendant le test (informatif ; mesures concernées classées hors gel, E6) | ✅ | aucun |
| la VM .162 est bien arrêtée pendant la mesure (contrôle de l'injection) | ✅ | vmrun rc=0  ; VM absente de `vmrun list`: True |
| [Windows] web >= 99.0 % et série d'échecs <= 10 s (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 3077 ms / BRUT: 100.0 % / 0.0 s / p95 3077 ms |
| [Windows] api_app >= 99.0 % et série d'échecs <= 10 s (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 3080 ms / BRUT: 100.0 % / 0.0 s / p95 3080 ms |
| [Windows] kube_api >= 99.0 % et série d'échecs <= 15 s (hors gels) | ❌ | hors gel: 98.094 % / 31.1 s / p95 27 ms / BRUT: 98.094 % / 31.1 s / p95 27 ms |
| [Windows] LB survivant .160 en direct (lb160_web) >= 99,5 % (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 67 ms / BRUT: 100.0 % / 0.0 s / p95 67 ms |
| [Windows] LB survivant .160 en direct (lb160_api) >= 99,5 % (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 26 ms / BRUT: 100.0 % / 0.0 s / p95 26 ms |
| [.161 sans cache] web >= 99.0 % et série d'échecs <= 10 s (hors gels) | ✅ | hors gel: 99.493 % / 7.0 s / p95 63 ms / BRUT: 99.493 % / 7.0 s / p95 63 ms |
| [.161 sans cache] api_app >= 99.0 % et série d'échecs <= 10 s (hors gels) | ✅ | hors gel: 99.323 % / 10.0 s / p95 67 ms / BRUT: 99.323 % / 10.0 s / p95 67 ms |
| [.161 sans cache] kube_api >= 99.0 % et série d'échecs <= 15 s (hors gels) | ✅ | hors gel: 99.323 % / 10.0 s / p95 19 ms / BRUT: 99.323 % / 10.0 s / p95 19 ms |
| [.161 avec cache] web >= 99.0 % et série d'échecs <= 10 s (hors gels) | ❌ | hors gel: 99.153 % / 13.0 s / p95 55 ms / BRUT: 99.153 % / 13.0 s / p95 55 ms |
| [.161 avec cache] api_app >= 99.0 % et série d'échecs <= 10 s (hors gels) | ❌ | hors gel: 99.153 % / 13.0 s / p95 58 ms / BRUT: 99.153 % / 13.0 s / p95 58 ms |
| [.161 avec cache] kube_api >= 99.0 % et série d'échecs <= 15 s (hors gels) | ✅ | hors gel: 99.153 % / 13.0 s / p95 13 ms / BRUT: 99.153 % / 13.0 s / p95 13 ms |
| canari ha01-canary-w1 : 0 écriture acquittée perdue | ✅ | perdues=0 acquittées=3280 |
| canari ha01-canary-w1 (survivant) : 0 échec hors gel | ✅ | échecs hors gel=0 (bruts 0) trou max hors gel=0.3s |
| canari ha01-canary-w2 : 0 écriture acquittée perdue | ✅ | perdues=0 acquittées=3268 |
| canari ha01-canary-w2 (survivant) : 0 échec hors gel | ✅ | échecs hors gel=0 (bruts 0) trou max hors gel=0.3s |
| nœuds : .153 et le survivant sans aucune transition NotReady | ❌ | {'k8s-master': ['2026-11-05T07:57:00Z', '2026-11-05T07:57:10Z']} |
| etcd : 4/4 membres sains en <= 10 s après l'arrêt | ✅ | 1 s |
| etcd : <= 2 élections pendant le test (terme Raft) | ✅ | terme 1820 -> 1820, leader 7691728604125665301 -> 7691728604125665301 |
| mémoire : survivants >= 300 Mo et .153 >= 500 Mo (minimum mesuré) | ✅ | {'153': 649, '155': 833, '158': 940, '160': 612} |
| 0 OOMKilled et 0 éviction pour mémoire pendant le test | ✅ | OOM=0 évictions=0 |
| RAM hôte >= 1500 Mo (minimum mesuré) | ✅ | 3876 Mo |
| placement : pods Pending documentés (anti-affinité = conséquence attendue, pas une panne) | ✅ | {'T+90s': [], 'T+300s': []} |
| retour : SSH, etcd 5/5 et HAProxy UP en <= 300 s après la relance | ✅ | ssh=43 etcd5=43 lb=43 |
| retour : DNS nominal (les 2 LB UP, publication nominale) <= 180 s après la relance | ✅ | 53 s |
| retour : aucun pod non sain (Pending résorbés) | ✅ | 33 s |
| stabilisation 1800 s : nœuds Ready, etcd 5/5, aucun pod non sain, sondes par nom OK (creux etcd isolés expliqués par une rafale CPU de .153 listés à part, E17) | ✅ | aucune anomalie inexpliquée |
| pas d'abandon | ✅ | None |
| bascule DNS : le contrôleur survivant (.160) cesse de publier .162 en <= 15 s après l'arrêt | ✅ | bascule à 11.2 s (modèle hors ligne : 8,1 à 11,1 s) |

## Bascule DNS (interrogation directe de dnsmasq toutes les secondes)
- mode du contrôleur : **enforce** ; sortie du LB .160 des réponses de dnsmasq .162 : **—**

## Bascule DNS vue par chaque client (mesures brutes, `probes/ha01_dnswatch.py`)
- **Windows — résolveur système + cache DNS Windows + NRPT (`api.helios.orange.tn`)** : première erreur à T+0.5 s ; première connexion réussie vers le LB survivant à T+16.7 s ; coupure de **16.1 s** ; réponses périmées (.162) : 17 ; retour de .162 dans la réponse (fail-back) : T+41.4 s après la relance
- **Nœud .158 — glibc, /etc/resolv.conf (.160 puis .162), sans cache système** : première erreur à T+0.7 s ; première connexion réussie vers le LB survivant à T+11.7 s ; coupure de **11.0 s** ; réponses périmées (.162) : 12 ; retour de .162 dans la réponse (fail-back) : T+40.3 s après la relance
- **dnsmasq survivant (.160, 5 Hz)** : cesse de publier .162 à T+11.2 s ; dnsmasq .162 (mort) : dernière réponse à T+1516.9 s

## Points de vue (hors gel | brut)
- **Windows** — exclus E6 : 0 échantillons dont 0 échecs
  - `api_app` : 100.0 % / 0.0 s / p95 3080 ms | brut 100.0 % / 0.0 s / p95 3080 ms
  - `kube_api` : 98.094 % / 31.1 s / p95 27 ms | brut 98.094 % / 31.1 s / p95 27 ms
    - séries d'échecs (brut) : [['07:56:22', 31.1]]
  - `lb160_api` : 100.0 % / 0.0 s / p95 26 ms | brut 100.0 % / 0.0 s / p95 26 ms
  - `lb160_web` : 100.0 % / 0.0 s / p95 67 ms | brut 100.0 % / 0.0 s / p95 67 ms
  - `lb162_api` : 0.0 % / 596.5 s / p95 0 ms | brut 0.0 % / 596.5 s / p95 0 ms
    - séries d'échecs (brut) : [['07:56:22', 596.5]]
  - `lb162_web` : 0.0 % / 596.5 s / p95 0 ms | brut 0.0 % / 596.5 s / p95 0 ms
    - séries d'échecs (brut) : [['07:56:22', 596.5]]
  - `web` : 100.0 % / 0.0 s / p95 3077 ms | brut 100.0 % / 0.0 s / p95 3077 ms
- **.161 sans cache** — exclus E6 : 0 échantillons dont 0 échecs
  - `api_app` : 99.323 % / 10.0 s / p95 67 ms | brut 99.323 % / 10.0 s / p95 67 ms
    - séries d'échecs (brut) : [['07:56:23', 10.0]]
  - `kube_api` : 99.323 % / 10.0 s / p95 19 ms | brut 99.323 % / 10.0 s / p95 19 ms
    - séries d'échecs (brut) : [['07:56:23', 10.0]]
  - `lb160_api` : 100.0 % / 0.0 s / p95 14 ms | brut 100.0 % / 0.0 s / p95 14 ms
  - `lb160_web` : 100.0 % / 0.0 s / p95 57 ms | brut 100.0 % / 0.0 s / p95 57 ms
  - `lb162_api` : 0.0 % / 593.9 s / p95 0 ms | brut 0.0 % / 593.9 s / p95 0 ms
    - séries d'échecs (brut) : [['07:56:22', 593.9]]
  - `lb162_web` : 0.0 % / 593.9 s / p95 0 ms | brut 0.0 % / 593.9 s / p95 0 ms
    - séries d'échecs (brut) : [['07:56:22', 593.9]]
  - `web` : 99.493 % / 7.0 s / p95 63 ms | brut 99.493 % / 7.0 s / p95 63 ms
    - séries d'échecs (brut) : [['07:56:24', 7.0]]
- **.161 avec cache** — exclus E6 : 0 échantillons dont 0 échecs
  - `api_app` : 99.153 % / 13.0 s / p95 58 ms | brut 99.153 % / 13.0 s / p95 58 ms
    - séries d'échecs (brut) : [['07:56:22', 13.0]]
  - `kube_api` : 99.153 % / 13.0 s / p95 13 ms | brut 99.153 % / 13.0 s / p95 13 ms
    - séries d'échecs (brut) : [['07:56:22', 13.0]]
  - `web` : 99.153 % / 13.0 s / p95 55 ms | brut 99.153 % / 13.0 s / p95 55 ms
    - séries d'échecs (brut) : [['07:56:22', 13.0]]

## Chronologie mesurée (secondes après l'arrêt, sauf mention)
- nœud mort NotReady après : None s ; etcd 4/4 après : 1 s ; baux de nœuds (âge max, s) : {'k8s-master': 56.3, 'k8s-worker1': 10.2, 'k8s-worker2': 10.3}
- CNPG pendant la panne : []
- retour (secondes après la relance) : {'ready_s': 33, 'pods_s': 33, 'ssh_s': 43, 'etcd5_s': 43, 'lb_s': 43, 'dns_s': 53}
- pods non Running à T+90 s / T+300 s : {'T+90s': [], 'T+300s': []}
