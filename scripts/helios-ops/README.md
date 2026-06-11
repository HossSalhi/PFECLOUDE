# HELIOS — Ops / Infra runbooks

État réel des corrections infra (L8/L11/L19/L20), vérifié le 2026-06-11.

## L19 — Backup etcd ✅ DÉPLOYÉ
Installé et vérifié sur le **master (192.168.148.153)** :
- `/usr/local/bin/etcd-backup.sh` (= `etcd-backup.sh` de ce dossier)
- `/etc/cron.d/etcd-backup` → `0 */6 * * * root /usr/local/bin/etcd-backup.sh`
- `/opt/etcd-backups/` (mode 700) — 1er backup réel : `etcd-20260611-091501.db` (23 MB)
- log : `/var/log/etcd-backup.log`

Réinstaller : `scp etcd-backup.sh kube@192.168.148.153:/tmp/ && ssh ... 'sudo install -m755 /tmp/etcd-backup.sh /usr/local/bin/'`

## L8 — node_exporter ✅ (vérifié OK sur .161)
`.161` exporte déjà sur `:9100` (port en écoute). `.160` non joignable en SSH `kube`
(clé/user différent) — à vérifier avec le compte d'admin de la VM monitoring.

## L20 — Registry Docker local ⏸️ script fourni, NON exécuté (risque à chaud)
Reconfigurer containerd (`insecure registry`) sur les 3 nœuds + restart containerd
peut casser les pulls d'images en cours. À faire en **fenêtre de maintenance** :
```bash
# Sur .160 :
docker run -d --restart=always -p 5000:5000 --name registry registry:2
# Sur chaque nœud, /etc/containerd/config.toml :
#   [plugins."io.containerd.grpc.v1.cri".registry.mirrors."192.168.148.160:5000"]
#     endpoint = ["http://192.168.148.160:5000"]
# puis : sudo systemctl restart containerd   (⚠️ un nœud à la fois)
```

## L11 — Watchdog RESTART_VM ⏸️ NON déployé (sécurité)
Un endpoint HTTP `/restart/<node>` → `sudo reboot` est sensible (surface d'attaque).
À déployer derrière le réseau interne uniquement, avec auth. Le code client existe
déjà côté backend (`KubernetesRemediationExecutor.restartVm` + `remediation.watchdog-base-url`).
