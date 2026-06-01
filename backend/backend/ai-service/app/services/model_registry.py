"""
Lightweight model registry — MLflow Model Registry equivalent.

Stores versioned XGBoost model bundles in model_artifacts/registry/
with a JSON manifest tracking metrics, status, and promotion history.

Supports: register, promote, rollback, list, compare.
"""
from __future__ import annotations

import json
import logging
import shutil
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import joblib

logger = logging.getLogger(__name__)


@dataclass
class ModelVersion:
    version: str
    model_file: str
    f1_incident: float
    f1_action: float
    f1_severity: float
    training_size: int
    feature_count: int
    created_at: float
    status: str          # "active" | "shadow" | "archived"
    notes: str = ""

    @property
    def avg_f1(self) -> float:
        return round((self.f1_incident + self.f1_action + self.f1_severity) / 3, 4)


class ModelRegistry:
    """
    Local model registry. On startup, bootstraps v1 from existing model.joblib
    so the registry is never empty.
    """

    def __init__(
        self,
        registry_file: str = "model_artifacts/model_registry.json",
        registry_dir: str = "model_artifacts/registry",
    ) -> None:
        self._file = Path(registry_file)
        self._dir = Path(registry_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._versions: dict[str, ModelVersion] = {}
        self._active: str | None = None
        self._shadow: str | None = None
        self._load()

    @property
    def active_version(self) -> str | None:
        return self._active

    @property
    def shadow_version(self) -> str | None:
        return self._shadow

    # ------------------------------------------------------------------ #
    #  Public API                                                          #
    # ------------------------------------------------------------------ #

    def register(
        self,
        model_bundle: Any,
        metrics: dict[str, float],
        training_size: int,
        feature_count: int = 0,
        notes: str = "",
        set_as_shadow: bool = True,
    ) -> ModelVersion:
        """Register a newly trained model. Returns the new ModelVersion."""
        next_n = len(self._versions) + 1
        vid = f"v{next_n}"
        dest = str(self._dir / f"model_{vid}.joblib")
        joblib.dump(model_bundle, dest)

        is_first = self._active is None
        mv = ModelVersion(
            version=vid,
            model_file=dest,
            f1_incident=round(metrics.get("f1_incident", 0.0), 4),
            f1_action=round(metrics.get("f1_action", 0.0), 4),
            f1_severity=round(metrics.get("f1_severity", 0.0), 4),
            training_size=training_size,
            feature_count=feature_count,
            created_at=time.time(),
            status="active" if is_first else ("shadow" if set_as_shadow else "archived"),
            notes=notes,
        )
        self._versions[vid] = mv

        if is_first:
            self._active = vid
        elif set_as_shadow:
            self._shadow = vid

        self._save()
        logger.info(
            "ModelRegistry: registered %s (F1 incident=%.3f action=%.3f severity=%.3f size=%d)",
            vid, mv.f1_incident, mv.f1_action, mv.f1_severity, training_size,
        )
        return mv

    def promote(self, version_id: str) -> bool:
        """Promote version_id to active, archive current active."""
        if version_id not in self._versions:
            return False
        if self._active and self._active != version_id:
            self._versions[self._active].status = "archived"
        self._versions[version_id].status = "active"
        self._active = version_id
        if self._shadow == version_id:
            self._shadow = None
        # Copy promoted model to canonical path
        src = Path(self._versions[version_id].model_file)
        if src.exists():
            shutil.copy2(str(src), "model_artifacts/model.joblib")
        self._save()
        logger.info("ModelRegistry: promoted %s → active", version_id)
        return True

    def rollback(self) -> str | None:
        """Rollback to the most recently archived version."""
        archived = [v for v in self._versions.values() if v.status == "archived"]
        if not archived:
            return None
        prev = max(archived, key=lambda v: v.created_at)
        self.promote(prev.version)
        return prev.version

    def compare(self, v1_id: str, v2_id: str) -> dict[str, Any] | None:
        v1 = self._versions.get(v1_id)
        v2 = self._versions.get(v2_id)
        if not v1 or not v2:
            return None
        return {
            "v1": {"version": v1.version, "avg_f1": v1.avg_f1, "size": v1.training_size},
            "v2": {"version": v2.version, "avg_f1": v2.avg_f1, "size": v2.training_size},
            "winner": v2.version if v2.avg_f1 > v1.avg_f1 else v1.version,
            "delta_f1": round(v2.avg_f1 - v1.avg_f1, 4),
        }

    def get_active(self) -> ModelVersion | None:
        return self._versions.get(self._active) if self._active else None

    def get_shadow(self) -> ModelVersion | None:
        return self._versions.get(self._shadow) if self._shadow else None

    def load_shadow_bundle(self) -> Any | None:
        mv = self.get_shadow()
        if not mv:
            return None
        p = Path(mv.model_file)
        return joblib.load(str(p)) if p.exists() else None

    def list_versions(self) -> list[dict[str, Any]]:
        return [
            {**asdict(v), "avg_f1": v.avg_f1}
            for v in sorted(self._versions.values(), key=lambda x: x.created_at)
        ]

    # ------------------------------------------------------------------ #
    #  Persistence                                                         #
    # ------------------------------------------------------------------ #

    def _save(self) -> None:
        try:
            state = {
                "active_version": self._active,
                "shadow_version": self._shadow,
                "versions": {k: asdict(v) for k, v in self._versions.items()},
            }
            self._file.parent.mkdir(parents=True, exist_ok=True)
            self._file.write_text(json.dumps(state, indent=2))
        except Exception as exc:
            logger.warning("ModelRegistry save failed: %s", exc)

    def _load(self) -> None:
        if not self._file.exists():
            self._bootstrap_from_existing()
            return
        try:
            state = json.loads(self._file.read_text())
            self._active = state.get("active_version")
            self._shadow = state.get("shadow_version")
            for k, v in state.get("versions", {}).items():
                v.pop("avg_f1", None)
                self._versions[k] = ModelVersion(**v)
            logger.info(
                "ModelRegistry: loaded %d version(s), active=%s",
                len(self._versions), self._active,
            )
        except Exception as exc:
            logger.warning("ModelRegistry load failed: %s — bootstrapping", exc)
            self._bootstrap_from_existing()

    def _bootstrap_from_existing(self) -> None:
        """Register existing model.joblib as v1 when no registry file found."""
        existing = Path("model_artifacts/model.joblib")
        if not existing.exists():
            return
        metrics_file = Path("model_artifacts/model_metrics.json")
        metrics: dict[str, Any] = {}
        if metrics_file.exists():
            try:
                metrics = json.loads(metrics_file.read_text())
            except Exception:
                pass

        dest = str(self._dir / "model_v1.joblib")
        try:
            shutil.copy2(str(existing), dest)
        except Exception:
            dest = str(existing)

        mv = ModelVersion(
            version="v1",
            model_file=dest,
            f1_incident=float(metrics.get("f1_incident", metrics.get("f1_score", 0.0))),
            f1_action=float(metrics.get("f1_action", 0.0)),
            f1_severity=float(metrics.get("f1_severity", 0.0)),
            training_size=int(metrics.get("training_size", 0)),
            feature_count=int(metrics.get("feature_count", 0)),
            created_at=time.time(),
            status="active",
            notes="bootstrapped from existing model.joblib",
        )
        self._versions["v1"] = mv
        self._active = "v1"
        self._save()
        logger.info("ModelRegistry: bootstrapped v1 from model.joblib")
