# HELIOS — Ops / Infra runbooks

État réel des corrections infra (L8/L11/L19/L20), vérifié le 2026-06-11.

## L19 — Backup etcd ✅ DÉPLOYÉ
Installé et vérifié sur le **master (192.168.148.153)** :
- `/usr/local/bin/etcd-backup.sh` (= `etcd-backup.sh` de ce dossier)
- `/etc/cron.d/etcd-backup` → `0 */6 * * * root /usr/local/bin/etcd-backup.sh`
- `/opt/etcd-backups/` (mode 700) — 1er backup réel : `etcd-20260611-091501.db` (23 MB)
- log : `/var/log/etcd-backup.log`

Réinstaller : `scp etcd-backup.sh kube@192.168.148.153:/tmp/ && ssh ... 'sudo install -m755 /tmp/etcd-backup.sh /usr/local/bin/'`

## L8 — node_exporter ✅ DÉPLOYÉ (5/5 UP, vérifié 2026-06-11)
Avant : `job=vm-node-exporters` → .160/.161 UP mais **.153/.155/.158 (nœuds K8s) DOWN**.
Action : `install-node-exporter.sh` (binaire + `node_exporter.service` DynamicUser) sur les
3 nœuds. Après : **5/5 UP** dans Prometheus → métriques OS des nœuds visibles dans Grafana.
- `.160` atteignable via **rebond** `ssh kube@.153 'ssh kube@.160 …'` (clé du master autorisée
  sur .160 ; la clé locale ne l'est pas). `.160`/`.161` exportaient déjà sur :9100.

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

## L11 — Watchdog RESTART_VM ⏸️ ARTEFACT fourni, NON déployé
`vm-watchdog.py` (ce dossier) : watchdog central token-protégé (master .153 → SSH
+ `sudo systemctl reboot` sur les workers ; master exclu). Le code client backend
existe (`KubernetesRemediationExecutor.restartVm` + `remediation.watchdog-base-url`).
**Non déployé** : endpoint reboot = sensible, et le valider = rebooter un nœud
(disruptif). De plus la HA K8s récupère déjà un node-down sans reboot VM (testé).
Pour activer : lancer le watchdog (systemd) avec `WATCHDOG_TOKEN`, poser
`REMEDIATION_WATCHDOG_BASE_URL` + ouvrir le port 9999 dans `backend-egress`.

## L7 — Prometheus in-cluster (fédération) ❌ ÉCARTÉ
`incluster-prometheus.yaml` existe MAIS l'archi a **délibérément retiré** le Prometheus
in-cluster au profit du scrape DIRECT par .160 (cf. mémoire `project_helios_archi_separation`).
Le redéployer contredirait ce choix + ajouterait ~0.5-1 Gi RAM sur un hôte saturé (L18).
Le SPOF .160 est couvert autrement par `prometheus-rules.yaml` (alertes externes).

## L1 / L5 — Données réelles ❌ hors-scope
Entraîner l'IA sur 3-6 mois d'incidents prod réels labellisés. Le temps le fera.

## L12 — Auto-exécution actions destructives 🔒 choix de design
rollback/drain/cordon restent sous approbation humaine = **sécurité**, pas un bug.
