# GATE-4 — mesures des arrêts réels de VM (généré par `ops/gate4_report.py`, rien n'est saisi à la main)

Chaque ligne = un `vmrun stop hard` réel d'UNE VM, 10 min d'observation, relance, 30 min de stabilisation. Les pourcentages sont « disponibilité / plus longue série d'échecs » mesurés HORS gel d'E/S de l'hôte (écart E6) ; le brut est publié dans le détail de chaque essai. La colonne « Gels d'E/S » indique les gels détectés pendant l'essai COMPLET (mesure + retour + stabilisation) : un gel survenu pendant la phase de retour (cas de `.158`) n'affecte pas les mesures de service de la fenêtre de 10 min, mais peut affecter la stabilisation.

| VM | Essai | Arrêt (UTC) | Score | Web (Windows) | `api_app` (Windows) | API K8s (Windows) | Écritures perdues (w1/w2) | Trou d'écriture survivant | etcd 4/4 en | Retour : Ready / etcd 5/5 / CNPG 2/2 | Gels d'E/S |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `.160` | essai unique | 09:33:40 | **29/29** ✅ | 100.0 % / 0.0 s | 100.0 % / 0.0 s | 100.0 % / 0.0 s | 0/0 | 0.3 s | 2 s | ssh=55 etcd5=65 lb=65 ; CNPG — | aucun |
| `.155` | essai 1 | 10:16:59 | **19/27** ❌ | 93.074 % / 44.7 s | 93.22 % / 44.7 s | 100.0 % / 0.0 s | 0/0 | 9.2 s | 2 s | ssh=55 ready=55 etcd5=261 ; CNPG 352 s | 11-04 10:28:29 -> 10:29:19 UTC |
| `.155` | essai 2 | 12:35:33 | **20/27** ❌ | 96.416 % / 11.0 s | 96.398 % / 32.0 s | 100.0 % / 0.0 s | 0/0 | 0.3 s | 2 s | ssh=62 ready=62 etcd5=62 ; CNPG 111 s | aucun |
| `.155` | essai 3 | 14:21:18 | **27/27** ✅ | 99.829 % / 1.0 s | 100.0 % / 0.0 s | 100.0 % / 0.0 s | 0/0 | 0.3 s | 1 s | ssh=53 ready=53 etcd5=39 ; CNPG 96 s | aucun |
| `.158` | essai (1re tentative abandonnée à T+14 s par un défaut d'outillage, voir GATE-4.md) | 15:13:13 | **26/27** ❌ | 99.657 % / 6.0 s | 99.83 % / 1.0 s | 100.0 % / 0.0 s | 0/0 | 54.0 s | 1 s | ssh=59 ready=49 etcd5=59 ; CNPG 276 s | 11-04 15:24:39 -> 15:25:24 UTC |


## `.160` — essai unique (`p4-vmstop160-stop160-20261104T093239Z`)

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

## `.155` — essai 1 (`p4-vmstop155-stop155-20261104T101559Z`)

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
| placement : pods Pending documentés (anti-affinité = conséquence attendue, pas une panne) | ✅ | {'T+90s': ['ingress-nginx        ingress-nginx-controller-84977f46dc-7x9cd   0/1   Pending       0               67s    <none>            <none>        <none>   <none>', 'ingress-nginx        ingress-nginx-controller-84977f46dc-q8bdn   1/1   Terminating   0               61m    172.16.126.62     k8s-worker2   <none>   <none>', 'pfe                  backend-75b8d9569b-42lkr                    0/1   |
| retour : nœud Ready <= 300 s et etcd 5/5 <= 300 s après la relance | ✅ | ssh=55 ready=55 etcd5=261 |
| retour : CNPG 2/2 sain <= 15 min après la relance | ✅ | 352 s |
| retour : aucun pod non sain (Pending résorbés) | ✅ | 151 s |
| stabilisation 1800 s : nœuds Ready, etcd 5/5, aucun pod non sain, sondes par nom OK | ❌ | ['T+38s etcd < 5'] |
| pas d'abandon | ✅ | None |

## `.155` — essai 2 (`p4-vmstop155-retest155-20261104T123434Z`)

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
| placement : pods Pending documentés (anti-affinité = conséquence attendue, pas une panne) | ✅ | {'T+90s': ['ingress-nginx        ingress-nginx-controller-84977f46dc-7x9cd   1/1   Terminating   0                139m    172.16.126.60     k8s-worker2   <none>   <none>', 'ingress-nginx        ingress-nginx-controller-84977f46dc-8cjxl   0/1   Pending       0                77s     <none>            <none>        <none>   <none>', 'pfe                  backend-75b8d9569b-42lkr                    1 |
| retour : nœud Ready <= 300 s et etcd 5/5 <= 300 s après la relance | ✅ | ssh=62 ready=62 etcd5=62 |
| retour : CNPG 2/2 sain <= 15 min après la relance | ✅ | 111 s |
| retour : aucun pod non sain (Pending résorbés) | ✅ | 194 s |
| stabilisation 1800 s : nœuds Ready, etcd 5/5, aucun pod non sain, sondes par nom OK | ❌ | ['T+319s etcd < 5', 'T+1325s etcd < 5'] |
| pas d'abandon | ✅ | None |

## `.155` — essai 3 (`p4-vmstop155-retest155c-20261104T142018Z`)

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
| placement : pods Pending documentés (anti-affinité = conséquence attendue, pas une panne) | ✅ | {'T+90s': ['ingress-nginx        ingress-nginx-controller-84977f46dc-8cjxl   1/1   Terminating   0                 107m    172.16.126.29     k8s-worker2   <none>   <none>', 'ingress-nginx        ingress-nginx-controller-84977f46dc-f7s4s   0/1   Pending       0                 71s     <none>            <none>        <none>   <none>', 'pfe                  backend-75b8d9569b-f7qpn                    |
| retour : nœud Ready <= 300 s et etcd 5/5 <= 300 s après la relance | ✅ | ssh=53 ready=53 etcd5=39 |
| retour : CNPG 2/2 sain <= 15 min après la relance | ✅ | 96 s |
| retour : aucun pod non sain (Pending résorbés) | ✅ | 96 s |
| stabilisation 1800 s : nœuds Ready, etcd 5/5, aucun pod non sain, sondes par nom OK (creux etcd isolés expliqués par une rafale CPU de .153 listés à part, E17) | ✅ | aucune anomalie inexpliquée |
| pas d'abandon | ✅ | None |

## `.158` — essai (1re tentative abandonnée à T+14 s par un défaut d'outillage, voir GATE-4.md) (`p4-vmstop158-stop158c-20261104T151214Z`)

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
| placement : pods Pending documentés (anti-affinité = conséquence attendue, pas une panne) | ✅ | {'T+90s': ['ingress-nginx        ingress-nginx-controller-84977f46dc-knxtt   0/1   Pending       0                 16s     <none>            <none>        <none>   <none>', 'ingress-nginx        ingress-nginx-controller-84977f46dc-zz5j8   1/1   Terminating   1 (6m20s ago)     7h35m   172.16.194.127    k8s-worker1   <none>   <none>', 'pfe                  backend-75b8d9569b-4tfvg                    |
| retour : nœud Ready <= 300 s et etcd 5/5 <= 300 s après la relance | ✅ | ssh=59 ready=49 etcd5=59 |
| retour : CNPG 2/2 sain <= 15 min après la relance | ✅ | 276 s |
| retour : aucun pod non sain (Pending résorbés) | ✅ | 93 s |
| stabilisation 1800 s : nœuds Ready, etcd 5/5, aucun pod non sain, sondes par nom OK (creux etcd isolés expliqués par une rafale CPU de .153 listés à part, E17) | ❌ | ["T+39s pods non sains: ['cnpg-system          cnpg-controller-manager-6cc9d8689f-8xnmn    0/1   Error     3 (2m53s ago)     16h']"] / expliquées (E17): ['T+6s etcd < 5 (KO: .153) — SSH de .153 expiré à 15:28:26 (rafale CPU)'] |
| pas d'abandon | ✅ | None |
