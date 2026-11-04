# Arrêt réel de la VM .155 (k8s-worker2) — 2026-11-04T12:35:33Z

| Critère | Résultat | Mesure |
|---|---|---|
| gels d'E/S de l'hôte pendant le test (informatif ; mesures concernées classées hors gel, E6) | ✅ | aucun |
| la VM .155 est bien arrêtée pendant la mesure (contrôle de l'injection) | ✅ | vmrun rc=0  ; VM absente de `vmrun list`: True |
| [Windows] web >= 98.0 % et série d'échecs <= 10 s (hors gels) | ❌ | hors gel: 96.416 % / 11.0 s / p95 66 ms / BRUT: 96.416 % / 11.0 s / p95 66 ms |
| [Windows] api_app >= 98.0 % et série d'échecs <= 10 s (hors gels) | ❌ | hors gel: 96.398 % / 32.0 s / p95 69 ms / BRUT: 96.398 % / 32.0 s / p95 69 ms |
| [Windows] kube_api >= 99.0 % et série d'échecs <= 15 s (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 26 ms / BRUT: 100.0 % / 0.0 s / p95 26 ms |
| [.161 sans cache] web >= 98.0 % et série d'échecs <= 10 s (hors gels) | ❌ | hors gel: 96.928 % / 17.0 s / p95 56 ms / BRUT: 96.928 % / 17.0 s / p95 56 ms |
| [.161 sans cache] api_app >= 98.0 % et série d'échecs <= 10 s (hors gels) | ❌ | hors gel: 96.735 % / 32.7 s / p95 57 ms / BRUT: 96.735 % / 32.7 s / p95 57 ms |
| [.161 sans cache] kube_api >= 99.0 % et série d'échecs <= 15 s (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 17 ms / BRUT: 100.0 % / 0.0 s / p95 17 ms |
| [.161 avec cache] web >= 98.0 % et série d'échecs <= 10 s (hors gels) | ❌ | hors gel: 96.239 % / 30.0 s / p95 54 ms / BRUT: 96.239 % / 30.0 s / p95 54 ms |
| [.161 avec cache] api_app >= 98.0 % et série d'échecs <= 10 s (hors gels) | ❌ | hors gel: 96.102 % / 20.0 s / p95 58 ms / BRUT: 96.102 % / 20.0 s / p95 58 ms |
| [.161 avec cache] kube_api >= 99.0 % et série d'échecs <= 15 s (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 17 ms / BRUT: 100.0 % / 0.0 s / p95 17 ms |
| canari ha01-canary-w1 : 0 écriture acquittée perdue | ✅ | perdues=0 acquittées=3273 |
| canari ha01-canary-w1 (survivant) : 0 échec hors gel | ✅ | échecs hors gel=0 (bruts 0) trou max hors gel=0.3s |
| canari ha01-canary-w2 : 0 écriture acquittée perdue (canari du worker éteint) | ✅ | perdues=0 acquittées=27 |
| nœuds : .153 et le survivant sans aucune transition NotReady | ✅ | aucune |
| le nœud k8s-worker2 passe NotReady (comportement attendu) | ✅ | après 44 s |
| etcd : 4/4 membres sains en <= 10 s après l'arrêt | ✅ | 2 s |
| etcd : <= 2 élections pendant le test (terme Raft) | ✅ | terme 1803 -> 1804, leader 8816046531553481068 -> 11635180085688803564 |
| mémoire : survivants >= 300 Mo et .153 >= 500 Mo (minimum mesuré) | ✅ | {'153': 656, '158': 683} |
| 0 OOMKilled et 0 éviction pour mémoire pendant le test | ✅ | OOM=0 évictions=0 |
| RAM hôte >= 1500 Mo (minimum mesuré) | ✅ | 6138 Mo |
| placement : pods Pending documentés (anti-affinité = conséquence attendue, pas une panne) | ✅ | {'T+90s': ['ingress-nginx        ingress-nginx-controller-84977f46dc-7x9cd   1/1   Terminating   0                139m    172.16.126.60     k8s-worker2   <none>   <none>', 'ingress-nginx        ingress-nginx-controller-84977f46dc-8cjxl   0/1   Pending       0                77s     <none>            <none>        <none>   <none>', 'pfe                  backend-75b8d9569b-42lkr                    1/1   Terminating   0                139m    172.16.126.45     k8s-worker2   <none>   <none>', 'pfe                  backend-75b8d9569b-lrwwm                    0/1   Pending       0                77s     <none>            <none>        <none>   <none>', 'pfe                  frontend-56b6646659-cpfc5                   0/1   Pending       0                77s     <none>            <none>        <none>   <none>', 'pfe                  frontend-56b6646659-dpjnh                   1/1   Terminating   0                139m    172.16.126.6      k8s-worker2   <none>   <none>', 'traefik              traefik-588c59bdcb-8tqp6                    0/1   Pending       0                77s     <none>            <none>        <none>   <none>', 'traefik              traefik-588c59bdcb-xt8hs                    1/1   Terminating   0                78m     192.168.148.155   k8s-worker2   <none>   <none>'], 'T+300s': ['ingress-nginx        ingress-nginx-controller-84977f46dc-7x9cd   1/1   Terminating   0                143m    172.16.126.60     k8s-worker2   <none>   <none>', 'ingress-nginx        ingress-nginx-controller-84977f46dc-8cjxl   0/1   Pending       0                4m48s   <none>            <none>        <none>   <none>', 'kube-system          metrics-server-696f6dff9c-gtxn7             1/1   Terminating   0                138m    172.16.126.54     k8s-worker2   <none>   <none>', 'kube-system          metrics-server-696f6dff9c-hn79v             0/1   Pending       0                18s     <none>            <none>        <none>   <none>', 'pfe                  backend-75b8d9569b-42lkr                    1/1   Terminating   0                143m    172.16.126.45     k8s-worker2   <none>   <none>', 'pfe                  backend-75b8d9569b-lrwwm                    0/1   Pending       0                4m48s   <none>            <none>        <none>   <none>', 'pfe                  frontend-56b6646659-cpfc5                   0/1   Pending       0                4m48s   <none>            <none>        <none>   <none>', 'pfe                  frontend-56b6646659-dpjnh                   1/1   Terminating   0                143m    172.16.126.6      k8s-worker2   <none>   <none>', 'pfe                  ha01-canary-w2-59df8bdcd8-4l66z             1/1   Terminating   0                138m    172.16.126.50     k8s-worker2   <none>   <none>', 'pfe                  ha01-canary-w2-59df8bdcd8-gfgnv             0/1   Pending       0                18s     <none>            <none>        <none>   <none>', 'pfe                  postgres-5                                  1/1   Terminating   1 (14m ago)      15m     172.16.126.4      k8s-worker2   <none>   <none>', 'traefik              traefik-588c59bdcb-8tqp6                    0/1   Pending       0                4m48s   <none>            <none>        <none>   <none>', 'traefik              traefik-588c59bdcb-xt8hs                    1/1   Terminating   0                81m     192.168.148.155   k8s-worker2   <none>   <none>']} |
| retour : nœud Ready <= 300 s et etcd 5/5 <= 300 s après la relance | ✅ | ssh=62 ready=62 etcd5=62 |
| retour : CNPG 2/2 sain <= 15 min après la relance | ✅ | 111 s |
| retour : aucun pod non sain (Pending résorbés) | ✅ | 194 s |
| stabilisation 1800 s : nœuds Ready, etcd 5/5, aucun pod non sain, sondes par nom OK | ❌ | ['T+319s etcd < 5', 'T+1325s etcd < 5'] |
| pas d'abandon | ✅ | None |

## Bascule DNS (interrogation directe de dnsmasq toutes les secondes)
- mode du contrôleur : **enforce** ; sortie du LB .160 des réponses de dnsmasq .162 : **—**

## Points de vue (hors gel | brut)
- **Windows** — exclus E6 : 0 échantillons dont 0 échecs
  - `api_app` : 96.398 % / 32.0 s / p95 69 ms | brut 96.398 % / 32.0 s / p95 69 ms
    - séries d'échecs (brut) : [['12:35:41', 32.0]]
  - `kube_api` : 100.0 % / 0.0 s / p95 26 ms | brut 100.0 % / 0.0 s / p95 26 ms
  - `lb160_api` : 100.0 % / 0.0 s / p95 26 ms | brut 100.0 % / 0.0 s / p95 26 ms
  - `lb160_web` : 96.769 % / 8.3 s / p95 66 ms | brut 96.769 % / 8.3 s / p95 66 ms
    - séries d'échecs (brut) : [['12:35:41', 8.3], ['12:35:56', 1.0], ['12:36:03', 1.0], ['12:36:04', 8.0]]
  - `lb162_api` : 100.0 % / 0.0 s / p95 26 ms | brut 100.0 % / 0.0 s / p95 26 ms
  - `lb162_web` : 96.265 % / 26.0 s / p95 67 ms | brut 96.265 % / 26.0 s / p95 67 ms
    - séries d'échecs (brut) : [['12:35:42', 26.0]]
  - `web` : 96.416 % / 11.0 s / p95 66 ms | brut 96.416 % / 11.0 s / p95 66 ms
    - séries d'échecs (brut) : [['12:35:38', 11.0], ['12:35:57', 6.0], ['12:36:04', 8.6]]
- **.161 sans cache** — exclus E6 : 0 échantillons dont 0 échecs
  - `api_app` : 96.735 % / 32.7 s / p95 57 ms | brut 96.735 % / 32.7 s / p95 57 ms
    - séries d'échecs (brut) : [['12:35:39', 32.7]]
  - `kube_api` : 100.0 % / 0.0 s / p95 17 ms | brut 100.0 % / 0.0 s / p95 17 ms
  - `lb160_api` : 99.661 % / 1.7 s / p95 17 ms | brut 99.661 % / 1.7 s / p95 17 ms
    - séries d'échecs (brut) : [['12:35:34', 1.7]]
  - `lb160_web` : 96.416 % / 19.0 s / p95 55 ms | brut 96.416 % / 19.0 s / p95 55 ms
    - séries d'échecs (brut) : [['12:35:40', 1.0], ['12:35:41', 19.0], ['12:36:04', 9.0]]
  - `lb162_api` : 100.0 % / 0.0 s / p95 15 ms | brut 100.0 % / 0.0 s / p95 15 ms
  - `lb162_web` : 96.271 % / 26.0 s / p95 55 ms | brut 96.271 % / 26.0 s / p95 55 ms
    - séries d'échecs (brut) : [['12:35:43', 26.0]]
  - `web` : 96.928 % / 17.0 s / p95 56 ms | brut 96.928 % / 17.0 s / p95 56 ms
    - séries d'échecs (brut) : [['12:35:38', 1.0], ['12:35:43', 17.0], ['12:36:04', 1.0], ['12:36:12', 1.0]]
- **.161 avec cache** — exclus E6 : 0 échantillons dont 0 échecs
  - `api_app` : 96.102 % / 20.0 s / p95 58 ms | brut 96.102 % / 20.0 s / p95 58 ms
    - séries d'échecs (brut) : [['12:35:38', 1.0], ['12:35:43', 20.0], ['12:36:10', 1.0]]
  - `kube_api` : 100.0 % / 0.0 s / p95 17 ms | brut 100.0 % / 0.0 s / p95 17 ms
  - `web` : 96.239 % / 30.0 s / p95 54 ms | brut 96.239 % / 30.0 s / p95 54 ms
    - séries d'échecs (brut) : [['12:35:41', 1.0], ['12:35:43', 30.0]]

## Chronologie mesurée (secondes après l'arrêt, sauf mention)
- nœud mort NotReady après : 44 s ; etcd 4/4 après : 2 s ; baux de nœuds (âge max, s) : {'k8s-master': 9.5, 'k8s-worker1': 10.0, 'k8s-worker2': 603.0}
- CNPG pendant la panne : [(2, 'Cluster in healthy state|postgres-7|2'), (146, 'Cluster in healthy state|postgres-7|1'), (358, 'Waiting for the instances to become active|postgres-7|1')]
- retour (secondes après la relance) : {'ssh_s': 62, 'ready_s': 62, 'etcd5_s': 62, 'cnpg_s': 111, 'pods_s': 194}
- pods non Running à T+90 s / T+300 s : {'T+90s': ['ingress-nginx        ingress-nginx-controller-84977f46dc-7x9cd   1/1   Terminating   0                139m    172.16.126.60     k8s-worker2   <none>   <none>', 'ingress-nginx        ingress-nginx-controller-84977f46dc-8cjxl   0/1   Pending       0                77s     <none>            <none>        <none>   <none>', 'pfe                  backend-75b8d9569b-42lkr                    1/1   Terminating   0                139m    172.16.126.45     k8s-worker2   <none>   <none>', 'pfe                  backend-75b8d9569b-lrwwm                    0/1   Pending       0                77s     <none>            <none>        <none>   <none>', 'pfe                  frontend-56b6646659-cpfc5                   0/1   Pending       0                77s     <none>            <none>        <none>   <none>', 'pfe                  frontend-56b6646659-dpjnh                   1/1   Terminating   0                139m    172.16.126.6      k8s-worker2   <none>   <none>', 'traefik              traefik-588c59bdcb-8tqp6                    0/1   Pending       0                77s     <none>            <none>        <none>   <none>', 'traefik              traefik-588c59bdcb-xt8hs                    1/1   Terminating   0                78m     192.168.148.155   k8s-worker2   <none>   <none>'], 'T+300s': ['ingress-nginx        ingress-nginx-controller-84977f46dc-7x9cd   1/1   Terminating   0                143m    172.16.126.60     k8s-worker2   <none>   <none>', 'ingress-nginx        ingress-nginx-controller-84977f46dc-8cjxl   0/1   Pending       0                4m48s   <none>            <none>        <none>   <none>', 'kube-system          metrics-server-696f6dff9c-gtxn7             1/1   Terminating   0                138m    172.16.126.54     k8s-worker2   <none>   <none>', 'kube-system          metrics-server-696f6dff9c-hn79v             0/1   Pending       0                18s     <none>            <none>        <none>   <none>', 'pfe                  backend-75b8d9569b-42lkr                    1/1   Terminating   0                143m    172.16.126.45     k8s-worker2   <none>   <none>', 'pfe                  backend-75b8d9569b-lrwwm                    0/1   Pending       0                4m48s   <none>            <none>        <none>   <none>', 'pfe                  frontend-56b6646659-cpfc5                   0/1   Pending       0                4m48s   <none>            <none>        <none>   <none>', 'pfe                  frontend-56b6646659-dpjnh                   1/1   Terminating   0                143m    172.16.126.6      k8s-worker2   <none>   <none>', 'pfe                  ha01-canary-w2-59df8bdcd8-4l66z             1/1   Terminating   0                138m    172.16.126.50     k8s-worker2   <none>   <none>', 'pfe                  ha01-canary-w2-59df8bdcd8-gfgnv             0/1   Pending       0                18s     <none>            <none>        <none>   <none>', 'pfe                  postgres-5                                  1/1   Terminating   1 (14m ago)      15m     172.16.126.4      k8s-worker2   <none>   <none>', 'traefik              traefik-588c59bdcb-8tqp6                    0/1   Pending       0                4m48s   <none>            <none>        <none>   <none>', 'traefik              traefik-588c59bdcb-xt8hs                    1/1   Terminating   0                81m     192.168.148.155   k8s-worker2   <none>   <none>']}
