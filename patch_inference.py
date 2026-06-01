"""
Patch script: modify /opt/pfe-ai-service/app/models/inference.py
to use the ML scenario classifier (4th model in v3 artifact) as the
authoritative source for incident_type when available.

This makes the XGBoost scenario classifier (52 classes) override the
LLM-driven or rule-based classification, giving fine-grained types
like POD_OOM_KILLED, DB_CONNECTION_POOL_EXHAUSTION, etc.
"""
from pathlib import Path
import re

INFERENCE_PATH = Path("/opt/pfe-ai-service/app/models/inference.py")

content = INFERENCE_PATH.read_text(encoding="utf-8")

# Idempotence guard
if "_predict_scenario_ml" in content:
    print("Already patched, skipping")
    raise SystemExit(0)

# ============================================================
# 1. Add _predict_scenario_ml method right before _predict_incident_score
# ============================================================
NEW_METHOD = '''
    def _predict_scenario_ml(self, features: dict[str, float]) -> tuple[str | None, float]:
        """
        Use the ML scenario classifier (52 classes) to determine incident_type.
        Returns (class_name, confidence). class_name is None if model not available
        or confidence is too low (< 0.20) to be trustworthy.
        """
        bundle = self.model_bundle
        if bundle.scenario_model is None or not bundle.scenario_labels:
            return None, 0.0

        vector = self._to_vector(features)
        model = bundle.scenario_model

        try:
            if hasattr(model, "predict_proba"):
                probs = model.predict_proba(vector)
                idx = int(probs.argmax(axis=1)[0])
                conf = float(probs[0][idx])
            else:
                pred = model.predict(vector)
                idx = int(pred[0])
                conf = 1.0
        except Exception:
            return None, 0.0

        if idx < 0 or idx >= len(bundle.scenario_labels):
            return None, 0.0
        if conf < 0.20:
            return None, conf

        label = str(bundle.scenario_labels[idx]).upper()
        return label, conf

'''

ANCHOR_PREDICT_INCIDENT = "    def _predict_incident_score(self, features: dict[str, float]) -> float | None:"
content = content.replace(ANCHOR_PREDICT_INCIDENT, NEW_METHOD + ANCHOR_PREDICT_INCIDENT, 1)

# ============================================================
# 2. Add ML override block right before "summary = self._build_summary(incident, incident_type, severity, confidence)"
# ============================================================
OVERRIDE_BLOCK = '''        # ── ML Scenario Override (52-class XGBoost classifier) ──────────
        # If the dedicated scenario classifier is available and confident,
        # override the LLM/rule-based incident_type with the ML prediction.
        # The ML classifier knows the full taxonomy (51 incident types +
        # NO_INCIDENT) whereas the LLM prompt is limited to a few families.
        if incident:
            ml_type, ml_conf = self._predict_scenario_ml(safe_features)
            if ml_type and ml_type != "NO_INCIDENT":
                incident_type = ml_type

        '''

ANCHOR_SUMMARY = "        summary = self._build_summary(incident, incident_type, severity, confidence)"
content = content.replace(ANCHOR_SUMMARY, OVERRIDE_BLOCK + ANCHOR_SUMMARY, 1)

INFERENCE_PATH.write_text(content, encoding="utf-8")
print(f"Patched {INFERENCE_PATH}")
print(f"File size: {INFERENCE_PATH.stat().st_size} bytes")
