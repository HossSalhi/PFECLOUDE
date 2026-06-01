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

from dataclasses import asdict

from fastapi import APIRouter, BackgroundTasks, HTTPException

mlops_router = APIRouter(prefix="/mlops", tags=["mlops"])


def _svc():
    """Return the singleton PredictorService (and its MLOps sub-services)."""
    from app.api.routes import get_predictor_service
    return get_predictor_service()


# ------------------------------------------------------------------ #
#  System status                                                       #
# ------------------------------------------------------------------ #

@mlops_router.get("/status")
def mlops_status() -> dict:
    """Full MLOps system health overview."""
    svc = _svc()
    drift_report = svc.drift_detector.compute_drift()
    active = svc.model_registry.get_active()
    shadow = svc.model_registry.get_shadow()
    trainer_st = svc.auto_trainer.get_status()
    ab_st = svc.ab_tester.get_status()

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
            "new_incidents_since_last_train": trainer_st["new_incidents_since_last_train"],
            "trigger_threshold": trainer_st["trigger_threshold"],
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
