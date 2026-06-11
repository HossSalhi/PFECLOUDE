"""
Enterprise MLOps REST API endpoints.

GET  /mlops/status                    full system status (drift + registry + A/B + trainer)
GET  /mlops/drift                     per-feature PSI drift report
POST /mlops/drift/reset               reset reference distribution
GET  /mlops/models                    list all versioned models
GET  /mlops/models/active             active model metadata
POST /mlops/models/{version}/promote  manually promote a version
POST /mlops/models/rollback           rollback to previous version
POST /mlops/retrain                   trigger XGBoost retraining (background)
GET  /mlops/retrain/status            auto-trainer status + last run result
GET  /mlops/ab-test                   A/B test status
POST /mlops/ab-test/promote           manually promote shadow to active
"""
from __future__ import annotations

import time
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException

mlops_router = APIRouter(prefix="/mlops", tags=["mlops"])

# Cache the drift report for /mlops/status — compute_drift() recomputes per-feature PSI
# over the whole window, which is wasteful under Grafana polling (every ~15s).
_DRIFT_CACHE_TTL = 30.0
_drift_cache: dict[str, Any] = {"ts": 0.0, "report": None}


def _svc():
    """Return the singleton PredictorService with its MLOps sub-services.

    Raises 503 when MLOps is disabled (mlops_enabled=False) — otherwise the
    sub-services are None and every endpoint would surface a raw AttributeError 500.
    """
    from app.api.routes import get_predictor_service
    svc = get_predictor_service()
    if (
        svc.drift_detector is None
        or svc.model_registry is None
        or svc.auto_trainer is None
        or svc.ab_tester is None
    ):
        raise HTTPException(
            status_code=503,
            detail="MLOps subsystem disabled — set AIOPS_MLOPS_ENABLED=true to enable it",
        )
    return svc


def _cached_drift(svc) -> dict:
    """Return the drift report, recomputing at most once per _DRIFT_CACHE_TTL seconds."""
    now = time.time()
    if _drift_cache["report"] is not None and (now - _drift_cache["ts"]) < _DRIFT_CACHE_TTL:
        return _drift_cache["report"]
    report = svc.drift_detector.compute_drift()
    _drift_cache["report"] = report
    _drift_cache["ts"] = now
    return report


# ------------------------------------------------------------------ #
#  System status                                                       #
# ------------------------------------------------------------------ #

@mlops_router.get("/status")
def mlops_status() -> dict:
    """Full MLOps system health overview."""
    from app.core.config import get_settings
    svc = _svc()
    settings = get_settings()
    drift_report = _cached_drift(svc)
    active = svc.model_registry.get_active()
    shadow = svc.model_registry.get_shadow()
    trainer_st = svc.auto_trainer.get_status()
    ab_st = svc.ab_tester.get_status()

    new_incidents = trainer_st["new_incidents_since_last_train"]
    drift_alert = (drift_report.get("max_psi") or 0) > 0.25
    # LLM retrain is recommended when drift is significant AND enough real incidents
    # have accumulated.  Run manually: python scripts/llm_continuous_learning.py
    llm_retrain_recommended = new_incidents >= settings.llm_retrain_min_incidents and drift_alert

    return {
        "mlops_ready": True,
        "active_model": {
            "version": active.version if active else None,
            "avg_f1": active.avg_f1 if active else None,
            "training_size": active.training_size if active else None,
            "created_at": active.created_at if active else None,
        },
        "shadow_model": (
            {
                "version": shadow.version,
                "avg_f1": shadow.avg_f1,
                "training_size": shadow.training_size,
            }
            if shadow else None
        ),
        "drift": {
            "status": drift_report.get("status"),
            "max_psi": drift_report.get("max_psi"),
            "retrain_recommended": drift_report.get("retrain_recommended", False),
            "total_predictions": drift_report.get("total_predictions", 0),
        },
        "auto_trainer": {
            "is_training": trainer_st["is_training"],
            "new_incidents_since_last_train": new_incidents,
            "trigger_threshold": trainer_st["trigger_threshold"],
        },
        "llm": {
            "retrain_recommended": llm_retrain_recommended,
            "min_incidents_required": settings.llm_retrain_min_incidents,
            "new_incidents": new_incidents,
            "hint": "Run: python scripts/llm_continuous_learning.py" if llm_retrain_recommended else None,
        },
        "ab_test": {
            "active": ab_st["active"],
            "progress_pct": ab_st["progress_pct"],
            "primary_version": ab_st["primary_version"],
            "shadow_version": ab_st["shadow_version"],
        },
    }


# ------------------------------------------------------------------ #
#  Drift detection                                                     #
# ------------------------------------------------------------------ #

@mlops_router.get("/drift")
def get_drift_report() -> dict:
    """Full drift detection report with per-feature PSI scores."""
    return _svc().drift_detector.compute_drift()


@mlops_router.post("/drift/reset")
def reset_drift_reference() -> dict:
    """Reset drift reference window (call after manual retraining)."""
    _svc().drift_detector.reset_reference()
    return {
        "status": "reset",
        "message": f"Reference window cleared. Rebuilds from next 500 predictions.",
    }


# ------------------------------------------------------------------ #
#  Model registry                                                      #
# ------------------------------------------------------------------ #

@mlops_router.get("/models")
def list_models() -> dict:
    """List all registered model versions with metrics."""
    svc = _svc()
    return {
        "active_version": svc.model_registry.active_version,
        "shadow_version": svc.model_registry.shadow_version,
        "versions": svc.model_registry.list_versions(),
    }


@mlops_router.get("/models/active")
def get_active_model() -> dict:
    """Metadata of the currently active (production) model."""
    active = _svc().model_registry.get_active()
    if not active:
        raise HTTPException(status_code=404, detail="No active model registered")
    return {**asdict(active), "avg_f1": active.avg_f1}


@mlops_router.post("/models/{version}/promote")
def promote_model_version(version: str) -> dict:
    """Manually promote a specific version to active."""
    ok = _svc().model_registry.promote(version)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Version '{version}' not found")
    return {"status": "promoted", "version": version}


@mlops_router.post("/models/rollback")
def rollback_model() -> dict:
    """Rollback to the most recently archived model version."""
    restored = _svc().model_registry.rollback()
    if not restored:
        raise HTTPException(status_code=404, detail="No archived version available for rollback")
    return {"status": "rolled_back", "restored_version": restored}


@mlops_router.get("/models/compare/{v1}/{v2}")
def compare_models(v1: str, v2: str) -> dict:
    """Compare two model versions by avg F1 score."""
    result = _svc().model_registry.compare(v1, v2)
    if not result:
        raise HTTPException(status_code=404, detail="One or both versions not found")
    return result


# ------------------------------------------------------------------ #
#  Auto-retraining                                                     #
# ------------------------------------------------------------------ #

@mlops_router.post("/retrain")
def trigger_retrain(background_tasks: BackgroundTasks, force: bool = False) -> dict:
    """
    Trigger XGBoost retraining in background.
    Runs automatically if drift or incident count threshold is reached.
    Use ?force=true to retrain unconditionally.
    """
    svc = _svc()
    if svc.auto_trainer.is_training:
        return {"status": "already_running", "message": "Retraining is already in progress"}

    should, reason = svc.auto_trainer.should_retrain()
    if not should and not force:
        st = svc.auto_trainer.get_status()
        return {
            "status": "skipped",
            "reason": reason,
            "new_incidents": st["new_incidents_since_last_train"],
            "trigger_threshold": st["trigger_threshold"],
            "hint": "Pass ?force=true to retrain unconditionally",
        }

    trigger = reason if should else "forced"
    background_tasks.add_task(svc.auto_trainer.retrain, trigger)
    return {
        "status": "started",
        "trigger_reason": trigger,
        "message": "Retraining running in background. Poll /mlops/retrain/status.",
    }


@mlops_router.get("/retrain/status")
def retrain_status() -> dict:
    """Auto-trainer current status and last completed run result."""
    return _svc().auto_trainer.get_status()


# ------------------------------------------------------------------ #
#  A/B testing                                                         #
# ------------------------------------------------------------------ #

@mlops_router.get("/ab-test")
def ab_test_status() -> dict:
    """Current A/B test status: progress, agreement rate, winner."""
    return _svc().ab_tester.get_status()


@mlops_router.post("/ab-test/promote")
def ab_promote_shadow() -> dict:
    """Manually promote the shadow model to active (skip sample target)."""
    ok = _svc().ab_tester.promote_shadow_now()
    if not ok:
        raise HTTPException(status_code=404, detail="No shadow model currently registered")
    return {"status": "promoted", "message": "Shadow model promoted to active"}
