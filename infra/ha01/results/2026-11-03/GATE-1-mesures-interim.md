# GATE-1 — mesures (2026-11-03 23:26:04 UTC)

| Groupe | Critère | Résultat | Mesure |
|---|---|---|---|
| C10 | .160 : keepalived absent, aucun VIP | ✅ | 0 / inactive / 192.168.148.160/24 |
| C10 | .162 : keepalived absent, aucun VIP | ✅ | 0 / inactive / 192.168.148.162/24 |
| C1 | .160 : config HAProxy = fichier canonique du dépôt, valide, service actif | ✅ | f8b744ca4480751850d8e1c3623fc136680e2488fe1cca08c6b0b4e9bd90d9b4 / Configuration file is valid / active |
| C1 | .160 : backends applicatifs = .155/.158 seulement ; .153 en secours (bck=1) ; tous UP | ✅ | apps=['node-155', 'node-155', 'node-158', 'node-158'] bck(master)=1 tousUP=True |
| C1 | .162 : config HAProxy = fichier canonique du dépôt, valide, service actif | ✅ | f8b744ca4480751850d8e1c3623fc136680e2488fe1cca08c6b0b4e9bd90d9b4 / Configuration file is valid / active |
| C1 | .162 : backends applicatifs = .155/.158 seulement ; .153 en secours (bck=1) ; tous UP | ✅ | apps=['node-155', 'node-155', 'node-158', 'node-158'] bck(master)=1 tousUP=True |
| C5 | .160 : contrôleur DNS actif en mode shadow depuis 0.5 h (>= 24 h), 0 passage DOWN | ❌ | active / Tue 2026-11-03 22:53:47 UTC / shadow / 0 / 1 |
| C5 | .162 : contrôleur DNS actif en mode shadow depuis 0.7 h (>= 24 h), 0 passage DOWN | ❌ | active / Tue 2026-11-03 22:43:51 UTC / shadow / 0 / 1 |
| C2 | taint control-plane NoSchedule sur .153 | ✅ | [{"effect":"NoSchedule","key":"node-role.kubernetes.io/control-plane"}] |
| C2 | ingress-nginx : 2 réplicas Running, un par worker (.155/.158) | ✅ | ['k8s-worker1', 'k8s-worker2'] |
| C2 | PDB ingress-nginx minAvailable = 1 | ✅ | 1 |
| C2 | aucun pod Pending/en défaut dans le cluster | ✅ | aucun |
| C2 | pods sur .153 = composants tolérés uniquement (informatif) | ✅ | ['calico', 'cnpg-controller-manager', 'etcd', 'falco-wnhwl', 'ha01-canary', 'kube', 'kube-apiserver', 'kube-controller-manager', 'kube-scheduler', 'speaker'] |
| C7 | opérateur CNPG : --leader-lease-duration=60 et --leader-renew-deadline=40 | ✅ | ["controller","--leader-elect","--max-concurrent-reconciles=10","--config-map-name=cnpg-controller-manager-config","--secret-name=cnpg-controller-manager-config |
| C7 | opérateur CNPG : Running, 0 redémarrage depuis >= 60 min | ❌ | restarts=0, démarré il y a 17 min |
| C7 | plugin kubectl-cnpg présent sur .155 | ✅ | Build: {Version:1.28.4 Commit:f74344b4d Date:2026-06-29} |
| C7 | plugin kubectl-cnpg présent sur .158 | ✅ | Build: {Version:1.28.4 Commit:f74344b4d Date:2026-06-29} |
| C6 | CronJob A (inchangé) et B présents | ✅ | postgres-backup     false   2026-11-03T02:00:08Z / postgres-backup-b   false   2026-11-03T23:14:49Z |
| C6 | PVC postgres-backups (.158) et postgres-backups-b Bound | ✅ | postgres-backups     Bound   pvc-036608b9-0842-4301-80bb-8f91d0eee350   5Gi   RWO   local-path   <unset>   96d / postgres-backups-b   Bound   pvc-c3506dc8-b545-44fc-ac71-5463c947de65   5Gi   RWO   loc |
| C6 | sauvegarde hors cluster (.161) : dump valide, snapshot etcd > 10 Mo, timer activé | ✅ | PG_OK / 23285792 / enabled |
| C6 | tests de restauration PASS archivés (CronJob B + sauvegarde hors cluster) | ✅ | ['restore-test-cronjobB.txt', 'restore-test-offsite.txt'] |
| C3 | .153 : admin.conf -> api.helios.orange.tn:6443 et kubectl fonctionne | ✅ | server: https://api.helios.orange.tn:6443 / 3 |
| C3 | .155 : admin.conf -> 192.168.148.155:6443 et kubectl fonctionne | ✅ | server: https://192.168.148.155:6443 / 3 |
| C3 | .158 : admin.conf -> 192.168.148.158:6443 et kubectl fonctionne | ✅ | server: https://192.168.148.158:6443 / 3 |
| OBS | observation >= 5 min après la dernière modification (2026-11-03T23:25:38Z) | ❌ | 0 min |
| OBS | sondes vues de Windows depuis la dernière modification : disponibilité min >= 99,95 % et série d'échecs <= 3 s | ✅ | min=100.0% série max=0.0s n/sonde=15 |
| OBS | sondes vues de .161 depuis la dernière modification : disponibilité min >= 99,95 % et série d'échecs <= 3 s | ❌ | min=0% série max=999s n/sonde=0 |
| OBS | canari DB depuis la dernière modification : 0 échec, 0 écriture acquittée perdue | ✅ | acquittées=121 échecs=0 perdues=0 trou max=0.24s |
| SYS | RAM hôte >= 2000 Mo sur 10 min (re-mesure) | ✅ | min=2472 n=62 |
| SYS | etcd 5/5 sain | ✅ | 5/5 |
| SYS | CNPG sain 2/2 | ❌ | Cluster in healthy state/2 |

**Verdict automatique : NON VALIDÉ**
