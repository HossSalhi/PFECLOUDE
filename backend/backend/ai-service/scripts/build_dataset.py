from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from app.core.config import get_settings
from app.models.schemas import PredictRequest
from app.services.predictor import PredictorService


@dataclass
class EventRecord:
    event_id: str
    scenario: str | None
    namespace: str
    app: str
    window_minutes: int | None
    end_time: datetime | None
    incident_label: bool | None
    incident_type_label: str | None
    severity_label: str | None
    action_label: str | None
    notes: str | None


TRUTHY = {"1", "true", "yes", "y", "on"}
FALSY = {"0", "false", "no", "n", "off"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a labeled feature dataset by replaying fault-injection event windows."
    )
    parser.add_argument(
        "--events",
        default="datasets/fault_injection_events.example.csv",
        help="Input events file (.csv, .json, or .jsonl).",
    )
    parser.add_argument(
        "--output",
        default="datasets/aiops_training_dataset.csv",
        help="Output CSV path.",
    )
    return parser.parse_args()


def parse_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if pd.isna(value):
            return None
        return bool(int(value))

    raw = str(value).strip().lower()
    if not raw:
        return None
    if raw in TRUTHY:
        return True
    if raw in FALSY:
        return False
    return None


def parse_iso_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    raw = str(value).strip()
    if not raw:
        return None
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


def load_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"Events file not found: {path}")

    suffix = path.suffix.lower()
    if suffix == ".csv":
        frame = pd.read_csv(path)
        return frame.to_dict(orient="records")
    if suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            return payload
        raise ValueError("JSON input must be a list of event objects.")
    if suffix == ".jsonl":
        rows: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rows.append(json.loads(line))
        return rows

    raise ValueError("Unsupported events format. Use .csv, .json, or .jsonl")


def to_event(record: dict[str, Any], row_index: int) -> EventRecord:
    namespace = str(record.get("namespace", "")).strip()
    app = str(record.get("app", "")).strip()
    if not namespace or not app:
        raise ValueError(
            f"Row {row_index}: both 'namespace' and 'app' are required."
        )

    window_raw = record.get("window_minutes")
    if window_raw is None or (isinstance(window_raw, str) and not window_raw.strip()) or pd.isna(window_raw):
        window_minutes = None
    else:
        window_minutes = int(window_raw)

    return EventRecord(
        event_id=str(record.get("event_id") or f"event-{row_index}"),
        scenario=str(record.get("scenario")).strip() if record.get("scenario") is not None else None,
        namespace=namespace,
        app=app,
        window_minutes=window_minutes,
        end_time=parse_iso_datetime(record.get("end_time")),
        incident_label=parse_bool(record.get("incident")),
        incident_type_label=(
            str(record.get("incident_type")).strip()
            if record.get("incident_type") is not None
            else None
        ),
        severity_label=(
            str(record.get("severity")).strip()
            if record.get("severity") is not None
            else None
        ),
        action_label=(
            str(record.get("recommended_action")).strip()
            if record.get("recommended_action") is not None
            else None
        ),
        notes=str(record.get("notes")).strip() if record.get("notes") is not None else None,
    )


def event_to_training_row(
    event: EventRecord,
    predictor: PredictorService,
) -> dict[str, Any]:
    request = PredictRequest(
        namespace=event.namespace,
        app=event.app,
        window_minutes=event.window_minutes,
        end_time=event.end_time,
    )
    prediction = predictor.predict(request)

    row: dict[str, Any] = {
        "event_id": event.event_id,
        "scenario": event.scenario,
        "namespace": event.namespace,
        "app": event.app,
        "window_minutes": request.window_minutes,
        "end_time": event.end_time.isoformat() if event.end_time else None,
        "incident_label": event.incident_label,
        "incident_type_label": event.incident_type_label,
        "severity_label": event.severity_label,
        "action_label": event.action_label,
        "notes": event.notes,
        "pred_incident": prediction.incident,
        "pred_incident_type": prediction.incident_type,
        "pred_severity": prediction.severity,
        "pred_action": prediction.recommended_action,
        "pred_confidence": prediction.confidence,
        "evidence_log_score": prediction.evidence.log_score,
        "evidence_metric_score": prediction.evidence.metric_score,
        "evidence_rule_score": prediction.evidence.rule_score,
        "evidence_model_score": prediction.evidence.model_score,
        "window_start": prediction.window_start.isoformat(),
        "window_end": prediction.window_end.isoformat(),
    }

    for feature_name, value in prediction.features.items():
        row[f"feature_{feature_name}"] = float(value)

    return row


def main() -> None:
    args = parse_args()
    events_path = Path(args.events)
    output_path = Path(args.output)

    raw_events = load_events(events_path)
    events = [to_event(record, idx + 1) for idx, record in enumerate(raw_events)]
    if not events:
        raise ValueError("No events found in input file.")

    settings = get_settings()
    predictor = PredictorService(settings)

    rows = [event_to_training_row(event, predictor) for event in events]
    frame = pd.DataFrame(rows)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output_path, index=False)

    print(f"Built dataset: {output_path} ({len(frame)} rows, {len(frame.columns)} columns)")
    if "incident_label" in frame.columns:
        label_counts = frame["incident_label"].value_counts(dropna=False).to_dict()
        print(f"incident_label distribution: {label_counts}")


if __name__ == "__main__":
    main()
