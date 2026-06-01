"""Apply ML scenario override patch correctly."""
from pathlib import Path

INFERENCE_PATH = Path("/opt/pfe-ai-service/app/models/inference.py")
content = INFERENCE_PATH.read_text(encoding="utf-8")

if "_predict_scenario_ml" in content:
    print("Already patched, skipping")
    raise SystemExit(0)

# ── 1. Add _predict_scenario_ml method ───────────────────────────────────
NEW_METHOD = '''    def _predict_scenario_ml(self, features: dict[str, float]) -> tuple[str | None, float]:
        """Use ML scenario classifier (52 classes) to determine incident_type."""
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

        return str(bundle.scenario_labels[idx]).upper(), conf

'''

ANCHOR_METHOD = "    def _predict_incident_score(self, features: dict[str, float]) -> float | None:"
if ANCHOR_METHOD not in content:
    raise RuntimeError("Anchor _predict_incident_score not found")
content = content.replace(ANCHOR_METHOD, NEW_METHOD + ANCHOR_METHOD, 1)

# ── 2. Insert override block — properly indented ─────────────────────────
# Find the unique line "        summary = self._build_summary(incident, incident_type, severity, confidence)"
# and insert BEFORE it with a blank line separator.
ANCHOR_SUMMARY = "        summary = self._build_summary(incident, incident_type, severity, confidence)"
if ANCHOR_SUMMARY not in content:
    raise RuntimeError("Anchor summary line not found")
if content.count(ANCHOR_SUMMARY) != 1:
    raise RuntimeError(f"Anchor summary line appears {content.count(ANCHOR_SUMMARY)} times — expected exactly 1")

OVERRIDE = """        # ── ML Scenario Override (52-class XGBoost classifier) ──────────
        if incident:
            _ml_type, _ml_conf = self._predict_scenario_ml(safe_features)
            if _ml_type and _ml_type != "NO_INCIDENT":
                incident_type = _ml_type

"""

content = content.replace(ANCHOR_SUMMARY, OVERRIDE + ANCHOR_SUMMARY, 1)

INFERENCE_PATH.write_text(content, encoding="utf-8")
print(f"Patched correctly. File size: {INFERENCE_PATH.stat().st_size} bytes")

# Verify by reading back the patched section
patched = INFERENCE_PATH.read_text(encoding="utf-8")
lines = patched.splitlines()
for i, line in enumerate(lines):
    if "ML Scenario Override" in line:
        for offset in range(-2, 8):
            j = i + offset
            if 0 <= j < len(lines):
                print(f"  L{j+1}: {lines[j]}")
        break
