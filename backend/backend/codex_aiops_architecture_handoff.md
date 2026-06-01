# Codex Handoff — Detailed Architecture and Execution Plan

## Project Name
**Hybrid AIOps Platform for Autonomous Incident Detection and Self-Healing in Kubernetes**

---

## 1. Purpose of the Project

This project is an **intelligent monitoring and remediation platform** for a Kubernetes-hosted application.

A Spring Boot backend is deployed on a Kubernetes cluster running on VMware VMs. The system collects:
- Prometheus metrics
- Loki logs
- Kubernetes and application health signals

Then an **AI microservice** analyzes these signals to:
- detect whether a real incident exists or not
- distinguish real incidents from noise
- classify incident type
- estimate incident severity
- recommend a remediation action

The backend stores the incident and decision data in PostgreSQL, and Camunda later orchestrates the corrective action on Kubernetes.

In simple terms:

> The application runs on Kubernetes. This project watches it, understands when something goes wrong, decides what to do, and helps trigger the appropriate corrective action.

---

## 2. Current Known Infrastructure

### 2.1 Virtual Machines
The project currently runs on VMware Workstation with:
- 1 Kubernetes master VM
- 2 Kubernetes worker VMs

Each VM is approximately:
- 40 GB disk
- 4 GB RAM
- 2 vCPUs

### 2.2 Kubernetes Cluster
- Kubernetes cluster already created and operational
- Master is control plane
- Workers host workloads
- Goal: if one worker fails, the system should continue operating on the other worker(s)

### 2.3 Namespaces
Main namespaces currently in use:
- `pfe` → application and database components
- `monitoring` → Prometheus, Grafana, Loki, Alloy/Promtail
- `cnpg-system` → CloudNativePG operator
- `local-path-storage` → storage provisioner

### 2.4 Application Stack
Current main application components:
- Spring Boot backend
- PostgreSQL via CloudNativePG (CNPG)
- Prometheus
- Grafana
- Loki
- Alloy (or Promtail equivalent log collection role)
- Camunda planned / partially integrated at workflow level

---

## 3. What Already Exists

### 3.1 Spring Boot Backend
The backend already provides:
- incident creation and update APIs
- incident history and audit trail
- action management
- workflow management
- human approval tasks
- fault injection engine for chaos testing and dataset generation

### 3.2 Monitoring Integration
The backend already exposes:
- `/actuator/prometheus`
- readiness and liveness probes
- ServiceMonitor integration for Prometheus scraping

### 3.3 Fault Injection Engine
The backend supports multiple synthetic incident scenarios such as:
- HTTP 500
- high latency
- DB pool exhaustion
- CPU stress
- memory stress
- readiness failure
- liveness failure
- crash on demand
- crash on startup

This is critical because it gives us a way to generate controlled incident data.

---

## 4. Final Target Architecture

## 4.1 High-Level Flow

```text
Kubernetes Application + Cluster
        ↓
Prometheus metrics + Loki logs + health signals
        ↓
AI microservice
        ↓
incident / noise decision
severity
incident type
recommended action
confidence
        ↓
Spring Boot backend
        ↓
PostgreSQL storage + workflow state
        ↓
Camunda
        ↓
Corrective action on Kubernetes
```

---

## 4.2 Concrete Component Responsibilities

### A. Kubernetes Cluster
Hosts all application and platform services.

Responsibilities:
- run backend pods
- run monitoring stack
- run database
- run AI microservice
- provide network and scheduling environment

### B. Spring Boot Backend
Acts as the **business orchestration API** and system of record.

Responsibilities:
- receive AI results
- create and update incidents
- create actions
- persist incident history
- manage workflow state
- prepare handoff to Camunda
- expose operational APIs and dashboards

### C. PostgreSQL (CNPG)
Stores:
- incidents
- actions
- workflows
- human decisions
- audit/history data
- optionally AI prediction records later

### D. Prometheus
Collects metrics from:
- Spring Boot actuator
- Kubernetes resources
- nodes and pods
- later possibly CNPG metrics and AI service metrics

### E. Loki
Stores and serves logs from:
- backend pods
- possibly other workloads
- later AI service logs as well

### F. Alloy / Promtail
Collects Kubernetes logs and pushes them to Loki.

### G. AI Microservice (to be built)
This is the **intelligence layer** of the project.

Responsibilities:
- query Loki for recent log windows
- query Prometheus for recent metric windows
- build features
- run inference
- determine if a real incident exists
- classify severity and incident type
- recommend action
- return structured JSON to backend

### H. Camunda
Responsible for execution workflow and remediation orchestration.

Responsibilities:
- receive decision from backend
- approve or route action if needed
- trigger corrective operations

---

## 5. Recommended AI Approach

## 5.1 First Principle
Do **not** start with a large heavy model.

For this environment, the best approach is a **lightweight AI microservice** with classical ML + compact embeddings.

## 5.2 Recommended First-Version AI Stack

### For Logs
Use:
- `sentence-transformers/all-MiniLM-L6-v2`

Purpose:
- create embeddings from log messages
- represent log windows compactly
- support classification/anomaly decisions later

Why:
- lightweight
- easy to run on CPU
- easy to containerize
- practical for lab deployment

### For Metrics + Final Decision
Use:
- `XGBoost`

Purpose:
- combine Prometheus-derived numeric features and log-derived features
- predict:
  - incident or no incident
  - severity
  - action recommendation

Why:
- lightweight
- strong baseline
- fast inference
- easy to explain and debug

### Optional Later Improvement
Possible future additions:
- TinyTimeMixer / compact time-series model for metrics
- LogBERT for richer log anomaly detection
- SHAP for explainability
- MLflow for experiment tracking

## 5.3 Why Not Start with LogBERT or a Large LLM
- more setup overhead
- more resource usage
- harder to debug
- more difficult to productionize in a small lab

For this project, **XGBoost + engineered features + MiniLM embeddings** is the most realistic starting point.

---

## 6. Deployment Recommendation

## 6.1 Where to Run the AI Service

### Development
During coding and debugging:
- run on local PC using Docker Desktop

### Final Project Deployment
For the real architecture:
- package the AI service in Docker
- deploy it as a pod inside Kubernetes on the VMs

## 6.2 Important Rule
The AI model/service must **not** live inside the Spring Boot process.

Correct design:
- Spring Boot = Java business/backend service
- AI service = separate Python microservice

This keeps responsibilities clear and makes the system easier to deploy and evolve.

---

## 7. Detailed Runtime Architecture

```text
[Users / Operators / UI]
          ↓
     Spring Boot Backend
          ↓
  stores incidents/actions/workflows
          ↓
      PostgreSQL (CNPG)

Observability Side:
-------------------
Application Pods / Kubernetes / Nodes
          ↓
      Alloy / Promtail
          ↓
          Loki

Application / Kubernetes metrics
          ↓
      Prometheus

AI Side:
--------
   AI Service (FastAPI, Docker, Python)
       ├─ Query Loki API
       ├─ Query Prometheus API
       ├─ Build features
       ├─ Run model inference
       └─ Return JSON prediction

Orchestration Side:
-------------------
Spring Boot Backend
          ↓
        Camunda
          ↓
   Remediation action on Kubernetes
```

---

## 8. AI Service Design

## 8.1 Technology Choice
Recommended stack:
- Python 3.11 or 3.10
- FastAPI
- Uvicorn
- requests / httpx
- pandas
- numpy
- scikit-learn
- xgboost
- sentence-transformers
- joblib

## 8.2 Responsibilities
The AI service should:
1. receive a request from backend or internal scheduler
2. pull a recent time window of logs from Loki
3. pull a recent time window of metrics from Prometheus
4. engineer features
5. run one or more models
6. return a compact result object

## 8.3 Proposed API Endpoints

### Health endpoints
- `GET /health`
- `GET /ready`

### Main prediction endpoint
- `POST /predict`

Example request:

```json
{
  "namespace": "pfe",
  "app": "backend",
  "window_minutes": 5
}
```

Example response:

```json
{
  "incident": true,
  "noise": false,
  "incident_type": "CPU_STRESS",
  "severity": "HIGH",
  "recommended_action": "RESTART_POD",
  "confidence": 0.91,
  "summary": "Sustained high CPU and repeated error patterns detected",
  "evidence": {
    "log_score": 0.84,
    "metric_score": 0.89
  }
}
```

### Optional future endpoints
- `POST /train`
- `POST /evaluate`
- `GET /model/info`
- `POST /dataset/build`

---

## 9. How the AI Service Gets Data

## 9.1 Logs
The AI service must **not** read logs directly from `/var/log/pods`.

Correct path:
- Alloy/Promtail already collects logs
- logs are pushed to Loki
- AI service queries Loki API

Example conceptual flow:

```text
Backend pod logs
    ↓
Alloy / Promtail
    ↓
Loki
    ↓
AI Service queries Loki API
```

## 9.2 Metrics
The AI service queries Prometheus API for recent metric windows.

Possible metrics to consume:
- request count
- error count / error rate
- latency
- CPU usage
- memory usage
- pod restart count
- probe failures if represented through metrics/events
- JVM / application metrics from Spring Boot actuator

---

## 10. Feature Engineering Plan

## 10.1 Log Features
For each time window, compute:
- number of log lines
- number of error lines
- number of warning lines
- unique message patterns
- restart-related keywords
- exception-related keywords
- average / pooled embedding using MiniLM

Possible keywords:
- `exception`
- `timeout`
- `connection refused`
- `out of memory`
- `crash`
- `readiness`
- `liveness`
- `database`

## 10.2 Metrics Features
For each time window, compute:
- average CPU
- max CPU
- average memory
- max memory
- request volume
- error rate
- p95 / p99 latency if available
- restart counts
- pod count
- unavailable replicas

## 10.3 Target Labels
For first model training, use labels such as:
- `incident` → yes/no
- `incident_type` → CPU_STRESS, MEMORY_STRESS, DB_POOL_EXHAUSTION, etc.
- `severity` → LOW, MEDIUM, HIGH, CRITICAL
- `action` → RESTART_POD, INVESTIGATION, ESCALATION, REMEDIATION, NOTIFICATION

---

## 11. Dataset Strategy

## 11.1 Best Data Source
The backend fault injection engine should be used to generate labeled incident data.

This is one of the strongest parts of the project.

## 11.2 Dataset Generation Workflow
For each fault scenario:
1. activate fault scenario
2. let the system run for a short period
3. query Loki logs for the window
4. query Prometheus metrics for the window
5. build feature vector
6. attach labels
7. save to CSV / parquet

Repeat for:
- normal behavior windows
- incident windows
- post-recovery windows

## 11.3 Example Labels
- `incident=true`
- `incident_type=CPU_STRESS`
- `severity=HIGH`
- `action=RESTART_POD`
- `root_cause=backend_cpu_saturation`

## 11.4 Important Note
A working first version does **not** require a huge dataset.

A small, controlled, well-labeled dataset from fault injection is enough to build the first useful baseline.

---

## 12. Backend Integration Plan

## 12.1 Integration Mode
Spring Boot should call the AI service using HTTP.

### Option A: backend-triggered
Backend calls AI service periodically or on demand.

### Option B: scheduled AI polling
AI service periodically scans the system and sends results to backend.

### Recommended for v1
Use backend-triggered or simple scheduled polling with a fixed time window.

## 12.2 Backend Responsibility
The backend should:
- call AI service
- receive prediction JSON
- create incident if needed
- assign severity and action
- persist history
- later route to Camunda

---

## 13. Camunda Integration Plan

Camunda should not be the first implementation target.

Recommended progression:
1. AI service predicts
2. backend stores result
3. backend exposes actions
4. then integrate Camunda workflow to execute or approve actions

Possible Camunda actions:
- restart pod
- scale deployment
- rollback deployment
- notify operator
- escalate to human approval

---

## 14. Phased Execution Plan

## Phase 0 — Stabilize the Platform
Before building AI, verify basic cluster reliability.

Tasks:
- investigate node instability and reboots
- add or fix metrics-server
- verify service discovery and scraping stability
- enable CNPG backups
- improve DB observability
- review local-path limitations for stateful workloads

Deliverable:
- stable lab environment with healthy monitoring

---

## Phase 1 — Build the AI Microservice Skeleton
Goal: create deployable service structure.

Tasks:
- create Python FastAPI service
- add Dockerfile
- add `/health`, `/ready`, `/predict`
- create config management for Loki and Prometheus URLs
- create internal clients for Loki and Prometheus APIs

Deliverable:
- Dockerized AI service skeleton running locally and in cluster

---

## Phase 2 — Implement Data Collection
Goal: retrieve observability data correctly.

Tasks:
- query Loki log windows
- query Prometheus metric windows
- normalize data
- implement feature extraction
- define schema for prediction input

Deliverable:
- working feature extraction pipeline

---

## Phase 3 — Build Dataset Generator
Goal: create training data from controlled faults.

Tasks:
- script fault scenario activation
- collect windows during scenarios
- export labeled dataset
- generate baseline CSV/parquet datasets

Deliverable:
- first labeled training dataset

---

## Phase 4 — Train Baseline Models
Goal: produce first real AI behavior.

Tasks:
- train XGBoost for incident detection
- train XGBoost or multiclass model for severity / action
- optionally use MiniLM embeddings for log representation
- evaluate metrics and thresholds

Deliverable:
- first trained model artifacts
- evaluation report

---

## Phase 5 — Integrate with Backend
Goal: make AI useful in the platform.

Tasks:
- backend calls `/predict`
- persist AI decision
- create incident/action/history records
- add confidence threshold logic

Deliverable:
- end-to-end incident flow without Camunda automation yet

---

## Phase 6 — Integrate Camunda
Goal: move from detection to remediation.

Tasks:
- map action recommendations to workflows
- trigger human approval for risky actions
- execute safe actions automatically
- record outcomes

Deliverable:
- end-to-end closed-loop remediation workflow

---

## Phase 7 — Feedback and Improvement
Goal: improve model quality and explainability.

Tasks:
- capture action success/failure
- capture post-remediation metrics
- refine labels
- retrain models
- add SHAP or lightweight explainability

Deliverable:
- improved model and continuous-learning pathway

---

## 15. Deployment Plan

## 15.1 Local Development
Use Docker Desktop on the developer PC.

Tasks:
- run FastAPI service locally
- connect to sample/mock data first
- validate endpoints
- optionally connect to cluster endpoints if accessible

## 15.2 Cluster Deployment
Deploy AI service to Kubernetes as:
- a Deployment
- a ClusterIP Service
- optional ServiceMonitor later

Recommended namespace:
- `pfe`

## 15.3 Runtime Requirements
Start CPU-only and lightweight.

Avoid:
- GPU assumptions
- large models
- excessive memory usage

---

## 16. Minimal Deliverables for Codex

Codex should first produce the following:

### Deliverable 1 — AI Service Repository Structure
Including:
- FastAPI app
- clients for Loki and Prometheus
- feature extraction module
- inference module
- config module
- Dockerfile
- requirements.txt
- README

### Deliverable 2 — Prediction Contract
Define:
- request JSON schema
- response JSON schema
- error behavior

### Deliverable 3 — Baseline Rule/ML Hybrid
Even before full training, implement:
- simple rules for obvious failures
- placeholder model pipeline
- clean extension points for XGBoost later

### Deliverable 4 — Kubernetes Deployment Files
Create:
- deployment yaml
- service yaml
- configmap / env vars
- optional secret support

### Deliverable 5 — Integration Notes
Explain:
- how backend should call AI service
- what fields backend should store
- what action mapping should be used

---

## 17. Recommended Repository Structure for AI Service

```text
ai-service/
├─ app/
│  ├─ main.py
│  ├─ api/
│  │  └─ routes.py
│  ├─ core/
│  │  ├─ config.py
│  │  └─ logging.py
│  ├─ clients/
│  │  ├─ loki_client.py
│  │  └─ prometheus_client.py
│  ├─ features/
│  │  ├─ log_features.py
│  │  ├─ metric_features.py
│  │  └─ combine.py
│  ├─ models/
│  │  ├─ loader.py
│  │  ├─ inference.py
│  │  └─ schemas.py
│  ├─ services/
│  │  └─ predictor.py
│  └─ utils/
│     └─ time_windows.py
├─ model_artifacts/
├─ tests/
├─ Dockerfile
├─ requirements.txt
└─ README.md
```

---

## 18. Non-Goals for the First Implementation

The first version does **not** need to:
- use a large LLM
- perform full autonomous remediation without review
- support every Kubernetes workload type
- solve full root-cause analysis perfectly
- train online continuously from day one

The first version should focus on:
- a working AI microservice
- useful predictions
- clean integration path
- simple, debuggable architecture

---

## 19. Risks and Engineering Constraints

### Technical Risks
- node instability and VM restarts can create noisy labels
- local-path storage is fragile for stateful components
- incomplete DB observability reduces feature quality
- no metrics-server weakens troubleshooting and visibility

### AI Risks
- too little data
- weak label quality
- overfitting to synthetic incidents
- confusing noise with incidents

### Recommended Mitigation
- start with hybrid rule + ML approach
- keep feature engineering interpretable
- collect both normal and faulty windows
- test on real cluster events as well as injected faults

---

## 20. Immediate Next Action List for Codex

1. Create the Python FastAPI AI service skeleton
2. Add Loki and Prometheus clients
3. Implement `/predict` endpoint
4. Implement basic log and metric feature extraction
5. Add Dockerfile and local Docker run instructions
6. Add Kubernetes deployment manifests
7. Define backend-to-AI JSON contract
8. Prepare extension points for XGBoost and MiniLM

---

## 21. Final Short Summary

This project is a **self-healing Kubernetes AIOps platform**.

The application runs on Kubernetes.
The monitoring stack observes it.
The AI service will read logs and metrics, decide whether a real incident exists, estimate severity, recommend an action, and send that decision back into the platform workflow.

The best practical implementation path is:
- lightweight Python AI microservice
- Dockerized
- developed locally on PC first
- deployed later into the Kubernetes cluster
- using MiniLM embeddings + XGBoost baseline
- integrated with Spring Boot and later Camunda


## Final AI Model Choice (Implementation Decision)

We will use a lightweight hybrid AI approach:

- Logs → embeddings using `all-MiniLM-L6-v2`
- Metrics → Prometheus features (no heavy model initially)
- Decision → XGBoost classifier:
  - incident vs noise
  - severity
  - action recommendation

Optional (later phase):
- LLM (e.g. Qwen2.5-Instruct) for explanation only