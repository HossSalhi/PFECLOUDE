"""Patch v3 — ML scenario classifier promotes incident=True too."""
from pathlib import Path

INFERENCE_PATH = Path("/opt/pfe-ai-service/app/models/inference.py")
BAK = Path("/opt/pfe-ai-service/app/models/inference.py.bak")

# Start fresh from backup
INFERENCE_PATH.write_text(BAK.read_text(encoding="utf-8"), encoding="utf-8")
content = INFERENCE_PATH.read_text(encoding="utf-8")

# 1. Add _predict_scenario_ml method
NEW_METHOD = '''    def _predict_scenario_ml(self, features: dict[str, float]) -> tuple[str | None, float]:
        """Use ML scenario classifier (52 classes) to determine incident_type."""
        bundle = self.model_bundle
        if bundle.scenario_model is None or not bundle.scenario_labels:
            return None, 0.0

        vector = self._to_vector(features)
        try:
            if hasattr(bundle.scenario_model, "predict_proba"):
                probs = bundle.scenario_model.predict_proba(vector)
                idx = int(probs.argmax(axis=1)[0])
                conf = float(probs[0][idx])
            else:
                pred = bundle.scenario_model.predict(vector)
                idx = int(pred[0])
                conf = 1.0
        except Exception:
            return None, 0.0

        if idx < 0 or idx >= len(bundle.scenario_labels):
            return None, 0.0
        if conf < 0.30:
            return None, conf

        return str(bundle.scenario_labels[idx]).upper(), conf

'''

ANCHOR = "    def _predict_incident_score(self, features: dict[str, float]) -> float | None:"
content = content.replace(ANCHOR, NEW_METHOD + ANCHOR, 1)

# 2. Insert override block — runs even if incident=False, can promote to True
OVERRIDE = """        # ── ML Scenario Classifier Override (52 classes) ────────────────
        # The dedicated scenario classifier is the authoritative source for
        # incident_type. It can also promote incident=True if very confident,
        # bypassing the conservative rule-based incident detection.
        _ml_type, _ml_conf = self._predict_scenario_ml(safe_features)
        if _ml_type and _ml_type != "NO_INCIDENT":
            incident_type = _ml_type
            if not incident and _ml_conf >= 0.50:
                incident = True
                noise = False
                if _ml_conf > confidence:
                    confidence = _ml_conf
                # Re-derive severity + action consistent with ML type
                severity = self._predict_severity(safe_features, incident, confidence, incident_type)
                ranked_plans = self._build_ranked_plans(
                    safe_features, incident_type, severity, confidence, incident, remediation_context
                )
                if ranked_plans:
                    selected_index = self._select_plan_index(ranked_plans, remediation_context, incident_type)
                    if selected_index > 0:
                        chosen_plan = ranked_plans[selected_index]
                        ranked_plans = [chosen_plan] + [p for i, p in enumerate(ranked_plans) if i != selected_index]
                    else:
                        chosen_plan = ranked_plans[0]
                    action = chosen_plan.recommended_action
                    final_executable_action = (
                        chosen_plan.executable_action if chosen_plan.execution_mode == "AUTO_EXECUTE_CANDIDATE" else None
                    )
                    execution_mode = chosen_plan.execution_mode
                    recommendation_only_reason = (
                        None if execution_mode == "AUTO_EXECUTE_CANDIDATE"
                        else self._build_recommendation_only_reason(chosen_plan, confidence, severity)
                    )
                    chosen_plan_id = chosen_plan.plan_id
                    decision_explanation = self._build_decision_explanation(chosen_plan, ranked_plans, remediation_context)

"""

ANCHOR_SUMMARY = "        summary = self._build_summary(incident, incident_type, severity, confidence)"
if content.count(ANCHOR_SUMMARY) != 1:
    raise RuntimeError("anchor not unique")
content = content.replace(ANCHOR_SUMMARY, OVERRIDE + ANCHOR_SUMMARY, 1)

INFERENCE_PATH.write_text(content, encoding="utf-8")
print(f"Patched v3. Size: {INFERENCE_PATH.stat().st_size} bytes")

# Verify by syntax check
import py_compile
try:
    py_compile.compile(str(INFERENCE_PATH), doraise=True)
    print("Syntax OK")
except py_compile.PyCompileError as e:
    print(f"SYNTAX ERROR: {e}")
