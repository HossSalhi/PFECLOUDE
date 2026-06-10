"""
A/B testing in shadow mode — Seldon/BentoML equivalent.

Shadow mode: primary model answers the request (production traffic unaffected).
Shadow model runs silently in parallel. After sample_target predictions,
the winner is computed using registered F1 scores and optionally auto-promoted.

Auto-promotion rule: shadow must beat primary by ≥ 2% avg_F1.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

AB_HISTORY_FILE = "model_artifacts/ab_test_history.json"
DEFAULT_SAMPLE_TARGET = 100
MIN_PROMOTION_DELTA = 0.02   # shadow must exceed primary avg_F1 by this margin


class ABTester:
    """
    Runs the shadow XGBoost model silently alongside the primary model.
    Tracks agreement rate and metric F1 delta to decide promotion.
    """

    def __init__(
        self,
        model_registry,
        sample_target: int = DEFAULT_SAMPLE_TARGET,
        auto_promote: bool = True,
    ) -> None:
        self._registry = model_registry
        self._sample_target = sample_target
        self._auto_promote = auto_promote
        # Per-session counters
        self._session_id: str = f"ab_{int(time.time())}"
        self._agreements: int = 0
        self._total: int = 0
        self._winner: str | None = None
        self._history: list[dict[str, Any]] = []
        # Shadow bundle cache — avoids repeated joblib.load() from disk for each shadow run
        self._shadow_bundle_cache: Any | None = None
        self._shadow_version_cached: str | None = None
        self._load()

    @property
    def active(self) -> bool:
        """True when a shadow model is registered and testing is ongoing."""
        return (
            self._registry.shadow_version is not None
            and self._winner is None
            and self._total < self._sample_target
        )

    def run_shadow(
        self,
        feature_vector: np.ndarray,
        primary_incident_pred: int,
    ) -> dict[str, Any] | None:
        """
        Run the shadow model on the same feature vector.
        Returns comparison dict (NOT returned to the API caller).
        """
        if not self.active:
            return None

        current_shadow_version = self._registry.shadow_version
        if current_shadow_version != self._shadow_version_cached:
            self._shadow_bundle_cache = self._registry.load_shadow_bundle()
            self._shadow_version_cached = current_shadow_version
        shadow_bundle = self._shadow_bundle_cache
        if shadow_bundle is None:
            return None

        try:
            shadow_model = shadow_bundle.get("models", {}).get("incident") if isinstance(shadow_bundle, dict) else None
            if shadow_model is None:
                shadow_model = getattr(shadow_bundle, "incident_model", None)
            if shadow_model is None:
                return None

            shadow_pred = int(shadow_model.predict(feature_vector)[0])
            agreement = shadow_pred == primary_incident_pred

            self._total += 1
            if agreement:
                self._agreements += 1

            if self._total >= self._sample_target:
                self._finalize()

            return {
                "sample": self._total,
                "primary": primary_incident_pred,
                "shadow": shadow_pred,
                "agreement": agreement,
            }
        except Exception as exc:
            logger.warning("ABTester: shadow prediction failed: %s", exc)
            return None

    def get_status(self) -> dict[str, Any]:
        active_mv = self._registry.get_active()
        shadow_mv = self._registry.get_shadow()
        return {
            "active": self.active,
            "primary_version": active_mv.version if active_mv else None,
            "shadow_version": shadow_mv.version if shadow_mv else None,
            "samples_collected": self._total,
            "samples_target": self._sample_target,
            "progress_pct": round(100 * self._total / self._sample_target, 1) if self._sample_target else 0,
            "agreement_rate": round(self._agreements / self._total, 4) if self._total else None,
            "current_winner": self._winner,
            "history": self._history[-5:],
        }

    def promote_shadow_now(self) -> bool:
        """Manually promote shadow to active, bypassing sample target."""
        shadow = self._registry.get_shadow()
        if not shadow:
            return False
        self._registry.promote(shadow.version)
        logger.info("ABTester: shadow %s manually promoted to active", shadow.version)
        return True

    # ------------------------------------------------------------------ #
    #  Internal                                                            #
    # ------------------------------------------------------------------ #

    def _finalize(self) -> None:
        """Compare F1 scores after sample target reached and decide winner."""
        active = self._registry.get_active()
        shadow = self._registry.get_shadow()
        if not active or not shadow:
            self._winner = "primary"
            return

        delta = shadow.avg_f1 - active.avg_f1
        agreement_rate = self._agreements / self._total if self._total else 0
        promoted = False

        if delta >= MIN_PROMOTION_DELTA:
            self._winner = "shadow"
            if self._auto_promote:
                self._registry.promote(shadow.version)
                promoted = True
                logger.info(
                    "ABTester: shadow %s auto-promoted (F1 delta=+%.4f, agreement=%.1f%%)",
                    shadow.version, delta, 100 * agreement_rate,
                )
        else:
            self._winner = "primary"
            logger.info(
                "ABTester: primary wins (delta=%.4f < threshold %.2f, agreement=%.1f%%)",
                delta, MIN_PROMOTION_DELTA, 100 * agreement_rate,
            )

        session: dict[str, Any] = {
            "session_id": self._session_id,
            "completed_at": time.time(),
            "total_samples": self._total,
            "agreement_rate": round(agreement_rate, 4),
            "primary_version": active.version,
            "primary_avg_f1": active.avg_f1,
            "shadow_version": shadow.version,
            "shadow_avg_f1": shadow.avg_f1,
            "delta_f1": round(delta, 4),
            "winner": self._winner,
            "promoted": promoted,
        }
        self._history.append(session)
        if len(self._history) > 50:
            self._history = self._history[-50:]
        self._save()

        # Reset for next A/B session
        self._session_id = f"ab_{int(time.time())}"
        self._agreements = 0
        self._total = 0
        self._winner = None

    def _save(self) -> None:
        try:
            Path(AB_HISTORY_FILE).parent.mkdir(parents=True, exist_ok=True)
            Path(AB_HISTORY_FILE).write_text(json.dumps({"history": self._history}, indent=2))
        except Exception as exc:
            logger.warning("ABTester: save failed: %s", exc)

    def _load(self) -> None:
        p = Path(AB_HISTORY_FILE)
        if not p.exists():
            return
        try:
            data = json.loads(p.read_text())
            self._history = data.get("history", [])
        except Exception as exc:
            logger.warning("ABTester: load failed: %s", exc)
