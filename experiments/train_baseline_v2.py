"""
Train baseline v2 — adds a 4th classifier for incident_type (51 classes)
on top of the existing 3 (incident binary, severity, action).

The 4th classifier uses the `scenario` column from the dataset as label.
This lets the predictor output specific incident types like POD_OOM_KILLED
instead of falling back to LLM-driven family names.

Usage:
    python -m scripts.train_baseline_v2 \\
        --dataset datasets/aiops_training_dataset_v2.csv \\
        --output-model model_artifacts/model.joblib \\
        --output-metrics model_artifacts/model_metrics.json
"""
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
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="datasets/aiops_training_dataset_v2.csv")
    parser.add_argument("--output-model", default="model_artifacts/model.joblib")
    parser.add_argument("--output-metrics", default="model_artifacts/model_metrics.json")
    parser.add_argument("--test-size", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=42)
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
    prefixed = [c for c in frame.columns if c.startswith("feature_")]
    return sorted(prefixed)


def split_for_eval(x, y, test_size, seed):
    label_counts = y.value_counts()
    can_split = len(x) >= 10 and int(label_counts.min()) >= 2 and test_size > 0.0
    if can_split:
        xt, xv, yt, yv = train_test_split(x, y, test_size=test_size, random_state=seed, stratify=y)
        if yt.nunique() >= 2:
            return xt, xv, yt, yv, "holdout"
    return x, x, y, y, "full_dataset_no_holdout"


def train_incident_model(x, y, args):
    if y.nunique() < 2:
        raise ValueError("incident_label needs 2 classes")
    xt, xv, yt, yv, strat = split_for_eval(x, y, args.test_size, args.seed)
    model = XGBClassifier(
        n_estimators=220, max_depth=5, learning_rate=0.08,
        subsample=0.9, colsample_bytree=0.9,
        objective="binary:logistic", eval_metric="logloss",
        random_state=args.seed,
    )
    model.fit(xt, yt)
    yp = model.predict(xv)
    report = classification_report(yv, yp, target_names=["no_incident", "incident"],
                                    output_dict=True, zero_division=0)
    return model, {"trained": True, "dataset_rows": int(len(x)),
                   "train_rows": int(len(xt)), "test_rows": int(len(xv)),
                   "split_strategy": strat, "classification_report": report}


def train_multiclass_model(task_name, x_all, labels_raw, args, deeper=False):
    normalized = labels_raw.map(normalize_label)
    valid_mask = normalized.notna()
    if int(valid_mask.sum()) < 2:
        return None, [], {"trained": False, "reason": "not_enough_labeled_rows"}
    x = x_all.loc[valid_mask]
    labels = normalized.loc[valid_mask]
    if labels.nunique() < 2:
        return None, [str(labels.iloc[0])], {"trained": False, "reason": "single_class_only"}

    encoder = LabelEncoder()
    y_enc = pd.Series(encoder.fit_transform(labels), index=labels.index)
    xt, xv, yt, yv, strat = split_for_eval(x, y_enc, args.test_size, args.seed)

    # deeper config for the 51-class scenario classifier
    n_est, depth = (400, 7) if deeper else (220, 5)
    model = XGBClassifier(
        n_estimators=n_est, max_depth=depth, learning_rate=0.08,
        subsample=0.9, colsample_bytree=0.9,
        objective="multi:softprob", eval_metric="mlogloss",
        random_state=args.seed, num_class=len(encoder.classes_),
    )
    model.fit(xt, yt)
    yp = model.predict(xv)
    class_labels = [str(v) for v in encoder.classes_]
    report = classification_report(yv, yp, target_names=class_labels,
                                    output_dict=True, zero_division=0)
    return model, class_labels, {
        "trained": True, "task": task_name,
        "dataset_rows": int(len(x)), "train_rows": int(len(xt)),
        "test_rows": int(len(xv)), "split_strategy": strat,
        "class_count": int(len(class_labels)), "classes": class_labels,
        "classification_report": report,
    }


def main() -> None:
    args = parse_args()
    dataset_path = Path(args.dataset)
    if not dataset_path.exists():
        raise FileNotFoundError(f"Dataset not found: {dataset_path}")

    frame = pd.read_csv(dataset_path)
    if "incident_label" not in frame.columns:
        raise ValueError("Dataset must contain incident_label column")

    dataset = frame.copy()
    dataset["incident_label"] = dataset["incident_label"].map(parse_bool)
    dataset = dataset.dropna(subset=["incident_label"])

    feature_columns = resolve_feature_columns(dataset)
    if not feature_columns:
        raise ValueError("No feature_* columns found")

    x = dataset[feature_columns].fillna(0.0).astype(float)
    y_incident = dataset["incident_label"].astype(int)
    incident_model, incident_metrics = train_incident_model(x, y_incident, args)

    incident_only_mask = dataset["incident_label"] == True
    x_incident_only = x.loc[incident_only_mask]

    severity_model, severity_labels, severity_metrics = train_multiclass_model(
        "severity", x_incident_only,
        dataset.loc[incident_only_mask, "severity_label"], args)

    action_model, action_labels, action_metrics = train_multiclass_model(
        "action", x_incident_only,
        dataset.loc[incident_only_mask, "action_label"], args)

    # NEW: scenario classifier (51 classes) using the scenario column
    scenario_source = dataset.get("scenario", pd.Series(index=dataset.index, dtype=object))
    scenario_model, scenario_labels, scenario_metrics = train_multiclass_model(
        "scenario", x, scenario_source, args, deeper=True)

    metrics = {
        "dataset_rows": int(len(dataset)),
        "feature_count": int(len(feature_columns)),
        "incident": incident_metrics,
        "severity": severity_metrics,
        "action": action_metrics,
        "scenario": scenario_metrics,
    }

    artifact = {
        "models": {
            "incident": incident_model,
            "severity": severity_model,
            "action": action_model,
            "scenario": scenario_model,
        },
        "feature_order": feature_columns,
        "severity_labels": severity_labels,
        "action_labels": action_labels,
        "scenario_labels": scenario_labels,
        "metadata": {
            "trainer": "scripts/train_baseline_v2.py",
            "dataset": str(dataset_path),
            "seed": args.seed,
            "approach": "hybrid_xgboost_4classifiers",
            "class_count": len(scenario_labels),
        },
    }

    model_path = Path(args.output_model)
    metrics_path = Path(args.output_metrics)
    model_path.parent.mkdir(parents=True, exist_ok=True)
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(artifact, model_path)
    metrics_path.write_text(json.dumps(metrics, indent=2), encoding="utf-8")

    print(f"Model saved: {model_path}")
    print(f"Metrics saved: {metrics_path}")
    print(f"Incident macro F1: {incident_metrics['classification_report']['macro avg']['f1-score']:.4f}")
    print(f"Severity macro F1: {severity_metrics['classification_report']['macro avg']['f1-score']:.4f}")
    print(f"Action macro F1:   {action_metrics['classification_report']['macro avg']['f1-score']:.4f}")
    print(f"Scenario macro F1: {scenario_metrics['classification_report']['macro avg']['f1-score']:.4f}")
    print(f"Scenario classes:  {len(scenario_labels)}")


if __name__ == "__main__":
    main()
