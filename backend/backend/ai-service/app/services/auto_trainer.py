"""
Automated XGBoost retraining pipeline — Kubeflow Pipelines equivalent.

Retraining is triggered automatically when:
  1. Drift detector reports significant PSI drift (>0.25)
  2. More than N new labeled incidents accumulated since last training

Pipeline:  trigger → load_data → train → cross-validate → promote_or_discard
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

RETRAIN_TRIGGER_INCIDENTS = 25
MIN_IMPROVEMENT = 0.005    # Promote new model only if avg_F1 improves by ≥ 0.5%
MIN_TRAINING_SIZE = 200    # Refuse promotion when the dataset is too small for reliable CV scores
LAST_RETRAIN_FILE = "model_artifacts/last_retrain.json"
MAX_DATASET_ROWS = 2000    # Keep only the most recent N rows to prevent quality degradation

# When set, auto_trainer will launch the LLM fine-tuning script after XGBoost retraining
# if enough new incidents are available (llm_retrain_min_incidents).
_LLM_FINETUNE_SCRIPT = os.environ.get(
    "AIOPS_LLM_FINETUNE_SCRIPT", "scripts/llm_continuous_learning.py"
)

# Features stored in ChromaDB incident metadata — subset of full feature set.
# Used to harvest real production data into the training CSV.
_CHROMA_FEATURES = [
    "cpu_usage_cores", "memory_working_set_bytes", "http_5xx_ratio",
    "http_latency_p95_seconds", "pod_restarts_total", "unavailable_replicas",
    "db_keyword_hits", "db_pool_pending", "log_risk_score", "metric_risk_score",
    "overall_risk_score", "error_ratio", "error_line_count", "restart_keyword_hits",
]


class AutoTrainer:
    """
    Orchestrates the full retraining pipeline end-to-end.

    Usage:
      trainer.record_new_incident()       → call after each resolved incident
      trainer.maybe_trigger_async()       → call periodically (e.g. after each learn())
      trainer.retrain()                   → blocking call for manual / background thread
    """

    def __init__(
        self,
        model_registry,
        drift_detector,
        dataset_path: str = "datasets/aiops_training_dataset.csv",
        retrain_trigger_count: int = RETRAIN_TRIGGER_INCIDENTS,
        llm_auto_retrain: bool = False,
        llm_retrain_min_incidents: int = 20,
    ) -> None:
        self._registry = model_registry
        self._drift = drift_detector
        self._dataset_path = Path(dataset_path)
        self._trigger_count = retrain_trigger_count
        self._llm_auto_retrain = llm_auto_retrain
        self._llm_retrain_min_incidents = llm_retrain_min_incidents
        self._new_incidents: int = 0
        self._is_training: bool = False
        self._last_retrain: dict[str, Any] | None = self._load_last_retrain()

    @property
    def is_training(self) -> bool:
        return self._is_training

    def record_new_incident(self) -> None:
        """Increment counter of new production incidents since last training."""
        self._new_incidents += 1

    def should_retrain(self) -> tuple[bool, str]:
        """Returns (should_retrain, reason_string)."""
        if self._is_training:
            return False, "already_training"
        if self._drift.should_retrain_for_drift():
            return True, "feature_drift_detected"
        if self._new_incidents >= self._trigger_count:
            return True, f"accumulated_{self._new_incidents}_new_incidents"
        return False, "below_threshold"

    def maybe_trigger_async(self) -> bool:
        """Launch retraining in a background thread if triggered. Returns True if started."""
        should, reason = self.should_retrain()
        if not should:
            return False
        import threading
        t = threading.Thread(target=self.retrain, kwargs={"trigger": reason}, daemon=True)
        t.start()
        logger.info("AutoTrainer: retraining started in background (reason: %s)", reason)
        return True

    def retrain(self, trigger: str = "manual") -> dict[str, Any]:
        """
        Full retraining pipeline (blocking).
        Returns result dict with status, metrics, and version info.
        """
        if self._is_training:
            return {"status": "already_training", "skipped": True}

        self._is_training = True
        started_at = time.time()
        result: dict[str, Any] = {"started_at": started_at, "trigger": trigger}

        try:
            logger.info("AutoTrainer: starting pipeline (trigger=%s)", trigger)

            pruned = self._prune_dataset()
            if pruned > 0:
                logger.info("AutoTrainer: pruned %d stale rows (dataset capped at %d)", pruned, MAX_DATASET_ROWS)

            harvested = self._harvest_chroma_to_csv()
            if harvested > 0:
                logger.info("AutoTrainer: harvested %d real incident rows from ChromaDB", harvested)

            data = self._load_data()
            if data is None:
                result.update({"status": "failed", "reason": "dataset_not_found"})
                return result

            X, y_incident, y_severity, y_action, feature_names = data
            logger.info("AutoTrainer: %d samples × %d features", len(X), len(feature_names))

            bundle, metrics = self._train(X, y_incident, y_severity, y_action, feature_names)
            logger.info(
                "AutoTrainer: new metrics — incident=%.3f action=%.3f severity=%.3f",
                metrics["f1_incident"], metrics["f1_action"], metrics["f1_severity"],
            )

            active = self._registry.get_active()
            promote = True
            promote_reason = "first_model"

            # Guard: refuse promotion when the dataset is too small — cross-validation
            # F1 scores on < MIN_TRAINING_SIZE rows are unreliable and may promote a worse model.
            if len(X) < MIN_TRAINING_SIZE:
                promote = False
                promote_reason = f"dataset_too_small_{len(X)}_lt_{MIN_TRAINING_SIZE}_rows"
                logger.warning(
                    "AutoTrainer: dataset has only %d rows (min=%d) — skipping promotion, "
                    "setting as shadow for A/B evaluation only.",
                    len(X), MIN_TRAINING_SIZE,
                )
            elif active:
                new_avg = (metrics["f1_incident"] + metrics["f1_action"] + metrics["f1_severity"]) / 3
                delta = new_avg - active.avg_f1
                if delta >= MIN_IMPROVEMENT:
                    promote = True
                    promote_reason = f"improved_avg_f1_by_{delta:.4f}"
                else:
                    promote = False
                    promote_reason = f"no_improvement_delta={delta:.4f}"

            new_mv = self._registry.register(
                model_bundle=bundle,
                metrics=metrics,
                training_size=len(X),
                feature_count=len(feature_names),
                notes=f"auto-retrain trigger={trigger}",
                set_as_shadow=not promote,
            )

            if promote:
                self._registry.promote(new_mv.version)
                self._drift.reset_reference()

            result.update({
                "status": "completed",
                "new_version": new_mv.version,
                "promoted": promote,
                "promote_reason": promote_reason,
                "metrics": metrics,
                "training_size": len(X),
                "duration_seconds": round(time.time() - started_at, 1),
            })

            self._new_incidents = 0
            self._save_last_retrain(result)
            logger.info("AutoTrainer: pipeline completed — %s", result)

            # Optionally trigger LLM fine-tuning right after XGBoost retraining.
            # Disabled by default (requires GPU time on .161).
            # Enable via AIOPS_LLM_AUTO_RETRAIN=true + AIOPS_LLM_RETRAIN_MIN_INCIDENTS=20.
            if self._llm_auto_retrain and len(X) >= self._llm_retrain_min_incidents:
                self._maybe_finetune_llm(result)

        except Exception as exc:
            logger.exception("AutoTrainer: pipeline failed: %s", exc)
            result.update({"status": "failed", "error": str(exc)})
        finally:
            self._is_training = False

        return result

    def get_status(self) -> dict[str, Any]:
        return {
            "is_training": self._is_training,
            "new_incidents_since_last_train": self._new_incidents,
            "trigger_threshold": self._trigger_count,
            "last_retrain": self._last_retrain,
            "llm_auto_retrain": self._llm_auto_retrain,
            "llm_retrain_min_incidents": self._llm_retrain_min_incidents,
        }

    def _maybe_finetune_llm(self, xgb_result: dict[str, Any]) -> None:
        """
        Launch the LLM fine-tuning script as a non-blocking subprocess.
        Only called when llm_auto_retrain=True AND the dataset is large enough.
        The subprocess is fire-and-forget; stdout/stderr go to the service log.
        """
        script = Path(_LLM_FINETUNE_SCRIPT)
        if not script.exists():
            logger.warning(
                "LLM fine-tune script not found at %s — skipping auto LLM retrain. "
                "Set AIOPS_LLM_FINETUNE_SCRIPT to the correct path.", script
            )
            return
        logger.info(
            "AutoTrainer: launching LLM fine-tuning (training_size=%d, script=%s)",
            xgb_result.get("training_size", "?"), script,
        )
        try:
            proc = subprocess.Popen(
                ["python", str(script)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            # Read output in a daemon thread so it appears in the service log
            import threading
            def _drain(p: subprocess.Popen) -> None:
                for line in p.stdout:
                    logger.info("[llm-finetune] %s", line.rstrip())
                p.wait()
                logger.info("LLM fine-tuning subprocess exited with code %d", p.returncode)
            threading.Thread(target=_drain, args=(proc,), daemon=True).start()
        except Exception as exc:
            logger.error("Failed to launch LLM fine-tuning subprocess: %s", exc)

    # ------------------------------------------------------------------ #
    #  Internal helpers                                                    #
    # ------------------------------------------------------------------ #

    def _prune_dataset(self) -> int:
        """Cap the training CSV at MAX_DATASET_ROWS by dropping the oldest rows.
        Older incidents drift out of distribution; keeping only recent data
        prevents the model from learning on stale patterns.
        Returns number of rows removed (0 if no pruning needed).
        """
        try:
            import pandas as pd
            path = self._dataset_path
            if not path.exists():
                return 0
            df = pd.read_csv(str(path))
            before = len(df)
            if before <= MAX_DATASET_ROWS:
                return 0
            df = df.tail(MAX_DATASET_ROWS).reset_index(drop=True)
            df.to_csv(str(path), index=False)
            return before - len(df)
        except Exception as exc:
            logger.warning("AutoTrainer: dataset pruning failed (non-critical): %s", exc)
            return 0

    def _load_data(self):
        """Load CSV dataset. Returns (X, y_incident, y_severity, y_action, feature_names) or None."""
        try:
            import numpy as np
            import pandas as pd

            path = self._dataset_path
            if not path.exists():
                for alt in [
                    Path("datasets/aiops_training_dataset.csv"),
                    Path("datasets/training_dataset.csv"),
                ]:
                    if alt.exists():
                        path = alt
                        break
                else:
                    logger.warning("AutoTrainer: no training dataset found")
                    return None

            df = pd.read_csv(str(path))
            logger.info("AutoTrainer: loaded %d rows from %s", len(df), path)

            if df.empty:
                logger.error("AutoTrainer: dataset at %s is empty — aborting", path)
                return None

            for required_col in ("incident", "severity", "recommended_action"):
                if required_col not in df.columns:
                    logger.error("AutoTrainer: required column '%s' missing from dataset", required_col)
                    return None

            if len(df) < 5:
                logger.error("AutoTrainer: dataset has only %d row(s) — too small to cross-validate", len(df))
                return None

            label_cols = {"incident", "severity", "recommended_action"}
            feature_cols = [c for c in df.columns if c not in label_cols]

            X = df[feature_cols].fillna(0).values.astype(np.float32)

            if X.shape[0] == 0 or X.shape[1] == 0:
                logger.error("AutoTrainer: feature matrix is empty after processing — aborting")
                return None
            y_incident = df["incident"].values if "incident" in df.columns else np.zeros(len(df), dtype=int)
            y_severity = df["severity"].values if "severity" in df.columns else np.zeros(len(df), dtype=int)
            y_action = df["recommended_action"].values if "recommended_action" in df.columns else np.zeros(len(df), dtype=int)

            return X, y_incident, y_severity, y_action, feature_cols

        except Exception as exc:
            logger.error("AutoTrainer: failed to load data: %s", exc)
            return None

    def _train(self, X, y_incident, y_severity, y_action, feature_names):
        """Train 3 XGBoost classifiers with 5-fold CV. Returns (bundle, metrics_dict)."""
        import numpy as np
        from sklearn.model_selection import cross_val_score
        from sklearn.preprocessing import LabelEncoder
        import xgboost as xgb

        params = dict(
            n_estimators=300,
            max_depth=6,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=42,
        )

        metrics: dict[str, float] = {}
        models: dict[str, Any] = {}

        # Incident (binary)
        clf_i = xgb.XGBClassifier(**params, eval_metric="logloss")
        scores_i = cross_val_score(clf_i, X, y_incident, cv=5, scoring="f1_macro")
        clf_i.fit(X, y_incident)
        models["incident"] = clf_i
        metrics["f1_incident"] = round(float(scores_i.mean()), 4)

        # Severity (multi-class)
        le_sev = LabelEncoder()
        y_sev_enc = le_sev.fit_transform(y_severity)
        clf_s = xgb.XGBClassifier(
            **params,
            objective="multi:softmax",
            num_class=len(le_sev.classes_),
        )
        scores_s = cross_val_score(clf_s, X, y_sev_enc, cv=5, scoring="f1_macro")
        clf_s.fit(X, y_sev_enc)
        models["severity"] = clf_s
        metrics["f1_severity"] = round(float(scores_s.mean()), 4)

        # Action (multi-class)
        le_act = LabelEncoder()
        y_act_enc = le_act.fit_transform(y_action)
        clf_a = xgb.XGBClassifier(
            **params,
            objective="multi:softmax",
            num_class=len(le_act.classes_),
        )
        scores_a = cross_val_score(clf_a, X, y_act_enc, cv=5, scoring="f1_macro")
        clf_a.fit(X, y_act_enc)
        models["action"] = clf_a
        metrics["f1_action"] = round(float(scores_a.mean()), 4)

        bundle = {
            "models": models,
            "feature_order": feature_names,
            "severity_labels": list(le_sev.classes_),
            "action_labels": list(le_act.classes_),
            "training_metadata": {
                "trained_at": time.time(),
                "training_size": len(X),
                **metrics,
            },
        }
        return bundle, metrics

    def _harvest_chroma_to_csv(self, max_entries: int = 500) -> int:
        """
        Fetch resolved incident vectors stored in ChromaDB by IncidentLearningService
        and append them as feature rows to the training CSV.
        Returns number of rows appended; 0 on any error or if ChromaDB unavailable.
        """
        try:
            import chromadb
            import pandas as pd
            from app.core.config import get_settings

            settings = get_settings()
            if not settings.memory_enabled:
                return 0

            if settings.chroma_mode == "http":
                client = chromadb.HttpClient(
                    host=settings.chroma_http_host,
                    port=settings.chroma_http_port,
                    ssl=settings.chroma_http_ssl,
                )
            else:
                client = chromadb.PersistentClient(path=settings.chroma_persist_dir)

            collection = client.get_or_create_collection("helios_incidents")
            count = collection.count()
            if count == 0:
                return 0

            results = collection.get(
                limit=min(max_entries, count),
                include=["metadatas"],
            )
            metadatas = results.get("metadatas") or []
            if not metadatas:
                return 0

            new_rows = []
            for meta in metadatas:
                if not meta or not meta.get("incident_type"):
                    continue
                row: dict = {
                    "incident": 1 if meta.get("resolved", True) else 0,
                    "severity": meta.get("severity", "MEDIUM"),
                    "recommended_action": meta.get("action", "INVESTIGATE"),
                }
                for feat in _CHROMA_FEATURES:
                    row[feat] = float(meta.get(feat, 0.0))
                new_rows.append(row)

            if not new_rows:
                return 0

            csv_path = self._dataset_path
            if not csv_path.exists():
                return 0

            existing = pd.read_csv(str(csv_path))
            incoming = pd.DataFrame(new_rows)
            # Align columns — fill missing feature cols with 0, drop extras not in CSV
            for col in existing.columns:
                if col not in incoming.columns:
                    incoming[col] = 0
            incoming = incoming[[c for c in existing.columns if c in incoming.columns]]
            combined = pd.concat([existing, incoming], ignore_index=True)
            combined.to_csv(str(csv_path), index=False)
            return len(new_rows)

        except Exception as exc:
            logger.debug("AutoTrainer: ChromaDB harvest skipped (%s)", exc)
            return 0

    def _save_last_retrain(self, result: dict) -> None:
        try:
            Path(LAST_RETRAIN_FILE).parent.mkdir(parents=True, exist_ok=True)
            Path(LAST_RETRAIN_FILE).write_text(json.dumps(result, indent=2))
        except Exception as exc:
            logger.warning("AutoTrainer: could not save last_retrain: %s", exc)

    def _load_last_retrain(self) -> dict | None:
        p = Path(LAST_RETRAIN_FILE)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text())
        except Exception:
            return None
