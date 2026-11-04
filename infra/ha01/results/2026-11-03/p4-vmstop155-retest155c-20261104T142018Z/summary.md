# Arrêt réel de la VM .155 (k8s-worker2) — 2026-11-04T14:21:18Z

| Critère | Résultat | Mesure |
|---|---|---|
| gels d'E/S de l'hôte pendant le test (informatif ; mesures concernées classées hors gel, E6) | ✅ | aucun |
| la VM .155 est bien arrêtée pendant la mesure (contrôle de l'injection) | ✅ | vmrun rc=0  ; VM absente de `vmrun list`: True |
| [Windows] web >= 98.0 % et série d'échecs <= 10 s (hors gels) | ✅ | hors gel: 99.829 % / 1.0 s / p95 67 ms / BRUT: 99.829 % / 1.0 s / p95 67 ms |
| [Windows] api_app >= 98.0 % et série d'échecs <= 10 s (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 1051 ms / BRUT: 100.0 % / 0.0 s / p95 1051 ms |
| [Windows] kube_api >= 99.0 % et série d'échecs <= 15 s (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 27 ms / BRUT: 100.0 % / 0.0 s / p95 27 ms |
| [.161 sans cache] web >= 98.0 % et série d'échecs <= 10 s (hors gels) | ✅ | hors gel: 99.83 % / 1.0 s / p95 75 ms / BRUT: 99.83 % / 1.0 s / p95 75 ms |
| [.161 sans cache] api_app >= 98.0 % et série d'échecs <= 10 s (hors gels) | ✅ | hors gel: 99.828 % / 1.0 s / p95 79 ms / BRUT: 99.828 % / 1.0 s / p95 79 ms |
| [.161 sans cache] kube_api >= 99.0 % et série d'échecs <= 15 s (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 16 ms / BRUT: 100.0 % / 0.0 s / p95 16 ms |
| [.161 avec cache] web >= 98.0 % et série d'échecs <= 10 s (hors gels) | ✅ | hors gel: 99.653 % / 1.0 s / p95 57 ms / BRUT: 99.653 % / 1.0 s / p95 57 ms |
| [.161 avec cache] api_app >= 98.0 % et série d'échecs <= 10 s (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 1009 ms / BRUT: 100.0 % / 0.0 s / p95 1009 ms |
| [.161 avec cache] kube_api >= 99.0 % et série d'échecs <= 15 s (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 16 ms / BRUT: 100.0 % / 0.0 s / p95 16 ms |
| canari ha01-canary-w1 : 0 écriture acquittée perdue | ✅ | perdues=0 acquittées=3274 |
| canari ha01-canary-w1 (survivant) : 0 échec hors gel | ✅ | échecs hors gel=0 (bruts 0) trou max hors gel=0.3s |
| canari ha01-canary-w2 : 0 écriture acquittée perdue (canari du worker éteint) | ✅ | perdues=0 acquittées=27 |
| nœuds : .153 et le survivant sans aucune transition NotReady | ✅ | aucune |
| le nœud k8s-worker2 passe NotReady (comportement attendu) | ✅ | après 45 s |
| etcd : 4/4 membres sains en <= 10 s après l'arrêt | ✅ | 1 s |
| etcd : <= 2 élections pendant le test (terme Raft) | ✅ | terme 1806 -> 1808, leader 8816046531553481068 -> 11635180085688803564 |
| mémoire : survivants >= 300 Mo et .153 >= 500 Mo (minimum mesuré) | ✅ | {'153': 615, '158': 659} |
| 0 OOMKilled et 0 éviction pour mémoire pendant le test | ✅ | OOM=0 évictions=0 |
| RAM hôte >= 1500 Mo (minimum mesuré) | ✅ | 5821 Mo |
| placement : pods Pending documentés (anti-affinité = conséquence attendue, pas une panne) | ✅ | {'T+90s': ['ingress-nginx        ingress-nginx-controller-84977f46dc-8cjxl   1/1   Terminating   0                 107m    172.16.126.29     k8s-worker2   <none>   <none>', 'ingress-nginx        ingress-nginx-controller-84977f46dc-f7s4s   0/1   Pending       0                 71s     <none>            <none>        <none>   <none>', 'pfe                  backend-75b8d9569b-f7qpn                    0/1   Pending       0                 71s     <none>            <none>        <none>   <none>', 'pfe                  backend-75b8d9569b-lrwwm                    1/1   Terminating   0                 107m    172.16.126.5      k8s-worker2   <none>   <none>', 'pfe                  frontend-788c665d7c-ltd5q                   0/1   Pending       0                 71s     <none>            <none>        <none>   <none>', 'pfe                  frontend-788c665d7c-p5vxl                   1/1   Terminating   0                 13m     172.16.126.26     k8s-worker2   <none>   <none>', 'traefik              traefik-5747ccdb79-47fgk                    0/1   Pending       0                 71s     <none>            <none>        <none>   <none>', 'traefik              traefik-5747ccdb79-qbtjr                    1/1   Terminating   0                 30m     192.168.148.155   k8s-worker2   <none>   <none>'], 'T+300s': ['ingress-nginx        ingress-nginx-controller-84977f46dc-8cjxl   1/1   Terminating   0                 110m    172.16.126.29     k8s-worker2   <none>   <none>', 'ingress-nginx        ingress-nginx-controller-84977f46dc-f7s4s   0/1   Pending       0                 4m47s   <none>            <none>        <none>   <none>', 'kube-system          metrics-server-696f6dff9c-2tmpr             0/1   Pending       0                 17s     <none>            <none>        <none>   <none>', 'kube-system          metrics-server-696f6dff9c-hn79v             1/1   Terminating   0                 106m    172.16.126.59     k8s-worker2   <none>   <none>', 'pfe                  backend-75b8d9569b-f7qpn                    0/1   Pending       0                 4m47s   <none>            <none>        <none>   <none>', 'pfe                  backend-75b8d9569b-lrwwm                    1/1   Terminating   0                 110m    172.16.126.5      k8s-worker2   <none>   <none>', 'pfe                  frontend-788c665d7c-ltd5q                   0/1   Pending       0                 4m47s   <none>            <none>        <none>   <none>', 'pfe                  frontend-788c665d7c-p5vxl                   1/1   Terminating   0                 17m     172.16.126.26     k8s-worker2   <none>   <none>', 'pfe                  ha01-canary-w2-59df8bdcd8-9lg82             0/1   Pending       0                 17s     <none>            <none>        <none>   <none>', 'pfe                  ha01-canary-w2-59df8bdcd8-gfgnv             1/1   Terminating   0                 106m    172.16.126.58     k8s-worker2   <none>   <none>', 'pfe                  postgres-5                                  1/1   Terminating   0                 100m    172.16.126.37     k8s-worker2   <none>   <none>', 'traefik              traefik-5747ccdb79-47fgk                    0/1   Pending       0                 4m47s   <none>            <none>        <none>   <none>', 'traefik              traefik-5747ccdb79-qbtjr                    1/1   Terminating   0                 33m     192.168.148.155   k8s-worker2   <none>   <none>']} |
| retour : nœud Ready <= 300 s et etcd 5/5 <= 300 s après la relance | ✅ | ssh=53 ready=53 etcd5=39 |
| retour : CNPG 2/2 sain <= 15 min après la relance | ✅ | 96 s |
| retour : aucun pod non sain (Pending résorbés) | ✅ | 96 s |
| stabilisation 1800 s : nœuds Ready, etcd 5/5, aucun pod non sain, sondes par nom OK (creux etcd isolés expliqués par une rafale CPU de .153 listés à part, E17) | ✅ | aucune anomalie inexpliquée |
| pas d'abandon | ✅ | None |

## Bascule DNS (interrogation directe de dnsmasq toutes les secondes)
- mode du contrôleur : **enforce** ; sortie du LB .160 des réponses de dnsmasq .162 : **—**

## Points de vue (hors gel | brut)
- **Windows** — exclus E6 : 0 échantillons dont 0 échecs
  - `api_app` : 100.0 % / 0.0 s / p95 1051 ms | brut 100.0 % / 0.0 s / p95 1051 ms
  - `kube_api` : 100.0 % / 0.0 s / p95 27 ms | brut 100.0 % / 0.0 s / p95 27 ms
  - `lb160_api` : 99.659 % / 1.0 s / p95 26 ms | brut 99.659 % / 1.0 s / p95 26 ms
    - séries d'échecs (brut) : [['14:25:14', 1.0]]
  - `lb160_web` : 100.0 % / 0.0 s / p95 71 ms | brut 100.0 % / 0.0 s / p95 71 ms
  - `lb162_api` : 99.659 % / 1.0 s / p95 26 ms | brut 99.659 % / 1.0 s / p95 26 ms
    - séries d'échecs (brut) : [['14:25:14', 1.0]]
  - `lb162_web` : 99.829 % / 1.0 s / p95 67 ms | brut 99.829 % / 1.0 s / p95 67 ms
    - séries d'échecs (brut) : [['14:21:48', 1.0]]
  - `web` : 99.829 % / 1.0 s / p95 67 ms | brut 99.829 % / 1.0 s / p95 67 ms
    - séries d'échecs (brut) : [['14:21:38', 1.0]]
- **.161 sans cache** — exclus E6 : 0 échantillons dont 0 échecs
  - `api_app` : 99.828 % / 1.0 s / p95 79 ms | brut 99.828 % / 1.0 s / p95 79 ms
    - séries d'échecs (brut) : [['14:21:55', 1.0]]
  - `kube_api` : 100.0 % / 0.0 s / p95 16 ms | brut 100.0 % / 0.0 s / p95 16 ms
  - `lb160_api` : 100.0 % / 0.0 s / p95 17 ms | brut 100.0 % / 0.0 s / p95 17 ms
  - `lb160_web` : 99.83 % / 1.0 s / p95 62 ms | brut 99.83 % / 1.0 s / p95 62 ms
    - séries d'échecs (brut) : [['14:21:29', 1.0]]
  - `lb162_api` : 100.0 % / 0.0 s / p95 19 ms | brut 100.0 % / 0.0 s / p95 19 ms
  - `lb162_web` : 99.828 % / 1.0 s / p95 56 ms | brut 99.828 % / 1.0 s / p95 56 ms
    - séries d'échecs (brut) : [['14:21:41', 1.0]]
  - `web` : 99.83 % / 1.0 s / p95 75 ms | brut 99.83 % / 1.0 s / p95 75 ms
    - séries d'échecs (brut) : [['14:21:30', 1.0]]
- **.161 avec cache** — exclus E6 : 0 échantillons dont 0 échecs
  - `api_app` : 100.0 % / 0.0 s / p95 1009 ms | brut 100.0 % / 0.0 s / p95 1009 ms
  - `kube_api` : 100.0 % / 0.0 s / p95 16 ms | brut 100.0 % / 0.0 s / p95 16 ms
  - `web` : 99.653 % / 1.0 s / p95 57 ms | brut 99.653 % / 1.0 s / p95 57 ms
    - séries d'échecs (brut) : [['14:21:46', 1.0], ['14:22:04', 1.0]]

## Chronologie mesurée (secondes après l'arrêt, sauf mention)
- nœud mort NotReady après : 45 s ; etcd 4/4 après : 1 s ; baux de nœuds (âge max, s) : {'k8s-master': 8.7, 'k8s-worker1': 10.2, 'k8s-worker2': 603.1}
- CNPG pendant la panne : [(1, 'Cluster in healthy state|postgres-7|2'), (70, 'Cluster in healthy state|postgres-7|1'), (364, 'Waiting for the instances to become active|postgres-7|1')]
- retour (secondes après la relance) : {'etcd5_s': 39, 'ssh_s': 53, 'ready_s': 53, 'pods_s': 96, 'cnpg_s': 96}
- pods non Running à T+90 s / T+300 s : {'T+90s': ['ingress-nginx        ingress-nginx-controller-84977f46dc-8cjxl   1/1   Terminating   0                 107m    172.16.126.29     k8s-worker2   <none>   <none>', 'ingress-nginx        ingress-nginx-controller-84977f46dc-f7s4s   0/1   Pending       0                 71s     <none>            <none>        <none>   <none>', 'pfe                  backend-75b8d9569b-f7qpn                    0/1   Pending       0                 71s     <none>            <none>        <none>   <none>', 'pfe                  backend-75b8d9569b-lrwwm                    1/1   Terminating   0                 107m    172.16.126.5      k8s-worker2   <none>   <none>', 'pfe                  frontend-788c665d7c-ltd5q                   0/1   Pending       0                 71s     <none>            <none>        <none>   <none>', 'pfe                  frontend-788c665d7c-p5vxl                   1/1   Terminating   0                 13m     172.16.126.26     k8s-worker2   <none>   <none>', 'traefik              traefik-5747ccdb79-47fgk                    0/1   Pending       0                 71s     <none>            <none>        <none>   <none>', 'traefik              traefik-5747ccdb79-qbtjr                    1/1   Terminating   0                 30m     192.168.148.155   k8s-worker2   <none>   <none>'], 'T+300s': ['ingress-nginx        ingress-nginx-controller-84977f46dc-8cjxl   1/1   Terminating   0                 110m    172.16.126.29     k8s-worker2   <none>   <none>', 'ingress-nginx        ingress-nginx-controller-84977f46dc-f7s4s   0/1   Pending       0                 4m47s   <none>            <none>        <none>   <none>', 'kube-system          metrics-server-696f6dff9c-2tmpr             0/1   Pending       0                 17s     <none>            <none>        <none>   <none>', 'kube-system          metrics-server-696f6dff9c-hn79v             1/1   Terminating   0                 106m    172.16.126.59     k8s-worker2   <none>   <none>', 'pfe                  backend-75b8d9569b-f7qpn                    0/1   Pending       0                 4m47s   <none>            <none>        <none>   <none>', 'pfe                  backend-75b8d9569b-lrwwm                    1/1   Terminating   0                 110m    172.16.126.5      k8s-worker2   <none>   <none>', 'pfe                  frontend-788c665d7c-ltd5q                   0/1   Pending       0                 4m47s   <none>            <none>        <none>   <none>', 'pfe                  frontend-788c665d7c-p5vxl                   1/1   Terminating   0                 17m     172.16.126.26     k8s-worker2   <none>   <none>', 'pfe                  ha01-canary-w2-59df8bdcd8-9lg82             0/1   Pending       0                 17s     <none>            <none>        <none>   <none>', 'pfe                  ha01-canary-w2-59df8bdcd8-gfgnv             1/1   Terminating   0                 106m    172.16.126.58     k8s-worker2   <none>   <none>', 'pfe                  postgres-5                                  1/1   Terminating   0                 100m    172.16.126.37     k8s-worker2   <none>   <none>', 'traefik              traefik-5747ccdb79-47fgk                    0/1   Pending       0                 4m47s   <none>            <none>        <none>   <none>', 'traefik              traefik-5747ccdb79-qbtjr                    1/1   Terminating   0                 33m     192.168.148.155   k8s-worker2   <none>   <none>']}
