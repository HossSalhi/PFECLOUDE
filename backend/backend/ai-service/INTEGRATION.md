# Backend Integration Contract

## Endpoint
- URL (offline 5-VM target): `http://192.168.148.161:8000/predict`
- Legacy in-cluster fallback: `http://ai-service.pfe.svc.cluster.local:8000/predict`
- Method: `POST`
- Content-Type: `application/json`

## Backend Entry Point
- Backend endpoint: `POST /api/ai/predict`
- Backend request body:
```json
{
  "namespace": "pfe",
  "app": "backend",
  "windowMinutes": 5,
  "createIncident": true
}
```
- `createIncident=true`: backend applies orchestration (incident/workflow/action creation).
- `createIncident=false`: backend returns prediction only, without creating incident records.

## Request
```json
{
  "namespace": "pfe",
  "app": "backend",
  "window_minutes": 5
}
```

Fields:
- `namespace` (string, required): Kubernetes namespace to analyze.
- `app` (string, required): Application selector key used for log/metric query patterns.
- `window_minutes` (int, optional): Lookback window in minutes.
- `end_time` (ISO datetime, optional): Window end time; defaults to now UTC.

## Response
```json
{
  "incident": true,
  "noise": false,
  "incident_type": "CPU_STRESS",
  "severity": "HIGH",
  "recommended_action": "RESTART_OR_SCALE",
  "confidence": 0.87,
  "summary": "High risk pattern detected",
  "evidence": {
    "log_score": 0.41,
    "metric_score": 0.92,
    "rule_score": 0.71,
    "model_score": null
  },
  "features": {
    "log_line_count": 120,
    "error_line_count": 42
  },
  "window_start": "2026-04-10T17:10:00Z",
  "window_end": "2026-04-10T17:15:00Z"
}
```

## Suggested Backend Persistence Mapping
- `incident`: `Incident.status` creation/update gate
- `severity`: `Incident.severity`
- `incident_type`: `Incident.title` or `IncidentDecision.category`
- `recommended_action`: `IncidentAction.actionType` candidate
- `confidence`: `IncidentDecision.confidence`
- `summary`: `IncidentDecision.summary`
- `evidence` + `features`: store as JSON payload in decision/audit history

## Error Behavior
- Service returns HTTP `500` with FastAPI error body for unexpected failures.
- Backend should:
1. record AI call failure in audit
2. avoid auto-remediation on AI-call failure
3. fallback to human/manual triage path
