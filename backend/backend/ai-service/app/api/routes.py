from functools import lru_cache
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest

from app.core.config import get_settings
from app.models.schemas import (
    LearnRequest,
    LearnResponse,
    PredictRequest,
    PredictResponse,
    CorrelateRequest,
    CorrelateResponse,
    AppSignalSchema,
    ProactiveRequest,
    ProactiveResponse,
    MetricTrendSchema,
)
from app.services.predictor import PredictorService
from app.services.correlation_service import CorrelationService
from app.services.proactive_service import ProactiveService

router = APIRouter()

# Set by main.py lifespan after monitor is created
_monitor = None


def set_monitor(monitor) -> None:
    global _monitor
    _monitor = monitor

PREDICT_REQUESTS_TOTAL = Counter(
    "aiops_predict_requests_total",
    "Total prediction requests by outcome",
    labelnames=("outcome",),
)


@lru_cache(maxsize=1)
def get_predictor_service() -> PredictorService:
    return PredictorService(get_settings())


@lru_cache(maxsize=1)
def get_correlation_service() -> CorrelationService:
    return CorrelationService(get_settings())


@lru_cache(maxsize=1)
def get_proactive_service() -> ProactiveService:
    return ProactiveService(get_settings())


@router.get("/health")
def health() -> dict[str, str]:
    settings = get_settings()
    return {"status": "ok", "service": settings.app_name, "version": settings.app_version}


@router.get("/ready")
def ready() -> dict[str, bool | str]:
    predictor = get_predictor_service()
    if not predictor.model_loaded:
        raise HTTPException(status_code=503, detail="Model not loaded yet")
    return {"status": "ready", "model_loaded": True}


@router.get("/metrics")
def metrics() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@router.post("/predict", response_model=PredictResponse)
def predict(request: PredictRequest) -> PredictResponse:
    predictor = get_predictor_service()
    try:
        response = predictor.predict(request)
        PREDICT_REQUESTS_TOTAL.labels(outcome="success").inc()
        return response
    except Exception as exc:
        PREDICT_REQUESTS_TOTAL.labels(outcome="failure").inc()
        raise HTTPException(status_code=500, detail=f"Prediction failed: {exc}") from exc


@router.post("/learn", response_model=LearnResponse)
def learn(request: LearnRequest) -> LearnResponse:
    """Store a resolved incident outcome in ChromaDB for future in-context learning."""
    predictor = get_predictor_service()
    stored = predictor.learn(
        features=request.features,
        incident_type=request.incident_type,
        severity=request.severity,
        action=request.action,
        executable=request.executable,
        success=request.success,
        minutes_to_resolve=request.minutes_to_resolve,
        app=request.app,
        namespace=request.namespace,
        confidence=request.confidence,
    )
    return LearnResponse(stored=stored, memory_count=predictor.memory_count())


@router.get("/memory/stats")
def memory_stats() -> dict:
    """Return ChromaDB memory store statistics."""
    predictor = get_predictor_service()
    available = predictor.memory_store.available if predictor.memory_store else False
    return {
        "available": available,
        "incident_count": predictor.memory_count(),
    }


@router.post("/correlate", response_model=CorrelateResponse)
def correlate(request: CorrelateRequest) -> CorrelateResponse:
    svc = get_correlation_service()
    try:
        result = svc.correlate(namespace=request.namespace, window_minutes=request.window_minutes)
        return CorrelateResponse(
            correlated=result.correlated,
            root_cause=result.root_cause,
            affected_apps=result.affected_apps,
            probable_cause=result.probable_cause,
            confidence=result.confidence,
            signals=[AppSignalSchema(**vars(s)) for s in result.signals],
            explanation=result.explanation
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Correlation failed: {exc}") from exc


@router.get("/monitor/status")
def monitor_status() -> dict:
    """Return the autonomous monitor live status."""
    if _monitor is None:
        return {"enabled": False, "running": False, "message": "Monitor not initialized"}
    status = _monitor.get_status()
    return {
        "enabled": status.enabled,
        "running": status.running,
        "total_cycles": status.total_cycles,
        "incidents_triggered": status.incidents_triggered,
        "last_run_utc": status.last_run_utc,
        "last_apps_analyzed": status.last_apps_analyzed,
        "last_anomalies_found": status.last_anomalies_found,
        "active_cooldowns": status.cooldowns,
        "recent_errors": status.recent_errors,
    }


@router.post("/predict/proactive", response_model=ProactiveResponse)
def predict_proactive(request: ProactiveRequest) -> ProactiveResponse:
    svc = get_proactive_service()
    try:
        result = svc.predict_proactive(
            namespace=request.namespace,
            app=request.app,
            window_minutes=request.window_minutes
        )
        return ProactiveResponse(
            needs_attention=result.needs_attention,
            predicted_incident=result.predicted_incident,
            time_horizon_minutes=result.time_horizon_minutes,
            severity=result.severity,
            incident_type=result.incident_type,
            confidence=result.confidence,
            trends=[MetricTrendSchema(**vars(t)) for t in result.trends],
            explanation=result.explanation,
            recommended_action=result.recommended_action
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Proactive prediction failed: {exc}") from exc


@router.get("/monitor/maintenance")
def get_maintenance_mode() -> dict:
    """Return the current maintenance mode state of the autonomous monitor."""
    if _monitor is None:
        return {"maintenance_mode": False, "message": "Monitor not initialized"}
    return {"maintenance_mode": _monitor.is_maintenance_mode()}


@router.post("/monitor/maintenance")
def set_maintenance_mode(enabled: bool) -> dict:
    """
    Toggle maintenance mode on the autonomous monitor.
    When enabled, the monitor loop skips analysis and does not create incidents.
    Use during planned maintenance to avoid false-positive incident storms.
    """
    if _monitor is None:
        raise HTTPException(status_code=503, detail="Monitor not initialized")
    _monitor.set_maintenance_mode(enabled)
    return {
        "maintenance_mode": enabled,
        "message": "Maintenance mode enabled — monitor paused" if enabled
                   else "Maintenance mode disabled — monitor resuming",
    }
