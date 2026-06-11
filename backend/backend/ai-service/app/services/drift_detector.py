"""
Feature drift detection using PSI (Population Stability Index) and KS test.
Equivalent to Evidently DataDriftPreset — no external framework required.

PSI interpretation (industry standard):
  PSI < 0.10  — stable, no action needed
  PSI 0.10–0.25 — slight drift, monitor closely
  PSI > 0.25  — significant drift → trigger retraining
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

MONITORED_FEATURES = [
    "cpu_usage_cores",
    "memory_working_set_bytes",
    "http_5xx_ratio",
    "http_latency_p95_seconds",
    "pod_restarts_total",
    "unavailable_replicas",
    "log_risk_score",
    "metric_risk_score",
    "overall_risk_score",
    "error_ratio",
    "error_line_count",
    "db_keyword_hits",
    "log_error_count",
    "network_rx_bytes",
]

PSI_WARN = 0.10
PSI_ALERT = 0.25
KS_PVALUE = 0.05


def _psi(reference: np.ndarray, current: np.ndarray, buckets: int = 10) -> float:
    """Population Stability Index between two distributions."""
    if len(reference) < 10 or len(current) < 10:
        return 0.0
    min_v = min(reference.min(), current.min())
    max_v = max(reference.max(), current.max())
    if max_v == min_v:
        return 0.0
    edges = np.linspace(min_v, max_v, buckets + 1)
    eps = 1e-6
    ref_cnt, _ = np.histogram(reference, bins=edges)
    cur_cnt, _ = np.histogram(current, bins=edges)
    ref_pct = (ref_cnt + eps) / (len(reference) + eps * buckets)
    cur_pct = (cur_cnt + eps) / (len(current) + eps * buckets)
    return float(np.sum((cur_pct - ref_pct) * np.log(cur_pct / ref_pct)))


class DriftDetector:
    """
    Tracks feature distributions over a rolling window.
    Reference window = first 500 predictions (training baseline).
    Current window  = last 100 predictions (production stream).
    """

    REFERENCE_SIZE = 500
    CURRENT_WINDOW = 100

    def __init__(self, persist_path: str = "model_artifacts/drift_history.json") -> None:
        self._path = Path(persist_path)
        self._reference: dict[str, list[float]] = {f: [] for f in MONITORED_FEATURES}
        self._current: dict[str, deque] = {
            f: deque(maxlen=self.CURRENT_WINDOW) for f in MONITORED_FEATURES
        }
        self._reference_locked = False
        self._prediction_count = 0
        self._last_report: dict[str, Any] = {}
        # /predict and /mlops/drift run in FastAPI's threadpool — record() (writes) and
        # compute_drift() (reads via list(deque)) can run concurrently across threads.
        # A single lock serialises mutation and snapshotting to avoid "deque mutated
        # during iteration" and torn _save() state.
        self._lock = threading.Lock()
        self._load()

    def record(self, features: dict[str, float]) -> None:
        """Record feature values from one prediction for drift tracking."""
        with self._lock:
            self._prediction_count += 1
            for feat in MONITORED_FEATURES:
                val = float(features.get(feat, 0.0))
                if not self._reference_locked:
                    self._reference[feat].append(val)
                    if len(self._reference[feat]) >= self.REFERENCE_SIZE:
                        self._reference_locked = True
                        logger.info(
                            "DriftDetector: reference window established (%d samples)",
                            self.REFERENCE_SIZE,
                        )
                self._current[feat].append(val)
            should_save = self._prediction_count % 50 == 0
            if should_save:
                self._save()

    def compute_drift(self) -> dict[str, Any]:
        """Compute PSI and KS test for all monitored features. Returns full report."""
        # Snapshot both windows under the lock, then compute outside it so the (potentially
        # slow) PSI/KS math never blocks concurrent record() calls from /predict.
        with self._lock:
            if not self._reference_locked:
                ref_size = min(len(v) for v in self._reference.values()) if self._reference else 0
                return {
                    "status": "warming_up",
                    "reference_samples": ref_size,
                    "required_samples": self.REFERENCE_SIZE,
                    "feature_drift": {},
                    "max_psi": 0.0,
                    "drifted_features": [],
                    "retrain_recommended": False,
                }
            ref_snapshot = {
                f: np.array(self._reference[f], dtype=np.float32) for f in MONITORED_FEATURES
            }
            cur_snapshot = {
                f: np.array(list(self._current[f]), dtype=np.float32) for f in MONITORED_FEATURES
            }
            prediction_count = self._prediction_count

        feature_drift: dict[str, dict] = {}
        max_psi = 0.0
        drifted: list[str] = []

        for feat in MONITORED_FEATURES:
            ref = ref_snapshot[feat]
            cur = cur_snapshot[feat]
            if len(cur) < 10:
                continue
            psi = _psi(ref, cur)
            status = "stable"
            if psi >= PSI_ALERT:
                status = "drifted"
                drifted.append(feat)
            elif psi >= PSI_WARN:
                status = "warning"
            entry: dict[str, Any] = {
                "psi": round(psi, 4),
                "status": status,
                "reference_mean": round(float(ref.mean()), 6),
                "current_mean": round(float(cur.mean()), 6),
            }
            feature_drift[feat] = entry
            if psi > max_psi:
                max_psi = psi

        ks_drifted: list[str] = []
        try:
            from scipy.stats import ks_2samp
            for feat in MONITORED_FEATURES:
                if feat not in feature_drift:
                    continue
                ref = ref_snapshot[feat]
                cur = cur_snapshot[feat]
                if len(cur) < 10:
                    continue
                _, p = ks_2samp(ref, cur)
                feature_drift[feat]["ks_p_value"] = round(float(p), 4)
                if p < KS_PVALUE:
                    ks_drifted.append(feat)
        except ImportError:
            pass

        retrain = max_psi >= PSI_ALERT or len(drifted) >= 3

        report: dict[str, Any] = {
            "status": "drifted" if drifted else ("warning" if max_psi >= PSI_WARN else "stable"),
            "reference_samples": len(ref_snapshot[MONITORED_FEATURES[0]]),
            "current_window_samples": len(cur_snapshot[MONITORED_FEATURES[0]]),
            "total_predictions": prediction_count,
            "max_psi": round(max_psi, 4),
            "drifted_features": drifted,
            "ks_drifted_features": ks_drifted,
            "feature_drift": feature_drift,
            "retrain_recommended": retrain,
            "computed_at": time.time(),
        }
        with self._lock:
            self._last_report = report
        return report

    def should_retrain_for_drift(self) -> bool:
        with self._lock:
            need_compute = self._reference_locked and not self._last_report
        if need_compute:
            self.compute_drift()  # acquires the lock itself — must not be held here
        with self._lock:
            return self._last_report.get("retrain_recommended", False)

    def reset_reference(self) -> None:
        """Reset reference window after retraining so the new model's baseline is rebuilt."""
        with self._lock:
            self._reference = {f: [] for f in MONITORED_FEATURES}
            self._reference_locked = False
            self._last_report = {}
            self._save()
        logger.info("DriftDetector: reference reset — will rebuild from next %d predictions", self.REFERENCE_SIZE)

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            state = {
                "reference": {k: v[-self.REFERENCE_SIZE:] for k, v in self._reference.items()},
                "reference_locked": self._reference_locked,
                "prediction_count": self._prediction_count,
            }
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(state))
            os.replace(str(tmp), str(self._path))
        except Exception as exc:
            logger.warning("DriftDetector save failed: %s", exc)

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            state = json.loads(self._path.read_text())
            loaded_ref = state.get("reference", {})
            for f in MONITORED_FEATURES:
                self._reference[f] = loaded_ref.get(f, [])
            self._reference_locked = state.get("reference_locked", False)
            self._prediction_count = state.get("prediction_count", 0)
            logger.info("DriftDetector: loaded state (%d predictions)", self._prediction_count)
        except Exception as exc:
            logger.warning("DriftDetector load failed: %s", exc)
