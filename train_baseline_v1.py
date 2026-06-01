from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import joblib
import pandas as pd
from sklearn.metrics import classification_report
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import LabelEncoder
from xgboost import XGBClassifier


TRUTHY = {"1", "true", "yes", "y", "on"}
FALSY = {"0", "false", "no", "n", "off"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Train the lightweight hybrid AIOps baseline "
            "(incident + severity + action classifiers)."
        )
    )
    parser.add_argument(
        "--dataset",
        default="datasets/aiops_training_dataset.csv",
        help="CSV built by scripts/build_dataset.py",
    )
    parser.add_argument(
        "--output-model",
        default="model_artifacts/model.joblib",
        help="Output model artifact path consumed by app.models.loader",
    )
    parser.add_argument(
        "--output-metrics",
        default="model_artifacts/model_metrics.json",
        help="Output evaluation metrics (JSON).",
    )
    parser.add_argument(
        "--test-size",
        type=float,
        default=0.2,
        help="Holdout ratio for evaluation.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed.",
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


def normalize_label(value: Any) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    if not text:
        return None
    return text.upper()


def resolve_feature_columns(frame: pd.DataFrame) -> list[str]:
    prefixed = [column for column in frame.columns if column.startswith("feature_")]
    if prefixed:
        return sorted(prefixed)

    ignore = {
        "event_id",
        "scenario",
        "namespace",
        "app",
        "window_minutes",
        "end_time",
        "incident_label",
        "incident_type_label",
        "severity_label",
        "action_label",
        "notes",
        "pred_incident",
        "pred_incident_type",
        "pred_severity",
        "pred_action",
        "pred_confidence",
        "evidence_log_score",
        "evidence_metric_score",
        "evidence_rule_score",
        "evidence_model_score",
        "window_start",
        "window_end",
    }
    numeric_columns = [
        column
        for column in frame.columns
        if column not in ignore and pd.api.types.is_numeric_dtype(frame[column])
    ]
    return sorted(numeric_columns)


def split_for_eval(
    x: pd.DataFrame,
    y: pd.Series,
    test_size: float,
    seed: int,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.Series, pd.Series, str]:
    label_counts = y.value_counts()
    can_split = len(x) >= 10 and int(label_counts.min()) >= 2 and test_size > 0.0
    if can_split:
        x_train, x_test, y_train, y_test = train_test_split(
            x,
            y,
            test_size=test_size,
            random_state=seed,
            stratify=y,
        )
        if y_train.nunique() >= 2:
            return x_train, x_test, y_train, y_test, "holdout"

    return x, x, y, y, "full_dataset_no_holdout"


def train_incident_model(
    x: pd.DataFrame,
    y: pd.Series,
    args: argparse.Namespace,
) -> tuple[XGBClassifier, dict[str, Any]]:
    if y.nunique() < 2:
        raise ValueError(
            "Training requires at least two classes in incident_label (true/false)."
        )

    x_train, x_test, y_train, y_test, split_strategy = split_for_eval(
        x,
        y,
        args.test_size,
        args.seed,
    )

    model = XGBClassifier(
        n_estimators=220,
        max_depth=5,
        learning_rate=0.08,
        subsample=0.9,
        colsample_bytree=0.9,
        objective="binary:logistic",
        eval_metric="logloss",
        random_state=args.seed,
    )
    model.fit(x_train, y_train)

    y_pred = model.predict(x_test)
    report = classification_report(
        y_test,
        y_pred,
        target_names=["no_incident", "incident"],
        output_dict=True,
        zero_division=0,
    )
    metrics = {
        "trained": True,
        "dataset_rows": int(len(x)),
        "train_rows": int(len(x_train)),
        "test_rows": int(len(x_test)),
        "split_strategy": split_strategy,
        "classification_report": report,
    }
    return model, metrics


def train_multiclass_model(
    task_name: str,
    x_all: pd.DataFrame,
    labels_raw: pd.Series,
    args: argparse.Namespace,
) -> tuple[XGBClassifier | None, list[str], dict[str, Any]]:
    normalized = labels_raw.map(normalize_label)
    valid_mask = normalized.notna()
    labeled_rows = int(valid_mask.sum())
    if labeled_rows < 2:
        return None, [], {
            "trained": False,
            "reason": "not_enough_labeled_rows",
            "labeled_rows": labeled_rows,
        }

    x = x_all.loc[valid_mask]
    labels = normalized.loc[valid_mask]
    if labels.nunique() < 2:
        return None, [str(labels.iloc[0])], {
            "trained": False,
            "reason": "single_class_only",
            "labeled_rows": labeled_rows,
        }

    encoder = LabelEncoder()
    y_encoded = pd.Series(encoder.fit_transform(labels), index=labels.index)

    x_train, x_test, y_train, y_test, split_strategy = split_for_eval(
        x,
        y_encoded,
        args.test_size,
        args.seed,
    )

    model = XGBClassifier(
        n_estimators=220,
        max_depth=5,
        learning_rate=0.08,
        subsample=0.9,
        colsample_bytree=0.9,
        objective="multi:softmax",
        eval_metric="mlogloss",
        random_state=args.seed,
        num_class=len(encoder.classes_),
    )
    model.fit(x_train, y_train)

    y_pred = model.predict(x_test)
    class_labels = [str(value) for value in encoder.classes_]
    report = classification_report(
        y_test,
        y_pred,
        target_names=class_labels,
        output_dict=True,
        zero_division=0,
    )
    metrics = {
        "trained": True,
        "task": task_name,
        "dataset_rows": int(len(x)),
        "train_rows": int(len(x_train)),
        "test_rows": int(len(x_test)),
        "split_strategy": split_strategy,
        "class_count": int(len(class_labels)),
        "classes": class_labels,
        "classification_report": report,
    }
    return model, class_labels, metrics


def main() -> None:
    args = parse_args()
    dataset_path = Path(args.dataset)
    if not dataset_path.exists():
        raise FileNotFoundError(f"Dataset not found: {dataset_path}")

    frame = pd.read_csv(dataset_path)
    if "incident_label" not in frame.columns:
        raise ValueError("Dataset must contain an 'incident_label' column.")

    dataset = frame.copy()
    dataset["incident_label"] = dataset["incident_label"].map(parse_bool)
    dataset = dataset.dropna(subset=["incident_label"])
    if dataset.empty:
        raise ValueError("No usable rows after filtering missing incident_label values.")

    feature_columns = resolve_feature_columns(dataset)
    if not feature_columns:
        raise ValueError("No numeric feature columns found for training.")

    x = dataset[feature_columns].fillna(0.0).astype(float)
    y_incident = dataset["incident_label"].astype(int)
    incident_model, incident_metrics = train_incident_model(x, y_incident, args)

    incident_only_mask = dataset["incident_label"] == True
    x_incident_only = x.loc[incident_only_mask]
    severity_raw = (
        dataset["severity_label"]
        if "severity_label" in dataset.columns
        else pd.Series(index=dataset.index, dtype=object)
    )
    action_raw = (
        dataset["action_label"]
        if "action_label" in dataset.columns
        else pd.Series(index=dataset.index, dtype=object)
    )

    severity_model, severity_labels, severity_metrics = train_multiclass_model(
        task_name="severity",
        x_all=x_incident_only,
        labels_raw=severity_raw.loc[incident_only_mask],
        args=args,
    )
    action_model, action_labels, action_metrics = train_multiclass_model(
        task_name="action",
        x_all=x_incident_only,
        labels_raw=action_raw.loc[incident_only_mask],
        args=args,
    )

    metrics = {
        "dataset_rows": int(len(dataset)),
        "feature_count": int(len(feature_columns)),
        "incident": incident_metrics,
        "severity": severity_metrics,
        "action": action_metrics,
    }

    model_path = Path(args.output_model)
    metrics_path = Path(args.output_metrics)
    model_path.parent.mkdir(parents=True, exist_ok=True)
    metrics_path.parent.mkdir(parents=True, exist_ok=True)

    artifact = {
        "models": {
            "incident": incident_model,
            "severity": severity_model,
            "action": action_model,
        },
        "feature_order": feature_columns,
        "severity_labels": severity_labels,
        "action_labels": action_labels,
        "metadata": {
            "trainer": "scripts/train_baseline.py",
            "dataset": str(dataset_path),
            "seed": args.seed,
            "approach": "hybrid_miniLM_metrics_xgboost",
        },
    }
    joblib.dump(artifact, model_path)
    metrics_path.write_text(json.dumps(metrics, indent=2), encoding="utf-8")

    print(f"Model saved: {model_path}")
    print(f"Metrics saved: {metrics_path}")
    print(
        "Incident macro F1:",
        round(
            incident_metrics["classification_report"]["macro avg"]["f1-score"],
            4,
        ),
    )


if __name__ == "__main__":
    main()
