# Arrêt réel de la VM .155 (k8s-worker2) — 2026-11-04T10:16:59Z

| Critère | Résultat | Mesure |
|---|---|---|
| gels d'E/S de l'hôte pendant le test (informatif ; mesures concernées classées hors gel, E6) | ✅ | 11-04 10:28:29 -> 10:29:19 UTC |
| la VM .155 est bien arrêtée pendant la mesure (contrôle de l'injection) | ✅ | vmrun rc=0  ; VM absente de `vmrun list`: True |
| [Windows] web >= 98.0 % et série d'échecs <= 10 s (hors gels) | ❌ | hors gel: 93.074 % / 44.7 s / p95 69 ms / BRUT: 93.074 % / 44.7 s / p95 69 ms |
| [Windows] api_app >= 98.0 % et série d'échecs <= 10 s (hors gels) | ❌ | hors gel: 93.22 % / 44.7 s / p95 73 ms / BRUT: 93.22 % / 44.7 s / p95 73 ms |
| [Windows] kube_api >= 99.0 % et série d'échecs <= 15 s (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 29 ms / BRUT: 100.0 % / 0.0 s / p95 29 ms |
| [.161 sans cache] web >= 98.0 % et série d'échecs <= 10 s (hors gels) | ❌ | hors gel: 93.098 % / 39.0 s / p95 79 ms / BRUT: 93.098 % / 39.0 s / p95 79 ms |
| [.161 sans cache] api_app >= 98.0 % et série d'échecs <= 10 s (hors gels) | ❌ | hors gel: 93.074 % / 42.0 s / p95 82 ms / BRUT: 93.074 % / 42.0 s / p95 82 ms |
| [.161 sans cache] kube_api >= 99.0 % et série d'échecs <= 15 s (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 22 ms / BRUT: 100.0 % / 0.0 s / p95 22 ms |
| [.161 avec cache] web >= 98.0 % et série d'échecs <= 10 s (hors gels) | ❌ | hors gel: 92.736 % / 42.0 s / p95 74 ms / BRUT: 92.736 % / 42.0 s / p95 74 ms |
| [.161 avec cache] api_app >= 98.0 % et série d'échecs <= 10 s (hors gels) | ❌ | hors gel: 92.568 % / 45.0 s / p95 62 ms / BRUT: 92.568 % / 45.0 s / p95 62 ms |
| [.161 avec cache] kube_api >= 99.0 % et série d'échecs <= 15 s (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 28 ms / BRUT: 100.0 % / 0.0 s / p95 28 ms |
| canari ha01-canary-w1 : 0 écriture acquittée perdue | ✅ | perdues=0 acquittées=3228 |
| canari ha01-canary-w1 (survivant) : 0 échec hors gel | ❌ | échecs hors gel=19 (bruts 19) trou max hors gel=9.2s |
| canari ha01-canary-w2 : 0 écriture acquittée perdue (canari du worker éteint) | ✅ | perdues=0 acquittées=27 |
| nœuds : .153 et le survivant sans aucune transition NotReady | ✅ | aucune |
| le nœud k8s-worker2 passe NotReady (comportement attendu) | ✅ | après 45 s |
| etcd : 4/4 membres sains en <= 10 s après l'arrêt | ✅ | 2 s |
| etcd : <= 2 élections pendant le test (terme Raft) | ✅ | terme 1801 -> 1802, leader 8816046531553481068 -> 11635180085688803564 |
| mémoire : survivants >= 300 Mo et .153 >= 500 Mo (minimum mesuré) | ✅ | {'153': 593, '158': 754} |
| 0 OOMKilled et 0 éviction pour mémoire pendant le test | ✅ | OOM=0 évictions=0 |
| RAM hôte >= 1500 Mo (minimum mesuré) | ✅ | 6270 Mo |
| placement : pods Pending documentés (anti-affinité = conséquence attendue, pas une panne) | ✅ | {'T+90s': ['ingress-nginx        ingress-nginx-controller-84977f46dc-7x9cd   0/1   Pending       0               67s    <none>            <none>        <none>   <none>', 'ingress-nginx        ingress-nginx-controller-84977f46dc-q8bdn   1/1   Terminating   0               61m    172.16.126.62     k8s-worker2   <none>   <none>', 'pfe                  backend-75b8d9569b-42lkr                    0/1   Pending       0               67s    <none>            <none>        <none>   <none>', 'pfe                  backend-75b8d9569b-w6l28                    1/1   Terminating   0               61m    172.16.126.12     k8s-worker2   <none>   <none>', 'pfe                  frontend-56b6646659-dpjnh                   0/1   Pending       0               67s    <none>            <none>        <none>   <none>', 'pfe                  frontend-56b6646659-qxrmw                   1/1   Terminating   0               61m    172.16.126.13     k8s-worker2   <none>   <none>', 'traefik              traefik-555f9854c-lg4tr                     1/1   Terminating   0               61m    172.16.126.61     k8s-worker2   <none>   <none>', 'traefik              traefik-555f9854c-xmznv                     0/1   Pending       0               67s    <none>            <none>        <none>   <none>'], 'T+300s': ['ingress-nginx        ingress-nginx-controller-84977f46dc-7x9cd   0/1   Pending       0               4m39s   <none>            <none>        <none>   <none>', 'ingress-nginx        ingress-nginx-controller-84977f46dc-q8bdn   1/1   Terminating   0               64m     172.16.126.62     k8s-worker2   <none>   <none>', 'kube-system          metrics-server-696f6dff9c-fnph9             1/1   Terminating   0               64m     172.16.126.28     k8s-worker2   <none>   <none>', 'kube-system          metrics-server-696f6dff9c-gtxn7             0/1   Pending       0               9s      <none>            <none>        <none>   <none>', 'pfe                  backend-75b8d9569b-42lkr                    0/1   Pending       0               4m39s   <none>            <none>        <none>   <none>', 'pfe                  backend-75b8d9569b-w6l28                    1/1   Terminating   0               64m     172.16.126.12     k8s-worker2   <none>   <none>', 'pfe                  frontend-56b6646659-dpjnh                   0/1   Pending       0               4m39s   <none>            <none>        <none>   <none>', 'pfe                  frontend-56b6646659-qxrmw                   1/1   Terminating   0               64m     172.16.126.13     k8s-worker2   <none>   <none>', 'pfe                  ha01-canary-w2-59df8bdcd8-4l66z             0/1   Pending       0               9s      <none>            <none>        <none>   <none>', 'pfe                  ha01-canary-w2-59df8bdcd8-ndxg9             1/1   Terminating   0               64m     172.16.126.47     k8s-worker2   <none>   <none>', 'pfe                  postgres-5                                  1/1   Terminating   0               64m     172.16.126.23     k8s-worker2   <none>   <none>', 'traefik              traefik-555f9854c-lg4tr                     1/1   Terminating   0               64m     172.16.126.61     k8s-worker2   <none>   <none>', 'traefik              traefik-555f9854c-xmznv                     0/1   Pending       0               4m39s   <none>            <none>        <none>   <none>']} |
| retour : nœud Ready <= 300 s et etcd 5/5 <= 300 s après la relance | ✅ | ssh=55 ready=55 etcd5=261 |
| retour : CNPG 2/2 sain <= 15 min après la relance | ✅ | 352 s |
| retour : aucun pod non sain (Pending résorbés) | ✅ | 151 s |
| stabilisation 1800 s : nœuds Ready, etcd 5/5, aucun pod non sain, sondes par nom OK | ❌ | ['T+38s etcd < 5'] |
| pas d'abandon | ✅ | None |

## Bascule DNS (interrogation directe de dnsmasq toutes les secondes)
- mode du contrôleur : **enforce** ; sortie du LB .160 des réponses de dnsmasq .162 : **—**

## Points de vue (hors gel | brut)
- **Windows** — exclus E6 : 0 échantillons dont 0 échecs
  - `api_app` : 93.22 % / 44.7 s / p95 73 ms | brut 93.22 % / 44.7 s / p95 73 ms
    - séries d'échecs (brut) : [['10:17:04', 44.7]]
  - `kube_api` : 100.0 % / 0.0 s / p95 29 ms | brut 100.0 % / 0.0 s / p95 29 ms
  - `lb160_api` : 100.0 % / 0.0 s / p95 26 ms | brut 100.0 % / 0.0 s / p95 26 ms
  - `lb160_web` : 93.243 % / 38.0 s / p95 65 ms | brut 93.243 % / 38.0 s / p95 65 ms
    - séries d'échecs (brut) : [['10:17:04', 1.0], ['10:17:10', 1.0], ['10:17:11', 38.0]]
  - `lb162_api` : 100.0 % / 0.0 s / p95 32 ms | brut 100.0 % / 0.0 s / p95 32 ms
  - `lb162_web` : 92.929 % / 43.0 s / p95 71 ms | brut 92.929 % / 43.0 s / p95 71 ms
    - séries d'échecs (brut) : [['10:17:04', 43.0]]
  - `web` : 93.074 % / 44.7 s / p95 69 ms | brut 93.074 % / 44.7 s / p95 69 ms
    - séries d'échecs (brut) : [['10:17:04', 44.7]]
- **.161 sans cache** — exclus E6 : 0 échantillons dont 0 échecs
  - `api_app` : 93.074 % / 42.0 s / p95 82 ms | brut 93.074 % / 42.0 s / p95 82 ms
    - séries d'échecs (brut) : [['10:17:06', 42.0]]
  - `kube_api` : 100.0 % / 0.0 s / p95 22 ms | brut 100.0 % / 0.0 s / p95 22 ms
  - `lb160_api` : 100.0 % / 0.0 s / p95 25 ms | brut 100.0 % / 0.0 s / p95 25 ms
  - `lb160_web` : 93.39 % / 44.0 s / p95 66 ms | brut 93.39 % / 44.0 s / p95 66 ms
    - séries d'échecs (brut) : [['10:17:04', 44.0]]
  - `lb162_api` : 100.0 % / 0.0 s / p95 25 ms | brut 100.0 % / 0.0 s / p95 25 ms
  - `lb162_web` : 92.929 % / 43.0 s / p95 72 ms | brut 92.929 % / 43.0 s / p95 72 ms
    - séries d'échecs (brut) : [['10:17:04', 43.0]]
  - `web` : 93.098 % / 39.0 s / p95 79 ms | brut 93.098 % / 39.0 s / p95 79 ms
    - séries d'échecs (brut) : [['10:17:04', 1.0], ['10:17:08', 39.0]]
- **.161 avec cache** — exclus E6 : 0 échantillons dont 0 échecs
  - `api_app` : 92.568 % / 45.0 s / p95 62 ms | brut 92.568 % / 45.0 s / p95 62 ms
    - séries d'échecs (brut) : [['10:17:04', 45.0]]
  - `kube_api` : 100.0 % / 0.0 s / p95 28 ms | brut 100.0 % / 0.0 s / p95 28 ms
  - `web` : 92.736 % / 42.0 s / p95 74 ms | brut 92.736 % / 42.0 s / p95 74 ms
    - séries d'échecs (brut) : [['10:17:06', 42.0]]

## Chronologie mesurée (secondes après l'arrêt, sauf mention)
- nœud mort NotReady après : 45 s ; etcd 4/4 après : 2 s ; baux de nœuds (âge max, s) : {'k8s-master': 10.1, 'k8s-worker1': 10.4, 'k8s-worker2': 598.1}
- CNPG pendant la panne : [(2, 'Cluster in healthy state|postgres-7|2'), (70, 'Instance Status Extraction Error: HTTP communication issue|postgres-7|'), (214, 'Instance Status Extraction Error: HTTP communication issue|postgres-7|1'), (359, 'Waiting for the instances to become active|postgres-7|1')]
- retour (secondes après la relance) : {'ssh_s': 55, 'ready_s': 55, 'pods_s': 151, 'etcd5_s': 261, 'cnpg_s': 352}
- pods non Running à T+90 s / T+300 s : {'T+90s': ['ingress-nginx        ingress-nginx-controller-84977f46dc-7x9cd   0/1   Pending       0               67s    <none>            <none>        <none>   <none>', 'ingress-nginx        ingress-nginx-controller-84977f46dc-q8bdn   1/1   Terminating   0               61m    172.16.126.62     k8s-worker2   <none>   <none>', 'pfe                  backend-75b8d9569b-42lkr                    0/1   Pending       0               67s    <none>            <none>        <none>   <none>', 'pfe                  backend-75b8d9569b-w6l28                    1/1   Terminating   0               61m    172.16.126.12     k8s-worker2   <none>   <none>', 'pfe                  frontend-56b6646659-dpjnh                   0/1   Pending       0               67s    <none>            <none>        <none>   <none>', 'pfe                  frontend-56b6646659-qxrmw                   1/1   Terminating   0               61m    172.16.126.13     k8s-worker2   <none>   <none>', 'traefik              traefik-555f9854c-lg4tr                     1/1   Terminating   0               61m    172.16.126.61     k8s-worker2   <none>   <none>', 'traefik              traefik-555f9854c-xmznv                     0/1   Pending       0               67s    <none>            <none>        <none>   <none>'], 'T+300s': ['ingress-nginx        ingress-nginx-controller-84977f46dc-7x9cd   0/1   Pending       0               4m39s   <none>            <none>        <none>   <none>', 'ingress-nginx        ingress-nginx-controller-84977f46dc-q8bdn   1/1   Terminating   0               64m     172.16.126.62     k8s-worker2   <none>   <none>', 'kube-system          metrics-server-696f6dff9c-fnph9             1/1   Terminating   0               64m     172.16.126.28     k8s-worker2   <none>   <none>', 'kube-system          metrics-server-696f6dff9c-gtxn7             0/1   Pending       0               9s      <none>            <none>        <none>   <none>', 'pfe                  backend-75b8d9569b-42lkr                    0/1   Pending       0               4m39s   <none>            <none>        <none>   <none>', 'pfe                  backend-75b8d9569b-w6l28                    1/1   Terminating   0               64m     172.16.126.12     k8s-worker2   <none>   <none>', 'pfe                  frontend-56b6646659-dpjnh                   0/1   Pending       0               4m39s   <none>            <none>        <none>   <none>', 'pfe                  frontend-56b6646659-qxrmw                   1/1   Terminating   0               64m     172.16.126.13     k8s-worker2   <none>   <none>', 'pfe                  ha01-canary-w2-59df8bdcd8-4l66z             0/1   Pending       0               9s      <none>            <none>        <none>   <none>', 'pfe                  ha01-canary-w2-59df8bdcd8-ndxg9             1/1   Terminating   0               64m     172.16.126.47     k8s-worker2   <none>   <none>', 'pfe                  postgres-5                                  1/1   Terminating   0               64m     172.16.126.23     k8s-worker2   <none>   <none>', 'traefik              traefik-555f9854c-lg4tr                     1/1   Terminating   0               64m     172.16.126.61     k8s-worker2   <none>   <none>', 'traefik              traefik-555f9854c-xmznv                     0/1   Pending       0               4m39s   <none>            <none>        <none>   <none>']}
