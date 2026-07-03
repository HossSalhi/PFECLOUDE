# HELIOS — Tests déclenchés DEPUIS le cluster (par SSH), pas depuis PowerShell/C:

> **Principe** : HELIOS observe la couche **Kubernetes**. Les pannes applicatives sont des **états K8s** → on les injecte via **kubectl exécuté en SSH sur le master** (dans le cluster). Les pannes **nœud** sont des faits **hôte** → on les injecte en **SSH dans worker1/worker2**.

## 0. Connexions SSH
```bash
# Le MASTER a kubectl (les workers n'ont pas de kubeconfig utilisateur)
ssh kube@192.168.148.153      # master  -> lancer les kubectl ici
ssh kube@192.168.148.158      # worker1 -> pannes hôte (kubelet, disque)
ssh kube@192.168.148.155      # worker2 -> pannes hôte
# mot de passe sudo = kube
```

Où tournent les apps ? (le master n'héberge PAS d'app — affinité node-type=worker)
```bash
kubectl -n pfe get pods -o wide          # voir sur quel worker est chaque pod
```
Pour forcer un test « sur worker1 » : cordon l'autre worker le temps du rollout
```bash
kubectl cordon k8s-worker2 && kubectl -n pfe rollout restart deploy/helios-cpu
# ... test ... puis:
kubectl uncordon k8s-worker2
```

---

## 1. Test des 2 GARDES CENTRALES (IA + Camunda)

### 1a. Garde Camunda (Camunda down ⇒ 0 détection + 0 remédiation)
```bash
# ARRÊT (sur .161)
ssh kube@192.168.148.161 "echo kube | sudo -S systemctl stop camunda8.service"
# -> injecter n'importe quelle panne : AUCUN incident créé (logs backend: 'Camunda injoignable — SUSPENDUE')
# REDÉMARRAGE
ssh kube@192.168.148.161 "echo kube | sudo -S systemctl --no-block start camunda8.service"   # Zeebe boot ~60-90s
```

### 1b. L'IA RECOMMANDE, elle ne DÉTECTE pas (IA down ⇒ Camunda détecte quand même, repli DÉTERMINISTE)
> **Exigence encadrant** : la détection doit être faisable par **Camunda + monitoring**, PAS par l'IA. L'IA
> est le **cerveau qui recommande** ; si elle tombe, Camunda **continue de détecter** et HELIOS remédie en
> **repli déterministe** (marqueurs d'état K8s certain). Seule **Camunda** est une garde bloquante.
```bash
# ARRÊT (sur .161)
ssh kube@192.168.148.161 "echo kube | sudo -S systemctl stop pfe-ai-service.service"
# -> panne à ÉTAT K8s certain (crashloop, oom, imagepull, config, init, pending, saturation, network,
#    dns, db, disk, 5xx, latence, deploy-unavailable) : DÉTECTÉE + REMÉDIÉE en repli déterministe
#    (logs: 'Service IA injoignable ... repli DÉTERMINISTE (marqueurs K8s)').
# -> worker coupé : NŒUD DÉTECTÉ (VM_DOWN) + AUTO-HEAL (power-on via Camunda->watchdog) — inchangé.
# -> SEULS les cas qui EXIGENT le cerveau (CPU_STRESS / AUTOSCALING = pas de marqueur d'état) attendent
#    le retour de l'IA (on n'invente pas de classification).
# REDÉMARRAGE  (health revient en ~10-20s -> l'IA reclasse/recommande de nouveau finement)
ssh kube@192.168.148.161 "echo kube | sudo -S systemctl start pfe-ai-service.service"
# vérifier: curl -s -o /dev/null -w '%{http_code}' http://192.168.148.161:8000/health   (200 = up)
```

---

## 2. Les 16 tests — en bash, à lancer SUR LE MASTER (ssh kube@.153)

### Groupe A — pannes applicatives (kubectl ; réparation = rollback auto)
```bash
# 1) POD_CRASH_LOOP
kubectl -n pfe patch deploy helios-crashloop --type=json -p '[{"op":"add","path":"/spec/template/spec/containers/0/command","value":["sh","-c","echo boot; exit 1"]}]'

# 2) MEMORY_STRESS (OOM)
kubectl -n pfe patch deploy helios-oom --type=json -p '[{"op":"add","path":"/spec/template/spec/containers/0/command","value":["sh","-c","tail /dev/zero"]}]'

# 3) HIGH_ERROR_RATE (5xx)
kubectl -n pfe patch deploy helios-http5xx --type=json -p '[{"op":"add","path":"/spec/template/spec/containers/0/command","value":["./podinfo","--random-error=true"]}]'

# 4) LATENCY_DEGRADATION
kubectl -n pfe patch deploy helios-latency --type=json -p '[{"op":"add","path":"/spec/template/spec/containers/0/command","value":["./podinfo","--random-delay=true"]}]'

# 5) POD_PENDING_INSUFFICIENT_RESOURCES
kubectl -n pfe patch deploy helios-pending --type=json -p '[{"op":"replace","path":"/spec/template/spec/containers/0/resources","value":{"requests":{"cpu":"2","memory":"32Mi"},"limits":{"cpu":"2","memory":"96Mi"}}}]'

# 6) POD_INIT_CONTAINER_FAILURE
kubectl -n pfe patch deploy helios-init --type=json -p '[{"op":"replace","path":"/spec/template/spec/initContainers/0/command","value":["sh","-c","echo init; exit 1"]}]'

# 7) POD_CONFIG_ERROR
kubectl -n pfe patch deploy helios-config --type=json -p '[{"op":"replace","path":"/spec/template/spec/containers/0/envFrom","value":[{"configMapRef":{"name":"helios-missing-cm"}}]}]'

# 8) DEPLOYMENT_UNAVAILABLE
kubectl -n pfe patch deploy helios-unavailable --type=json -p '[{"op":"replace","path":"/spec/template/spec/containers/0/readinessProbe/httpGet/path","value":"/nonexistent-404-helios"}]'

# 9) POD_IMAGE_PULL_BACKOFF
kubectl -n pfe set image deploy/helios-imagepull app=127.0.0.1:1/nope:helios
```

### Groupe B — charge injectée DANS le pod (donc sur son worker) via kubectl exec
```bash
# 10) CPU_STRESS  (le hog tourne dans le pod helios-cpu = sur son worker ; réparation = rolling restart)
POD=$(kubectl -n pfe get pod -l app=helios-cpu -o jsonpath='{.items[0].metadata.name}')
kubectl -n pfe exec $POD -- sh -c 'nohup sh -c "yes >/dev/null 2>&1 & p=$!; sleep 300; kill $p" >/dev/null 2>&1 & echo hog'

# 11) NODE_DISK_PRESSURE  (remplit l'emptyDir du pod helios-disk ; réparation = delete pod)
POD=$(kubectl -n pfe get pod -l app=helios-disk -o jsonpath='{.items[0].metadata.name}')
kubectl -n pfe exec $POD -- sh -c 'dd if=/dev/zero of=/data/fill bs=1M count=120 2>/dev/null; echo filled'

# 12) AUTOSCALING  (opt-in helios.io/autoscale ; réparation = scale +1)
POD=$(kubectl -n pfe get pod -l app=helios-hpa -o jsonpath='{.items[0].metadata.name}')
kubectl -n pfe exec $POD -- sh -c 'nohup sh -c "yes >/dev/null 2>&1 & p=$!; sleep 300; kill $p" >/dev/null 2>&1 & echo hog'
```

### Groupe C — NetworkPolicies (réparation = suppression de la policy)
```bash
# 13) SERVICE_UNAVAILABLE
kubectl -n pfe apply -f - <<'EOF'
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata: {name: helios-demo-service, namespace: pfe}
spec: {podSelector: {matchLabels: {app: helios-service}}, policyTypes: [Ingress]}
EOF

# 14) NETWORK_PARTITION
kubectl -n pfe apply -f - <<'EOF'
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata: {name: helios-demo-network, namespace: pfe}
spec: {podSelector: {matchLabels: {app: helios-network}}, policyTypes: [Ingress, Egress]}
EOF

# 15) DNS_FAILURE  (egress autorisé sauf le port 53)
kubectl -n pfe apply -f - <<'EOF'
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata: {name: helios-demo-dns, namespace: pfe}
spec:
  podSelector: {matchLabels: {app: helios-dns}}
  policyTypes: [Egress]
  egress: [{to: [], ports: [{protocol: TCP, port: 80},{protocol: TCP, port: 443},{protocol: TCP, port: 9898}]}]
EOF

# 16) DATABASE_CONNECTIVITY  (egress DNS ok mais pas 5432)
kubectl -n pfe apply -f - <<'EOF'
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata: {name: helios-demo-db, namespace: pfe}
spec:
  podSelector: {matchLabels: {app: helios-db}}
  policyTypes: [Egress]
  egress: [{to: [], ports: [{protocol: UDP, port: 53},{protocol: TCP, port: 53},{protocol: TCP, port: 80},{protocol: TCP, port: 443}]}]
EOF
```

---

## 3. Les VRAIES pannes NŒUD — en SSH DANS le worker (worker1/worker2)

```bash
# NODE_NOT_READY — arrêt du kubelet DANS worker1 (plus propre que couper la VM)
ssh kube@192.168.148.158 "echo kube | sudo -S systemctl stop kubelet"
#  -> worker1 NotReady -> HELIOS: incident NODE_NOT_READY -> Camunda -> power-on/récup
#  RÉCUP:
ssh kube@192.168.148.158 "echo kube | sudo -S systemctl start kubelet"
```
> ⚠️ Le master (.153) : `stop kubelet` le rend NotReady mais l'API survit (HA etcd 2/3). Récup = `start kubelet`.
> ⚠️ NE PAS remplir le disque RÉEL d'un nœud (`dd` sur `/`) : risque de casser le cluster. Utiliser la version pod (#11).
> ⚠️ Stress CPU de l'HÔTE worker (stress-ng en SSH) : HELIOS ne le verra PAS (il mesure les pods). Utiliser #10 (in-pod).

---

## 4. Observer ce que fait HELIOS

- **UI HELIOS** : http://192.168.148.158:30880 (ou .155:30880) — login superadmin / Helios2026! → tableau des incidents (détection → classification IA → action → RESOLVED).
- **En base (sur le master)** :
```bash
PRIM=$(kubectl -n pfe get pod -l cnpg.io/instanceRole=primary -o jsonpath='{.items[0].metadata.name}')
kubectl -n pfe exec $PRIM -c postgres -- psql -U postgres -d pfe_db -c \
 "SELECT id,app,incident_type,severity,status FROM incident ORDER BY id DESC LIMIT 20;"
```
- **Logs backend** (détection/IA/Camunda) :
```bash
kubectl -n pfe logs -l app=backend --tail=50 | grep -iE 'analyze|prediction|incident|remediation'
```

---

## Résumé pour l'encadrant
- **App faults = kubectl sur le master (via SSH) = dans le cluster** (pas depuis Windows/C:). C'est ainsi qu'une vraie panne K8s se produit — il n'y a pas de commande hôte pour un ImagePullBackOff.
- **Node faults = vraies commandes hôte en SSH dans worker1/worker2** (kubelet).
- Le **master n'héberge pas d'app** (par design) → seules les pannes nœud le concernent.
- **Détection = Camunda + monitoring** (alarme brute sur l'état K8s / métriques, SANS IA) → **Camunda orchestre** (process `incident-detection`) → **l'IA RECOMMANDE** (classe/explique/propose l'action) → **Camunda EXÉCUTE** dans le cluster → **vérifie → RESOLVED**.
- **L'IA ne détecte pas et n'exécute pas** : c'est le **cerveau qui recommande**. IA down ⇒ Camunda détecte quand même et remédie en **repli déterministe** ; seuls les cas ambigus (CPU/HPA) attendent son retour. **Camunda down ⇒ 0 détection** (garde bloquante unique).
