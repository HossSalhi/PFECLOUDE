# AI Service

Lightweight AIOps inference service for the PFE Kubernetes platform.

## What This Service Does
- Exposes health and readiness endpoints.
- Exposes `POST /predict` for incident/noise/severity/action decisions.
- Pulls logs from Loki and metrics from Prometheus.
- Builds hybrid feature vectors (log heuristics + log embeddings + metrics).
- Runs lightweight XGBoost decision models:
  - incident vs noise
  - severity
  - action recommendation

## Endpoints
- `GET /health`
- `GET /ready`
- `GET /metrics`
- `POST /predict`

## Quick Local Run
```bash
cd ai-service
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

For the target offline 5-VM architecture, this service is expected to run on the dedicated
`ai-camunda` VM as a normal Python service or a `systemd`-managed process.

For tests:
```bash
pip install -r requirements-dev.txt
```

If you only need embeddings dependencies:
```bash
pip install -r requirements-embeddings.txt
```

## Legacy Docker
```bash
cd ai-service
docker build -t pfe-ai-service:latest .
docker run --rm -p 8000:8000 pfe-ai-service:latest
```

This is kept only as a legacy fallback. It is no longer the primary runtime path for the project.

## Dataset Builder (Phase 2)
Generate a labeled dataset from event windows (fault-injection timeline):
```bash
cd ai-service
python -m scripts.build_dataset \
  --events datasets/fault_injection_events.example.csv \
  --output datasets/aiops_training_dataset.csv
```

Notes:
- Use `AIOPS_ENABLE_UPSTREAM_QUERIES=true` to query live Loki/Prometheus.
- Use `end_time` in your events file to replay historical windows.

## Baseline Training (Phase 3)
Train and export a hybrid multi-model artifact:
```bash
cd ai-service
python -m scripts.train_baseline \
  --dataset datasets/aiops_training_dataset.csv \
  --output-model model_artifacts/model.joblib \
  --output-metrics model_artifacts/model_metrics.json
```

The exported `model.joblib` contains XGBoost models for incident/severity/action and is loaded directly at runtime.

## Main Environment Variables
- `AIOPS_LOG_LEVEL` (default: `INFO`)
- `AIOPS_ENABLE_UPSTREAM_QUERIES` (default: `false`)
- `AIOPS_LOKI_BASE_URL` (default: `http://loki-gateway.monitoring.svc.cluster.local`)
- `AIOPS_PROMETHEUS_BASE_URL` (default: `http://prometheus-kube-prometheus-prometheus.monitoring.svc.cluster.local:9090`)
- `AIOPS_LOKI_TIMEOUT_SECONDS` (default: `15`)
- `AIOPS_PROMETHEUS_TIMEOUT_SECONDS` (default: `15`)
- `AIOPS_DEFAULT_WINDOW_MINUTES` (default: `5`)
- `AIOPS_MODEL_PATH` (default: `model_artifacts/model.joblib`)
- `AIOPS_LOG_EMBEDDING_MODEL_NAME` (default: `all-MiniLM-L6-v2`)
- `AIOPS_LOG_EMBEDDING_FALLBACK_DIM` (default: `384`)
- `AIOPS_LOG_EMBEDDING_MAX_LINES` (default: `200`)

## Notes
- By default, upstream querying is disabled to keep local development simple.
- In the current target architecture, the backend talks to the AI service through the dedicated AI/Camunda VM.
- If MiniLM cannot be loaded (for example no model cache/network), the service falls back to deterministic hashed log embeddings to remain operational.
