# 🎯 ENTRY POINT — Reprendre le plan après reset Claude

> **Document de reprise unique.** Quand la prochaine session Claude démarre, l'utilisateur dira "continue le plan" et Claude lit CE fichier en premier.

---

## ✅ DÉJÀ FAIT (ne pas refaire)

### Phase 0 — Infrastructure
- ✅ 5 VMs configurées avec RAM stable :
  - master: 1792 MB
  - worker1: 2560 MB (réduit de 3072 → évite cascade overcommit)
  - worker2: 2560 MB (réduit de 3072)
  - monitoring: 1024 MB (réduit de 1488)
  - aiCamunda: 9216 MB (F16 LLM)
- ✅ SSH VM 161 OK (clé ed25519 publique de Claude installée dans `kube@161:~/.ssh/authorized_keys`)
- ✅ Ollama actif : `aiops-sre-q4` (6.80 GB F16) — backup `aiops-sre-q4-backup` (Q4 2.1 GB)

### Phase 1 — Dataset training
- ✅ `/opt/pfe-ai-service/scripts/generate_v2_51_classes.py` (poussé sur VM 161)
- ✅ `/opt/pfe-ai-service/datasets/aiops_training_dataset_v2.csv` — **4790 rows, 51 scénarios + NO_INCIDENT**
- ✅ Copie locale Windows : `c:/Users/houss/IdeaProjects/PFE-Orange/generate_v2_51_classes.py`

### Phase 2 — Training XGBoost (4 classifiers)
- ✅ `/opt/pfe-ai-service/scripts/train_baseline_v2.py` (poussé)
- ✅ Modèle déployé : `/opt/pfe-ai-service/model_artifacts/model.joblib` (v3)
- ✅ Backup ancien modèle : `model_v2_3classifiers.joblib.bak`
- ✅ Métriques :
  - incident: F1=0.99
  - severity: F1=0.90
  - action: F1=0.88
  - **scenario (52 classes): F1=0.85**

### Phase 2b — Patch inference.py (override scenario_ml)
- ✅ `/opt/pfe-ai-service/app/models/loader.py` modifié (charge scenario_model + scenario_labels)
- ✅ `/opt/pfe-ai-service/app/models/inference.py` patché : méthode `_predict_scenario_ml()` + override block qui promeut `incident=True` si confidence ≥ 0.50
- ✅ Backups conservés : `loader.py.bak` et `inference.py.bak`
- ✅ Service `pfe-ai-service.service` actif et fonctionnel

### Phase 4 — Backend mapping
- ✅ Vérifié : `RemediationPolicyService.mapActionType()` est déjà générique (gère 7 actions via switch + keyword fallback)
- ✅ Pas de rebuild backend nécessaire pour les 51 classes

---

## ⏳ À FAIRE (ordre exact)

### Phase 3 — Train Qwen LoRA (1h30 background) — **PRIORITÉ DU USER**

⚠️ **Contraintes connues** :
- Disque VM 161 : 12 GB libre (tight)
- Pas de `transformers`/`peft`/`torch` installés dans venv
- LoRA adapters EXISTANTS dans `/opt/pfe-ai-service/model_artifacts/aiops-sre-lora` (rang inconnu) et `aiops-sre-lora-full` (~120 MB safetensors)

**Approche recommandée** :
1. Installer dépendances dans venv VM 161 :
   ```bash
   ssh kube@192.168.148.161 '/opt/pfe-ai-service/.venv/bin/pip install --no-cache-dir peft transformers datasets accelerate'
   ```
   (vérifier disk après — si <5 GB libre, abort)
2. Générer dataset LoRA : 1000 prompts SRE (50 types × 20 variations) basé sur la taxonomie `docs/INCIDENT_TAXONOMY.md`
   - Format JSONL Alpaca : `{"instruction":"...", "input":"", "output":"..."}`
   - Sauver dans `/opt/pfe-ai-service/datasets/lora_instructions_v2.jsonl`
3. Écrire `retrain_lora.py` qui :
   - Charge le LoRA existant `aiops-sre-lora-full` comme base
   - Entraîne 3 epochs, batch=2, lr=1e-4
   - Sauve dans `/opt/pfe-ai-service/model_artifacts/aiops-sre-lora-v2/`
4. Lancer en background :
   ```bash
   ssh kube@192.168.148.161 'nohup /opt/pfe-ai-service/.venv/bin/python /opt/pfe-ai-service/scripts/retrain_lora.py > /tmp/lora_train.log 2>&1 &'
   ```
5. Suivre : `ssh kube@192.168.148.161 'tail -f /tmp/lora_train.log'`
6. Quand fini : convertir LoRA → GGUF → Ollama create → swap modèle Ollama

**FALLBACK si LoRA training impossible** (disque saturé ou erreur) :
- Utiliser approche in-context : modifier le prompt LLM dans `inference.py` pour injecter les 51 types comme contexte
- Documenter dans le mémoire comme "perspective future" pour le full LoRA

### Phase 5 — 7 scénarios chaos réels (1h)
Voir `docs/CHAOS_ENGINEERING_GUIDE.md` pour les commandes exactes.
Prérequis : cluster k8s + AI service v3 up.
Tests :
1. CPU stress demo-app via `kubectl exec stress-ng`
2. Memory leak backend via `dd /dev/shm/leak`
3. `kill -1` répété 7× → POD_CRASH_LOOP
4. Disk full sur worker via `dd if=/dev/zero`
5. 100 requêtes parallèles → DB pool exhaustion
6. `kubectl scale coredns --replicas=0` → DNS chaos
7. E2E complete : crash → AI detect → Camunda → resolution

### Phase 6.5 — 3 couches AI + suppression Alertmanager (~1 jour)
Voir détails dans `docs/PLAN_TRAINING_IA.md` section "Phase 6.5".
SI temps insuffisant : skip et documenter comme "évolution future".

### Phase 6 — Doc finale (30 min)
- Créer `docs/AI_TRAINING_GUIDE.md`
- Update mémoire Claude avec versions finales
- Cleanup todo list

---

## 📋 État VMs (pour démarrage)

Quand prochaine session démarre :
```powershell
$vmrun = "C:\Program Files (x86)\VMware\VMware Workstation\vmrun.exe"
# Démarrer EN SÉQUENCE pour éviter cascade RAM Windows :
& $vmrun start "C:\Users\houss\Documents\Virtual Machines\k8s-aiCamunda\k8s-aiCamunda.vmx" nogui
Start-Sleep 30
& $vmrun start "C:\Users\houss\Documents\Virtual Machines\k8s-monitoring\k8s-monitoring.vmx" nogui
Start-Sleep 20
& $vmrun start "C:\Users\houss\Documents\Virtual Machines\k8s-master\k8s-master.vmx" nogui
Start-Sleep 20
& $vmrun start "C:\Users\houss\Documents\Virtual Machines\k8s-worker1\k8s-worker1.vmx" nogui
Start-Sleep 20
& $vmrun start "C:\Users\houss\Documents\Virtual Machines\k8s-worker2\k8s-worker2.vmx" nogui
```

---

## 🔑 Credentials & URLs

- SSH: `kube@192.168.148.161` (clé OK), `kube@192.168.148.{153,158,155}` (cluster, clé OK)
- App UI: `http://192.168.148.153:30880` (login: `superadmin` / `superadmin-change-me`)
- AI service: `http://192.168.148.161:8000`
- Ollama: `http://192.168.148.161:11434`
- Prometheus: `http://192.168.148.160:9090`

---

## ⚠️ RÈGLES STRICTES (du user)

1. **Pas de philosophie**. Pas d'action sans ordre explicite.
2. **Cluster = applications uniquement**. AI/Camunda sur VM 161, Monitoring sur VM 160.
3. **F16 Qwen actif** (ne pas revert à Q4).
4. **Docker Desktop incompatible avec 5 VMs simultanément** : si rebuild needed, stop VMs → Docker → build → save tar → stop Docker → restart VMs → SCP + import.
5. **RAM tight** : ne JAMAIS bump VMs au-delà des valeurs actuelles sans demander.

---

## 🎬 PROMPT À COPIER POUR REPRENDRE

> *"continue le plan training IA — phase 3 LoRA en background puis phase 5 chaos"*

Claude lira ce fichier `RESUME_HERE.md` + `docs/PLAN_TRAINING_IA.md` + `docs/CHAOS_ENGINEERING_GUIDE.md` + `docs/INCIDENT_TAXONOMY.md` et exécutera.
