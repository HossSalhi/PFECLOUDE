# HELIOS — Runbook : lancer les 14 tests soi-même (sans Claude)

Tout est scripté. 3 commandes suffisent une fois le cluster sain.

---

## 0) Prérequis : démarrer l'environnement
1. **Docker Desktop** (sur le PC) — lancer AVANT les VMs.
2. **Démarrer les 5 VMs** :
   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts/start-aiops.ps1
   ```
3. Attendre ~3-5 min que le cluster se rétablisse (surtout après un reboot du PC).

---

## 1) Vérifier la santé du cluster (les pièges connus)
```bash
kubectl get nodes                                   # les 3 doivent etre Ready
kubectl get cluster postgres -n pfe                 # READY doit etre 2
kubectl get deploy backend -n pfe                   # doit etre 2/2
curl -s http://192.168.148.161:8080/v2/topology     # partition health doit etre "healthy"
```

**Pièges fréquents après un reboot du PC :**
- **postgres / backend pas prêts (0/2)** : c'est la récupération WAL post-arrêt brutal → **attends 3-5 min**, ça revient seul (postgres 2/2 → backend 2/2).
- **Zeebe "unhealthy"** = disque de **.161 plein** (>87%). Fix :
  ```bash
  ssh kube@192.168.148.161 "echo kube | sudo -S journalctl --vacuum-size=150M"
  ```
  (revient "healthy" en ~15s, sans redémarrage)
- **Polling bloqué ~5 min après un restart backend** = `leader_lease` du pod mort. Fix :
  ```bash
  kubectl exec -n pfe postgres-1 -c postgres -- psql postgresql://appuser:postgres@localhost/pfe_db -c "DELETE FROM leader_lease WHERE lease_name='aiops-scheduler';"
  ```
  *(le script reset_demo_clean.py le fait déjà automatiquement)*

---

## 2) Lancer LES 14 TESTS (3 commandes, dans l'ordre)
```bash
# A. Nettoyage : supprime les incidents, remet les 16 apps SAINES, vide la lease
python scripts/reset_demo_clean.py

# B. Injection des 14 scénarios (16 types) PAR LOTS de 150s (anti-saturation Zeebe)
python scripts/inject_demo_faults.py 150

# C. Attente + réconcile + TABLEAU FINAL imprimé a la fin
python scripts/final_wait_reconcile.py
```
Durée totale ~25-30 min. Le tableau final s'affiche à la fin de l'étape C.

---

## 3) Voir les résultats à tout moment
```bash
# Tableau rapide (type | statut | titre)
python scripts/watch_incidents.py
```
Ou requête directe :
```bash
kubectl exec -n pfe postgres-1 -c postgres -- psql -t -A -F'|' postgresql://appuser:postgres@localhost/pfe_db -c "SELECT incident_type, status, title FROM incident ORDER BY incident_type;"
```
Ou l'**interface frontend** (page Incidents) — titre, type, action IA, traces Camunda, commande kubectl, statut.

**Résultat attendu :** 16 types distincts, tous **RESOLVED**, 0 GENERAL_DEGRADATION, 0 RESOLUTION_FAILED.

---

## 4) (Optionnel) Lancer UN SEUL test à la main
Réinitialise d'abord (`python scripts/reset_demo_clean.py`), puis injecte une seule panne :
```bash
# Crash loop
kubectl patch deploy helios-crashloop -n pfe --type=json -p '[{"op":"add","path":"/spec/template/spec/containers/0/command","value":["sh","-c","echo boot; exit 1"]}]'
# Image pull backoff
kubectl set image deploy/helios-imagepull app=127.0.0.1:1/nope:helios -n pfe
# OOM
kubectl patch deploy helios-oom -n pfe --type=json -p '[{"op":"add","path":"/spec/template/spec/containers/0/command","value":["sh","-c","tail /dev/zero"]}]'
# Network partition
kubectl apply -n pfe -f - <<'EOF'
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata: {name: helios-demo-network, namespace: pfe}
spec: {podSelector: {matchLabels: {app: helios-network}}, policyTypes: [Ingress, Egress]}
EOF
```
*(toutes les recettes d'injection sont dans `scripts/inject_demo_faults.py`, fonction `f_<nom>`)*

---

## 5) Mapping des 14 scénarios → type HELIOS
| Scénario | Type HELIOS | App | Action / commande |
|---|---|---|---|
| CPU stress | CPU_STRESS | helios-cpu | rollout restart |
| Memory OOM | MEMORY_STRESS | helios-oom | rollout undo |
| Disk pressure | NODE_DISK_PRESSURE | helios-disk | rollout restart |
| HTTP 5xx | HIGH_ERROR_RATE | helios-http5xx | rollout undo |
| HTTP latency | LATENCY_DEGRADATION | helios-latency | rollout undo |
| Network | NETWORK_PARTITION | helios-network | delete networkpolicy |
| Database down | DATABASE_CONNECTIVITY | helios-db | delete networkpolicy |
| DNS | DNS_FAILURE | helios-dns | delete networkpolicy |
| Pod crash | POD_CRASH_LOOP | helios-crashloop | rollout undo |
| Container failure | POD_INIT_CONTAINER_FAILURE / POD_CONFIG_ERROR | helios-init / helios-config | rollout undo |
| Pod eviction / image | POD_IMAGE_PULL_BACKOFF | helios-imagepull | rollout undo |
| Pending (ressources) | POD_PENDING_INSUFFICIENT_RESOURCES | helios-pending | rollout undo |
| Service-to-service | SERVICE_UNAVAILABLE | helios-service | delete networkpolicy |
| Déploiement indispo | DEPLOYMENT_UNAVAILABLE | helios-unavailable | rollout undo |
| HPA / scaling | AUTOSCALING | helios-hpa | kubectl scale (scale up) |
| Node failure | NODE_NOT_READY | (worker) | RESTART_VM via Camunda → agent hôte |

> Node failure se déclenche au niveau hôte (éteindre un worker) — séparé des 16 tests applicatifs ci-dessus.
