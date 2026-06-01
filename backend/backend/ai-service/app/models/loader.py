from dataclasses import dataclass
from pathlib import Path
from typing import Any

import joblib


@dataclass
class ModelBundle:
    incident_model: Any | None
    severity_model: Any | None
    action_model: Any | None
    feature_order: list[str] | None
    severity_labels: list[str] | None
    action_labels: list[str] | None

    @property
    def loaded(self) -> bool:
        return self.incident_model is not None


def load_model_bundle(model_path: str) -> ModelBundle:
    path = Path(model_path)
    if not path.exists():
        return ModelBundle(
            incident_model=None,
            severity_model=None,
            action_model=None,
            feature_order=None,
            severity_labels=None,
            action_labels=None,
        )

    artifact = joblib.load(path)
    if isinstance(artifact, dict):
        models = artifact.get("models")
        if isinstance(models, dict):
            return ModelBundle(
                incident_model=models.get("incident"),
                severity_model=models.get("severity"),
                action_model=models.get("action"),
                feature_order=artifact.get("feature_order"),
                severity_labels=artifact.get("severity_labels"),
                action_labels=artifact.get("action_labels"),
            )

        # Backward compatibility with older single-model artifacts.
        return ModelBundle(
            incident_model=artifact.get("model"),
            severity_model=None,
            action_model=None,
            feature_order=artifact.get("feature_order"),
            severity_labels=None,
            action_labels=None,
        )

    return ModelBundle(
        incident_model=artifact,
        severity_model=None,
        action_model=None,
        feature_order=None,
        severity_labels=None,
        action_labels=None,
    )
