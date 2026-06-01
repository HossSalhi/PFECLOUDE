# HELIOS AIOps — Plan de test Camunda + IA + Monitoring

Document de référence pour valider le pipeline complet après le rebuild + redéploiement
des nouvelles images (pfe-backend:0.2.12 et pfe-frontend:1.0.6).

---

## ⚙️ Pré-requis — appliquer les fixes de cette session

Tous ces fixes sont déjà sur disque. Ils prendront effet au prochain rebuild + apply :

| Fix | Fichier | Effet |
|---|---|---|
| Labels monitoring sur backend | `backend-deployment.yaml` | Pods discoverable par AI + Prometheus |
| Labels monitoring sur ai-service | `ai-service-deployment.yaml` | Idem |
| Labels monitoring sur frontend | `frontend-deployment.yaml` | Frontend surveillé aussi |
| HPA minReplicas=1 maxReplicas=2 | `backend-hpa.yaml` | Pas de chaos de scaling |
| Backend memory 384Mi | `backend-deployment.yaml` | Rentre dans les workers RAM-contraints |
| Prometheus scrape cAdvisor+kubelet | `incluster-prometheus.yaml` | **Source des features AI** (cpu_usage_cores, memory_working_set_bytes, etc.) |
| Prometheus resources bumped | `incluster-prometheus.yaml` | 500Mi req / 1Gi limit (encaisse le scrape cAdvisor) |
| TwoFaAutoSetup config-gated | `TwoFaAutoSetup.java` + `application.properties` | Plus de lockout SuperAdmin à chaque restart |
| Frontend Dockerfile multi-stage | `frontend/Dockerfile` | Plus de piège du dist/ pré-buildé |
| OrchestrationService → Camunda | `OrchestrationService.java` | Alert webhook déclenche maintenant un vrai Camunda workflow |
| Bouton "Démarrer remédiation" UI | `incident-detail.component.html/ts` | UX claire + déclenche Camunda pour tous types d'incidents (sauf MANUAL) |

---

## 🔄 Sequence complete pour repartir frais

```powershell
# 1. (PC frais après reboot Windows si possible)

# 2. Stop éventuels VMs zombies
.\scripts\stop-aiops.ps1

# 3. Build des nouvelles images (Docker doit etre éteint au démarrage; le script gère tout)
.\scripts\build-images.ps1 `
    -BackendTag pfe-backend:0.2.12 `
    -FrontendTag pfe-frontend:1.0.6

# 4. Boot le cluster
.\scripts\start-aiops.ps1

# 5. Deploy les nouvelles images + applique les manifests YAML mis à jour
.\scripts\deploy-images.ps1 `
    -BackendTag pfe-backend:0.2.12 `
    -FrontendTag pfe-frontend:1.0.6

# 6. Manuellement appliquer le manifest demo-app + Prometheus mis à jour
ssh kube@192.168.148.153 "kubectl apply -f /tmp/demo-app.yaml"
# (le script deploy-images push déjà les YAMLs depuis backend/infra/k8s/)
```

---

## 🧪 Tes 3 questions sur Camunda — Réponses définitives avec scripts de test

### Question 1 — Quand l'IA dit `AUTO_EXECUTE`, Camunda tourne-t-il automatiquement ?

**OUI** — flux entièrement automatique sans intervention humaine.

**Trace dans le code :**

```java
// AIOpsOrchestrationService.processAiPrediction() — appelé par le scheduler
if (!plan.approvalRequired() && plan.autoExecutable()) {
    camundaWorkflowService.startWorkflow(workflow, action, plan, prediction);
    // Aucun humanTaskService.createTask() — c'est pleinement automatique
    incident.setStatus(IncidentStatus.IN_PROGRESS);
}
```

**Côté Camunda BPMN** (`backend/src/main/resources/camunda/incident-remediation.bpmn`) :

```
[Incident created] → [Approval required?] ─── NO ───→ [Execute remediation]
                                                            ↓
                                              executeWorker.handle()
                                                            ↓
                                       KubernetesRemediationExecutor.execute(plan)
                                                            ↓
                                           coreV1Api.deleteNamespacedPod()
                                           appsV1Api.replaceNamespacedDeploymentScale()
                                                            ↓
                                              [Verify recovery] → [Resolved]
```

**Comment le déclencher en test :**

```bash
# Sur la VM 161, force une prediction AUTO_EXECUTE :
curl -X POST http://192.168.148.161:8000/predict \
  -H "Content-Type: application/json" \
  -d '{"namespace":"pfe","app":"demo-app","force_execution_mode":"AUTO_EXECUTE"}'

# Vérifie dans les logs backend :
kubectl logs -n pfe -l app=backend --tail=100 | grep -i "Camunda orchestration started"
# Attendre :
# "Camunda orchestration started with process instance NNNNNN"

# Vérifie que kubectl a vraiment été appelé :
kubectl get pods -n pfe -l app=demo-app -w   # Tu vois le pod redémarrer
```

---

### Question 2 — Quand mode `RECOMMEND_ONLY`, cliquer "Démarrer" déclenche-t-il Camunda ?

**MAINTENANT OUI** (après les fixes de cette session).

**Avant les fixes :** Le bouton "Démarrer" (= IN_PROGRESS) appelait `executeIncident()` UNIQUEMENT si `incident.source === 'AI'`. Pour les autres sources (MANUAL, OTHER, PROMETHEUS) → juste un UPDATE SQL du statut, **PAS de Camunda**.

**Après les fixes :** Logique étendue. Voir `incident-detail.component.ts:58-91` :

```typescript
const shouldTriggerRemediation =
  newStatus === 'IN_PROGRESS' &&
  this.incident.source !== 'MANUAL' &&   // MANUAL = note d'admin, pas d'action infra
  this.incident.status === 'OPEN';

if (shouldTriggerRemediation) {
  this.api.executeIncident(this.incident.id).subscribe(...);  // ← VRAI Camunda
  return;
}
// Sinon : simple UPDATE statut (cas MANUAL ou Resolve/Rouvrir)
```

L'endpoint `POST /api/incidents/{id}/execute` appelle `AIOpsOrchestrationService.executeRecommendation()` qui :
1. Crée un Workflow + Action (mode MANUAL)
2. **`camundaWorkflowService.startWorkflow(workflow, action, plan, prediction)`** — vrai Camunda
3. `humanTaskService.createTask("Validate AI remediation for incident #N", workflow)`
4. Status → `WAITING_FOR_APPROVAL`

L'admin doit ensuite cliquer "Approuver" dans `/tasks` → Camunda débloque → executeWorker exécute kubectl.

**Comment le tester :**

```bash
# 1. Crée un incident RECOMMEND_ONLY (par exemple via POST manuel)
curl -X POST http://192.168.148.153:30880/api/incidents \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"title":"Test manual remediation","description":"...","severity":"HIGH","source":"PROMETHEUS"}'
# Réponse : { "id": 123, "status": "NEW", "source": "PROMETHEUS" }

# 2. Sur l'UI : ouvrir l'incident #123. Le bouton "Démarrer remédiation (Camunda)"
#    apparaît avec une icône de fusée 🚀 et un texte explicatif.

# 3. Click → backend appelle /api/incidents/123/execute
#    → Camunda workflow démarre
#    → Human Task "Validate AI remediation for incident #123" apparaît dans /tasks
#    → Status → WAITING_FOR_APPROVAL

# 4. Aller dans /tasks → click "Approuver"
#    → publishDecision envoie un message Camunda
#    → BPMN avance vers "Execute remediation"
#    → executeWorker.handle() → KubernetesRemediationExecutor.execute()
#    → vrai kubectl exec sur le pod ciblé

# 5. Vérification :
kubectl logs -n pfe -l app=backend | grep "Automated remediation executed"
```

---

### Question 3 — Comment vérifier le path complet alert webhook → Camunda ?

**Avant les fixes :** Alert webhook créait un incident + workflow DB **sans Camunda**. Quand on approuvait la Human Task, c'était juste un record DB sans exécution kubectl.

**Après les fixes :** `OrchestrationService.handleIncomingAlert()` délègue maintenant à `AIOpsOrchestrationService.executeRecommendation()` pour les sévérités HIGH/CRITICAL, ce qui déclenche un VRAI workflow Camunda.

**Test :**

```bash
# 1. POST alert (sans approbation requise au début — le délégation à executeRecommendation
#    cree la Human Task car le mode est MANUAL avec approvalRequired=true)
curl -X POST http://192.168.148.153:30880/api/alerts/webhook \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message":"HIGH CPU on backend pod","source":"EXTERNAL","severity":"CRITICAL"}'

# 2. Vérifier dans les logs backend :
kubectl logs -n pfe -l app=backend --tail=50 | grep "Alert webhook"
# Attendu :
# "Alert webhook -> Camunda workflow started for incident #N"

# 3. Vérifier la Human Task créée :
curl http://192.168.148.153:30880/api/tasks/pending -H "Authorization: Bearer $TOKEN"
# Attendu : tâche "Validate AI remediation for incident #N"

# 4. Vérifier le process Camunda existe :
curl http://192.168.148.161:8080/v2/process-instances
# Attendu : process instance avec correlationKey = incident workflow ID
```

---

## 🧪 Test complet IA + Monitoring sur tous les apps (frontend inclus)

Après les fixes de cette session, **5 apps surveillés** :
- backend (Spring Boot, /actuator/prometheus)
- ai-service (Python FastAPI, /metrics)
- chromadb (vector DB)
- frontend (nginx, /stub_status) **← nouveau**
- demo-app (nginx test, /stub_status)

### Étape 1 — Vérifier la découverte par Prometheus

```bash
# Pods discoverable par AI :
kubectl get pods -A -l "aiops.io/monitor=true" -o wide
# Attendu : backend + ai-service + frontend + demo-app (4 pods minimum)

# Targets actifs dans Prometheus in-cluster :
curl http://192.168.148.153:30090/api/v1/targets?state=active | \
  python3 -c "import json,sys; [print(t['scrapeUrl'], '->', t['health']) for t in json.load(sys.stdin)['data']['activeTargets']]"
```

### Étape 2 — Vérifier que cAdvisor remonte les métriques container

```bash
# Query CPU usage par container dans namespace pfe (devait être 0 avant les fixes) :
curl 'http://192.168.148.153:30090/api/v1/query?query=container_cpu_usage_seconds_total{namespace="pfe"}' | \
  python3 -m json.tool | head -30

# Attendu : plusieurs séries (une par container) avec des valeurs
```

### Étape 3 — Vérifier que l'AI voit enfin des features non-nulles

```bash
# /monitor/status : last_apps_analyzed devrait être > 0 maintenant
curl http://192.168.148.161:8000/monitor/status
# Attendu : "apps_analyzed": 5 (ou plus), "anomalies_found": variable

# Test direct de prediction :
curl -X POST http://192.168.148.161:8000/predict \
  -H "Content-Type: application/json" \
  -d '{"namespace":"pfe","app":"backend","window_minutes":5}' | python3 -m json.tool
# Attendu : "features": {...valeurs non-nulles...}, "confidence": > 0.1
```

### Étape 4 — Stress test réel sur demo-app + observer la détection

```bash
# 1. Injecter du vrai stress CPU sur demo-app :
kubectl exec -n pfe deploy/demo-app -- sh -c "(yes > /dev/null &); (yes > /dev/null &); (yes > /dev/null &); (yes > /dev/null &)"

# 2. Attendre 2 minutes (pour que Prometheus scrape + AI poll cycle)
sleep 120

# 3. Vérifier la métrique CPU monte dans Prometheus :
curl 'http://192.168.148.153:30090/api/v1/query?query=rate(container_cpu_usage_seconds_total{pod=~"demo-app.*"}[1m])'

# 4. Vérifier que l'AI a maintenant une vraie prediction :
kubectl logs -n pfe -l app=backend | grep "AI prediction.*demo-app" | tail -5
# Attendu : "AI prediction: incident=true confidence=0.7+ action=RESTART_POD"

# 5. Vérifier qu'un incident a été créé :
curl http://192.168.148.153:30880/api/incidents?page=0\&size=3 -H "Authorization: Bearer $TOKEN" | \
  python3 -m json.tool

# 6. Cleanup :
kubectl exec -n pfe deploy/demo-app -- killall yes
```

---

## 🔧 Si problèmes après le redeploy

### "AI retourne toujours confidence=0.1"
→ Vérifier que cAdvisor scrape fonctionne :
```bash
kubectl logs -n monitoring -l app=prometheus-incluster | grep -i error | tail -10
curl 'http://192.168.148.153:30090/api/v1/query?query=up{job="kubernetes-cadvisor"}'
```
Si pas de données : le ClusterRole `prometheus-incluster` doit avoir `nodes/metrics` et `nodes/proxy` (déjà dans le YAML).

### "SuperAdmin requiert 2FA à chaque connexion"
→ Vérifier que la nouvelle image inclut le fix `@ConditionalOnProperty` :
```bash
kubectl exec -n pfe deploy/backend -- env | grep TWO_FA
# Attendu : TWO_FA_AUTO_SETUP_ENABLED=false (ou absent — default false)
```
Si nécessaire, retoucher la DB pour disable 2FA temporairement :
```bash
kubectl exec -n pfe postgres-4 -- psql -U postgres -d pfe_db -c \
  "UPDATE app_user SET two_factor_enabled=false WHERE username='superadmin'"
```

### "Bouton Démarrer remédiation ne déclenche pas Camunda"
→ Hard refresh le navigateur (Ctrl+Shift+R) pour charger le nouveau JS.
→ Vérifier dans la console réseau que la requête va vers `POST /api/incidents/X/execute`, pas vers `PUT /api/incidents/X/status/IN_PROGRESS`.

### "Camunda ne démarre pas le BPMN"
→ Vérifier la connectivité backend → VM 161 :
```bash
kubectl exec -n pfe deploy/backend -- wget -qO- http://192.168.148.161:8080/v2/topology
```
→ Vérifier que la BPMN est bien déployée :
```bash
curl http://192.168.148.161:8080/v2/process-definitions
# Attendu : process "incident-remediation" listé
```

---

## 📊 Validation finale

Après le test cycle, tu devrais avoir prouvé :

- [ ] Les 5 apps sont scrapés par Prometheus in-cluster (cAdvisor + annotations)
- [ ] L'AI service voit des features non-nulles dans ses predictions
- [ ] Un stress réel sur demo-app produit un incident détecté par l'AI
- [ ] L'incident déclenche un workflow Camunda
- [ ] Le bouton "Démarrer remédiation (Camunda)" sur n'importe quel incident → vrai Camunda
- [ ] L'approbation d'une Human Task → kubectl reel sur K8s
- [ ] L'alert webhook (Alertmanager) crée un incident HIGH/CRITICAL et démarre Camunda automatiquement

Ce qui est **pleinement automatique** :
- AI poll → predict → AUTO_EXECUTE (confidence > 0.70) → Camunda → kubectl → resolved (0 intervention)
- Alert webhook HIGH/CRITICAL → incident → Camunda → Human Task created

Ce qui nécessite **intervention humaine** :
- AI predict APPROVAL_REQUIRED (confidence 0.45-0.70) → Camunda en attente → admin approve dans /tasks
- AI predict RECOMMEND_ONLY (confidence < 0.45) → incident OPEN → admin click "Démarrer remédiation"
- Alert webhook LOW/MEDIUM → incident OPEN → admin décide manuellement

---

Bonne chance ! Tout est sur disque, c'est durable, ça survit au reboot.
