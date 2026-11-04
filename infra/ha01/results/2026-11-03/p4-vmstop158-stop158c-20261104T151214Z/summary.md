# Arrêt réel de la VM .158 (k8s-worker1) — 2026-11-04T15:13:13Z

| Critère | Résultat | Mesure |
|---|---|---|
| gels d'E/S de l'hôte pendant le test (informatif ; mesures concernées classées hors gel, E6) | ✅ | 11-04 15:24:39 -> 15:25:24 UTC |
| la VM .158 est bien arrêtée pendant la mesure (contrôle de l'injection) | ✅ | vmrun rc=0  ; VM absente de `vmrun list`: True |
| [Windows] web >= 98.0 % et série d'échecs <= 10 s (hors gels) | ✅ | hors gel: 99.657 % / 6.0 s / p95 75 ms / BRUT: 99.657 % / 6.0 s / p95 75 ms |
| [Windows] api_app >= 98.0 % et série d'échecs <= 10 s (hors gels) | ✅ | hors gel: 99.83 % / 1.0 s / p95 175 ms / BRUT: 99.83 % / 1.0 s / p95 175 ms |
| [Windows] kube_api >= 99.0 % et série d'échecs <= 15 s (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 31 ms / BRUT: 100.0 % / 0.0 s / p95 31 ms |
| [.161 sans cache] web >= 98.0 % et série d'échecs <= 10 s (hors gels) | ✅ | hors gel: 99.828 % / 1.0 s / p95 64 ms / BRUT: 99.828 % / 1.0 s / p95 64 ms |
| [.161 sans cache] api_app >= 98.0 % et série d'échecs <= 10 s (hors gels) | ✅ | hors gel: 99.655 % / 1.0 s / p95 106 ms / BRUT: 99.655 % / 1.0 s / p95 106 ms |
| [.161 sans cache] kube_api >= 99.0 % et série d'échecs <= 15 s (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 26 ms / BRUT: 100.0 % / 0.0 s / p95 26 ms |
| [.161 avec cache] web >= 98.0 % et série d'échecs <= 10 s (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 90 ms / BRUT: 100.0 % / 0.0 s / p95 90 ms |
| [.161 avec cache] api_app >= 98.0 % et série d'échecs <= 10 s (hors gels) | ✅ | hors gel: 99.655 % / 1.0 s / p95 91 ms / BRUT: 99.655 % / 1.0 s / p95 91 ms |
| [.161 avec cache] kube_api >= 99.0 % et série d'échecs <= 15 s (hors gels) | ✅ | hors gel: 100.0 % / 0.0 s / p95 21 ms / BRUT: 100.0 % / 0.0 s / p95 21 ms |
| canari ha01-canary-w1 : écritures acquittées perdues <= 5 (1 s d'écriture à 5/s ; RPO de la réplication asynchrone) | ✅ | perdues=0 acquittées=27 |
| canari ha01-canary-w2 (survivant) : 1re écriture réussie après l'arrêt <= 120 s (plus long trou hors gel) | ✅ | trou max hors gel=54.0s (brut 54.0s) |
| canari ha01-canary-w2 : écritures acquittées perdues <= 5 (1 s d'écriture à 5/s ; RPO de la réplication asynchrone) | ✅ | perdues=0 acquittées=2997 |
| nœuds : .153 et le survivant sans aucune transition NotReady | ✅ | aucune |
| le nœud k8s-worker1 passe NotReady (comportement attendu) | ✅ | après 46 s |
| etcd : 4/4 membres sains en <= 10 s après l'arrêt | ✅ | 1 s |
| etcd : <= 2 élections pendant le test (terme Raft) | ✅ | terme 1808 -> 1808, leader 11635180085688803564 -> 11635180085688803564 |
| mémoire : survivants >= 300 Mo et .153 >= 500 Mo (minimum mesuré) | ✅ | {'153': 572, '155': 966} |
| 0 OOMKilled et 0 éviction pour mémoire pendant le test | ✅ | OOM=0 évictions=0 |
| RAM hôte >= 1500 Mo (minimum mesuré) | ✅ | 5818 Mo |
| placement : pods Pending documentés (anti-affinité = conséquence attendue, pas une panne) | ✅ | {'T+90s': ['ingress-nginx        ingress-nginx-controller-84977f46dc-knxtt   0/1   Pending       0                 16s     <none>            <none>        <none>   <none>', 'ingress-nginx        ingress-nginx-controller-84977f46dc-zz5j8   1/1   Terminating   1 (6m20s ago)     7h35m   172.16.194.127    k8s-worker1   <none>   <none>', 'pfe                  backend-75b8d9569b-4tfvg                    0/1   Pending       0                 16s     <none>            <none>        <none>   <none>', 'pfe                  backend-75b8d9569b-5f6gt                    1/1   Terminating   1 (6m20s ago)     7h35m   172.16.194.110    k8s-worker1   <none>   <none>', 'pfe                  frontend-788c665d7c-784c4                   1/1   Terminating   1 (6m20s ago)     64m     172.16.194.74     k8s-worker1   <none>   <none>', 'pfe                  frontend-788c665d7c-txvtb                   0/1   Pending       0                 16s     <none>            <none>        <none>   <none>', 'traefik              traefik-5747ccdb79-g9kxl                    1/1   Terminating   1 (6m20s ago)     81m     192.168.148.158   k8s-worker1   <none>   <none>', 'traefik              traefik-5747ccdb79-ptg77                    0/1   Pending       0                 16s     <none>            <none>        <none>   <none>'], 'T+300s': ['ingress-nginx        ingress-nginx-controller-84977f46dc-knxtt   0/1   Pending       0                 3m46s   <none>            <none>        <none>   <none>', 'ingress-nginx        ingress-nginx-controller-84977f46dc-zz5j8   1/1   Terminating   1 (9m50s ago)     7h38m   172.16.194.127    k8s-worker1   <none>   <none>', 'pfe                  backend-75b8d9569b-4tfvg                    0/1   Pending       0                 3m46s   <none>            <none>        <none>   <none>', 'pfe                  backend-75b8d9569b-5f6gt                    1/1   Terminating   1 (9m50s ago)     7h38m   172.16.194.110    k8s-worker1   <none>   <none>', 'pfe                  frontend-788c665d7c-784c4                   1/1   Terminating   1 (9m50s ago)     68m     172.16.194.74     k8s-worker1   <none>   <none>', 'pfe                  frontend-788c665d7c-txvtb                   0/1   Pending       0                 3m46s   <none>            <none>        <none>   <none>', 'traefik              traefik-5747ccdb79-g9kxl                    1/1   Terminating   1 (9m50s ago)     84m     192.168.148.158   k8s-worker1   <none>   <none>', 'traefik              traefik-5747ccdb79-ptg77                    0/1   Pending       0                 3m46s   <none>            <none>        <none>   <none>']} |
| retour : nœud Ready <= 300 s et etcd 5/5 <= 300 s après la relance | ✅ | ssh=59 ready=49 etcd5=59 |
| retour : CNPG 2/2 sain <= 15 min après la relance | ✅ | 276 s |
| retour : aucun pod non sain (Pending résorbés) | ✅ | 93 s |
| stabilisation 1800 s : nœuds Ready, etcd 5/5, aucun pod non sain, sondes par nom OK (creux etcd isolés expliqués par une rafale CPU de .153 listés à part, E17) | ❌ | ["T+39s pods non sains: ['cnpg-system          cnpg-controller-manager-6cc9d8689f-8xnmn    0/1   Error     3 (2m53s ago)     16h']"] / expliquées (E17): ['T+6s etcd < 5 (KO: .153) — SSH de .153 expiré à 15:28:26 (rafale CPU)'] |
| pas d'abandon | ✅ | None |

## Bascule DNS (interrogation directe de dnsmasq toutes les secondes)
- mode du contrôleur : **enforce** ; sortie du LB .160 des réponses de dnsmasq .162 : **—**

## Points de vue (hors gel | brut)
- **Windows** — exclus E6 : 0 échantillons dont 0 échecs
  - `api_app` : 99.83 % / 1.0 s / p95 175 ms | brut 99.83 % / 1.0 s / p95 175 ms
    - séries d'échecs (brut) : [['15:13:32', 1.0]]
  - `kube_api` : 100.0 % / 0.0 s / p95 31 ms | brut 100.0 % / 0.0 s / p95 31 ms
  - `lb160_api` : 100.0 % / 0.0 s / p95 32 ms | brut 100.0 % / 0.0 s / p95 32 ms
  - `lb160_web` : 100.0 % / 0.0 s / p95 77 ms | brut 100.0 % / 0.0 s / p95 77 ms
  - `lb162_api` : 100.0 % / 0.0 s / p95 29 ms | brut 100.0 % / 0.0 s / p95 29 ms
  - `lb162_web` : 99.831 % / 1.0 s / p95 89 ms | brut 99.831 % / 1.0 s / p95 89 ms
    - séries d'échecs (brut) : [['15:13:52', 1.0]]
  - `web` : 99.657 % / 6.0 s / p95 75 ms | brut 99.657 % / 6.0 s / p95 75 ms
    - séries d'échecs (brut) : [['15:13:46', 6.0]]
- **.161 sans cache** — exclus E6 : 0 échantillons dont 0 échecs
  - `api_app` : 99.655 % / 1.0 s / p95 106 ms | brut 99.655 % / 1.0 s / p95 106 ms
    - séries d'échecs (brut) : [['15:13:19', 1.0], ['15:13:39', 1.0]]
  - `kube_api` : 100.0 % / 0.0 s / p95 26 ms | brut 100.0 % / 0.0 s / p95 26 ms
  - `lb160_api` : 100.0 % / 0.0 s / p95 24 ms | brut 100.0 % / 0.0 s / p95 24 ms
  - `lb160_web` : 99.829 % / 1.0 s / p95 75 ms | brut 99.829 % / 1.0 s / p95 75 ms
    - séries d'échecs (brut) : [['15:13:36', 1.0]]
  - `lb162_api` : 100.0 % / 0.0 s / p95 27 ms | brut 100.0 % / 0.0 s / p95 27 ms
  - `lb162_web` : 99.484 % / 1.0 s / p95 67 ms | brut 99.484 % / 1.0 s / p95 67 ms
    - séries d'échecs (brut) : [['15:13:29', 1.0], ['15:13:44', 1.0], ['15:14:00', 1.0]]
  - `web` : 99.828 % / 1.0 s / p95 64 ms | brut 99.828 % / 1.0 s / p95 64 ms
    - séries d'échecs (brut) : [['15:13:28', 1.0]]
- **.161 avec cache** — exclus E6 : 0 échantillons dont 0 échecs
  - `api_app` : 99.655 % / 1.0 s / p95 91 ms | brut 99.655 % / 1.0 s / p95 91 ms
    - séries d'échecs (brut) : [['15:13:20', 1.0], ['15:13:39', 1.0]]
  - `kube_api` : 100.0 % / 0.0 s / p95 21 ms | brut 100.0 % / 0.0 s / p95 21 ms
  - `web` : 100.0 % / 0.0 s / p95 90 ms | brut 100.0 % / 0.0 s / p95 90 ms

## Chronologie mesurée (secondes après l'arrêt, sauf mention)
- nœud mort NotReady après : 46 s ; etcd 4/4 après : 1 s ; baux de nœuds (âge max, s) : {'k8s-master': 10.2, 'k8s-worker1': 598.4, 'k8s-worker2': 10.4}
- CNPG pendant la panne : [(1, 'Cluster in healthy state|postgres-7|2'), (41, 'Cluster in healthy state|postgres-7|1'), (51, 'Failing over|postgres-5|1'), (351, 'Waiting for the instances to become active|postgres-5|1')]
- retour (secondes après la relance) : {'ready_s': 49, 'ssh_s': 59, 'etcd5_s': 59, 'pods_s': 93, 'cnpg_s': 276}
- pods non Running à T+90 s / T+300 s : {'T+90s': ['ingress-nginx        ingress-nginx-controller-84977f46dc-knxtt   0/1   Pending       0                 16s     <none>            <none>        <none>   <none>', 'ingress-nginx        ingress-nginx-controller-84977f46dc-zz5j8   1/1   Terminating   1 (6m20s ago)     7h35m   172.16.194.127    k8s-worker1   <none>   <none>', 'pfe                  backend-75b8d9569b-4tfvg                    0/1   Pending       0                 16s     <none>            <none>        <none>   <none>', 'pfe                  backend-75b8d9569b-5f6gt                    1/1   Terminating   1 (6m20s ago)     7h35m   172.16.194.110    k8s-worker1   <none>   <none>', 'pfe                  frontend-788c665d7c-784c4                   1/1   Terminating   1 (6m20s ago)     64m     172.16.194.74     k8s-worker1   <none>   <none>', 'pfe                  frontend-788c665d7c-txvtb                   0/1   Pending       0                 16s     <none>            <none>        <none>   <none>', 'traefik              traefik-5747ccdb79-g9kxl                    1/1   Terminating   1 (6m20s ago)     81m     192.168.148.158   k8s-worker1   <none>   <none>', 'traefik              traefik-5747ccdb79-ptg77                    0/1   Pending       0                 16s     <none>            <none>        <none>   <none>'], 'T+300s': ['ingress-nginx        ingress-nginx-controller-84977f46dc-knxtt   0/1   Pending       0                 3m46s   <none>            <none>        <none>   <none>', 'ingress-nginx        ingress-nginx-controller-84977f46dc-zz5j8   1/1   Terminating   1 (9m50s ago)     7h38m   172.16.194.127    k8s-worker1   <none>   <none>', 'pfe                  backend-75b8d9569b-4tfvg                    0/1   Pending       0                 3m46s   <none>            <none>        <none>   <none>', 'pfe                  backend-75b8d9569b-5f6gt                    1/1   Terminating   1 (9m50s ago)     7h38m   172.16.194.110    k8s-worker1   <none>   <none>', 'pfe                  frontend-788c665d7c-784c4                   1/1   Terminating   1 (9m50s ago)     68m     172.16.194.74     k8s-worker1   <none>   <none>', 'pfe                  frontend-788c665d7c-txvtb                   0/1   Pending       0                 3m46s   <none>            <none>        <none>   <none>', 'traefik              traefik-5747ccdb79-g9kxl                    1/1   Terminating   1 (9m50s ago)     84m     192.168.148.158   k8s-worker1   <none>   <none>', 'traefik              traefik-5747ccdb79-ptg77                    0/1   Pending       0                 3m46s   <none>            <none>        <none>   <none>']}
