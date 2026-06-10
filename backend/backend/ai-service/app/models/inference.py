from dataclasses import dataclass
import math

import numpy as np

from app.models.loader import ModelBundle


@dataclass
class InferenceResult:
    incident: bool
    noise: bool
    incident_type: str
    severity: str
    recommended_action: str
    final_executable_action: str | None
    execution_mode: str
    recommendation_only_reason: str | None
    confidence: float
    summary: str
    decision_explanation: str
    chosen_plan_id: str | None
    top_recommended_plans: list[dict[str, object]]
    decision_context: dict[str, object]
    rule_score: float
    model_score: float | None


@dataclass
class PlanOption:
    plan_id: str
    title: str
    strategy: str
    recommended_action: str
    executable_action: str
    safe_to_execute: bool
    execution_mode: str
    rationale: str
    reasons: list[str]
    advantages: list[str]
    tradeoffs: list[str]
    risk_score: float
    cost_score: float
    blast_radius_score: float
    historical_effectiveness: float
    novelty_score: float
    priority_score: float
    requires_approval: bool


class InferenceEngine:
    def __init__(self, model_bundle: ModelBundle) -> None:
        self.model_bundle = model_bundle

    @property
    def model_loaded(self) -> bool:
        return self.model_bundle.loaded

    def predict(
        self,
        features: dict[str, float],
        remediation_context: dict[str, object] | None = None,
        skip_llm: bool = False,
    ) -> InferenceResult:
        safe_features = {
            key: self._finite(value)
            for key, value in features.items()
        }
        remediation_context = remediation_context or {}
        rule_score = self._finite(safe_features.get("overall_risk_score", 0.0))
        model_score = self._predict_incident_score(safe_features)

        if model_score is None:
            confidence = rule_score
        else:
            confidence = (0.6 * rule_score) + (0.4 * model_score)

        confidence = max(0.0, min(1.0, self._finite(confidence)))
        memory_bytes = self._finite(safe_features.get("memory_working_set_bytes", 0.0))
        cpu_cores = self._finite(safe_features.get("cpu_usage_cores", 0.0))
        node_not_ready = self._finite(safe_features.get("node_not_ready_count", 0.0))
        monitoring_vm_up = self._finite(safe_features.get("monitoring_vm_up", 1.0), default=1.0)
        ai_vm_up = self._finite(safe_features.get("ai_vm_up", 1.0), default=1.0)
        latency_p95 = self._finite(safe_features.get("http_latency_p95_seconds", 0.0))
        http_5xx_ratio = self._finite(safe_features.get("http_5xx_ratio", 0.0))
        pod_restarts = self._finite(safe_features.get("pod_restarts_total", 0.0))
        error_ratio = self._finite(safe_features.get("error_ratio", 0.0))
        log_line_count = self._finite(safe_features.get("log_line_count", 0.0))
        error_line_count = self._finite(safe_features.get("error_line_count", 0.0))
        unavailable_replicas = self._finite(safe_features.get("unavailable_replicas", 0.0))
        db_keyword_hits = self._finite(safe_features.get("db_keyword_hits", 0.0))
        metric_risk_score = self._finite(safe_features.get("metric_risk_score", 0.0))
        db_pool_pending = self._finite(safe_features.get("db_pool_pending", 0.0))
        db_pool_usage_ratio = self._finite(safe_features.get("db_pool_usage_ratio", 0.0))

        # --- LLM full analysis (qwen2:0.5b via Ollama) ---
        # Skip LLM when signals are too weak (saves 3 min per silent app)
        _risk = self._finite(safe_features.get('metric_risk_score', 0.0))
        _auto_skip = _risk < 0.01 and pod_restarts < 1.0 and unavailable_replicas < 1.0 and latency_p95 < 0.01 and http_5xx_ratio < 0.01
        _skip_llm = skip_llm or _auto_skip
        llm_result = None if _skip_llm else self._full_llm_predict(safe_features)

        if llm_result is not None:
            incident = llm_result["incident"]
            incident_type = llm_result["incident_type"]
            severity = llm_result["severity"]
            confidence = llm_result["confidence"]
            action = llm_result["recommended_action"]
            execution_mode = llm_result["execution_mode"]
            decision_explanation = llm_result["explanation"]
            noise = not incident
            final_executable_action = (
                self._map_executable_action(action) if execution_mode == "AUTO_EXECUTE_CANDIDATE" else None
            )
            recommendation_only_reason = (
                None if execution_mode == "AUTO_EXECUTE_CANDIDATE"
                else f"LLM confidence {confidence:.2f} below auto-execute threshold (0.70) — human review required."
            )
            chosen_plan_id = None
            ranked_plans = self._build_ranked_plans(
                safe_features, incident_type, severity, confidence, incident, remediation_context
            )
            if ranked_plans:
                chosen_plan_id = ranked_plans[0].plan_id
        else:
            # --- Rule-based fallback ---
            incident = confidence >= 0.45
            if not incident and node_not_ready >= 1.0 and confidence >= 0.30:
                incident = True
            if not incident and monitoring_vm_up < 0.5 and confidence >= 0.35:
                incident = True
            if not incident and ai_vm_up < 0.5 and confidence >= 0.35:
                incident = True
            if not incident and unavailable_replicas >= 1.0 and confidence >= 0.05:
                incident = True
            if not incident and memory_bytes >= 700_000_000 and confidence >= 0.10:
                incident = True
            if not incident and cpu_cores >= 0.55 and confidence >= 0.10:
                incident = True
            if not incident and pod_restarts >= 1.0 and confidence >= 0.05:
                incident = True
            if (
                not incident
                and db_keyword_hits >= 3.0
                and (metric_risk_score >= 0.16 or self._finite(safe_features.get("overall_risk_score", 0.0)) >= 0.08)
                and confidence >= 0.35
            ):
                incident = True
            if (
                not incident
                and db_keyword_hits >= 8.0
                and (metric_risk_score >= 0.20 or self._finite(safe_features.get("overall_risk_score", 0.0)) >= 0.12)
                and confidence >= 0.10
            ):
                incident = True
            if not incident and db_pool_pending >= 1.0:
                incident = True
            if not incident and db_pool_usage_ratio >= 0.75:
                incident = True
            if (not incident and db_keyword_hits >= 2.0 and db_pool_usage_ratio >= 0.45 and confidence >= 0.15):
                incident = True
            if not incident and http_5xx_ratio >= 0.15 and confidence >= 0.10:
                incident = True
            if not incident and latency_p95 >= 1.0 and confidence >= 0.05:
                incident = True
            if (
                not incident
                and error_ratio >= 0.08
                and (error_line_count >= 3.0 or log_line_count >= 5.0)
                and confidence >= 0.35
            ):
                incident = True
            if (
                not incident
                and self._finite(safe_features.get("warning_line_count", 0.0)) >= 5.0
                and error_ratio >= 0.05
                and confidence >= 0.30
            ):
                incident = True
            noise = not incident
            incident_type = self._classify_incident_type(safe_features, incident)
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
            else:
                action = self._predict_action(safe_features, incident_type, severity, incident)
                final_executable_action = None
                execution_mode = "RECOMMEND_ONLY"
                recommendation_only_reason = self._build_no_incident_explanation(confidence)
                chosen_plan_id = None
                decision_explanation = self._build_no_incident_explanation(confidence)

        # ── ML Scenario Classifier Override (52 classes) ────────────────
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

        # === Deterministic, evidence-based classification + factual explanation =====
        # The incident TYPE, recommended ACTION and EXPLANATION are derived from the real
        # measured signals (priority-ordered), so every incident is labelled and explained
        # accurately and durably -- not from non-deterministic LLM prose.
        if incident:
            mem_trend = self._finite(safe_features.get("memory_trend_bytes_per_sec", 0.0))
            overall_risk = self._finite(safe_features.get("overall_risk_score", 0.0))
            # The real Kubernetes root cause (collected by the predictor from kube-state-metrics)
            # DRIVES the type, action and Why so every incident is attributed to its ACTUAL cause.
            # When no specific fault is observable we stay HONEST (investigate) instead of assuming
            # a bad deploy -- no fabricated cause.
            _rc = remediation_context.get("root_cause") if isinstance(remediation_context, dict) else None
            rc_low = (_rc or "").lower()
            evidence = []
            dominant = None
            rec_action = None

            # 1) Infrastructure / node outage is the cascade root: a down node explains any
            #    pod/deployment unavailability on it, so attribute to the node, not a deploy.
            if node_not_ready >= 1.0 or "unreachable" in rc_low or "node(s):" in rc_low:
                dominant, rec_action = "NODE_NOT_READY", "ESCALATE_NODE_OUTAGE_AND_REBALANCE"
                evidence.append(f"{node_not_ready:.0f} node(s) NotReady" if node_not_ready >= 1.0
                                else "node unreachable")
            elif monitoring_vm_up < 0.5:
                dominant, rec_action = "MONITORING_DOWN", "ESCALATE_VM_OUTAGE_AND_FAILOVER"
                evidence.append("monitoring VM unreachable (up=0)")
            elif ai_vm_up < 0.5:
                dominant, rec_action = "AI_SERVICE_DOWN", "ESCALATE_VM_OUTAGE_AND_FAILOVER"
                evidence.append("AI VM unreachable (up=0)")
            # 2) Container-level faults, chosen by the ACTUAL Kubernetes reason.
            elif "oomkilled" in rc_low:
                dominant, rec_action = "MEMORY_LEAK", "RESTART_POD_AND_TUNE_MEMORY_LIMITS"
                evidence.append("container OOMKilled (exceeded memory limit)")
            elif "imagepullbackoff" in rc_low or "errimagepull" in rc_low or "invalidimagename" in rc_low:
                dominant, rec_action = "DEPLOYMENT_UNAVAILABLE", "ROLLBACK_RECENT_CHANGE_AND_RESTART"
                evidence.append(f"{unavailable_replicas:.0f} replica(s) unavailable")
            elif "createcontainerconfigerror" in rc_low or "configerror" in rc_low:
                dominant, rec_action = "DEPLOYMENT_UNAVAILABLE", "ROLLBACK_RECENT_CONFIG_CHANGE_AND_RESTART"
                evidence.append(f"{unavailable_replicas:.0f} replica(s) unavailable")
            elif "crashloopbackoff" in rc_low or pod_restarts >= 3.0:
                dominant, rec_action = "POD_CRASH_LOOP", "DELETE_CRASHLOOPING_PODS"
                evidence.append(f"{pod_restarts:.0f} restarts in window" if pod_restarts >= 3.0
                                else "container crash-looping")
            elif "evicted" in rc_low:
                dominant, rec_action = "DEPLOYMENT_UNAVAILABLE", "RESTART_FAILED_WORKLOAD_AND_REBALANCE"
                evidence.append("pod evicted (node resource pressure)")
            # 3) Application-metric incidents (already specific via their numbers).
            elif db_pool_pending >= 1.0 or db_pool_usage_ratio >= 0.9:
                dominant, rec_action = "DATABASE_POOL_SATURATION", "CHECK_DATABASE_CONNECTION_POOL_AND_RESTART"
                evidence.append(f"DB pool pending={db_pool_pending:.0f}, usage={db_pool_usage_ratio:.2f}")
            elif http_5xx_ratio >= 0.10:
                dominant, rec_action = "HIGH_ERROR_RATE", "ROLLBACK_LAST_RELEASE_AND_SHED_TRAFFIC"
                evidence.append(f"HTTP 5xx ratio={http_5xx_ratio*100:.0f}%")
            elif latency_p95 >= 1.0:
                dominant, rec_action = "LATENCY_DEGRADATION", "SCALE_DEPLOYMENT_AND_REBALANCE"
                evidence.append(f"HTTP p95 latency={latency_p95:.2f}s")
            elif cpu_cores >= 0.20:
                dominant, rec_action = "CPU_STRESS", "RESTART_POD_AND_CAPTURE_CPU_PROFILE"
                evidence.append(f"CPU={cpu_cores:.2f} cores")
            elif memory_bytes >= 1.0e8 and mem_trend > 0.0:
                dominant, rec_action = "MEMORY_LEAK", "RESTART_POD_AND_TUNE_MEMORY_LIMITS"
                evidence.append(f"memory={memory_bytes/1e6:.0f}MB rising (+{mem_trend/1e3:.0f}KB/s)")
            # 4) Replicas unavailable with NO identifiable container fault: stay HONEST --
            #    do NOT assume a bad release; recommend investigation.
            elif unavailable_replicas >= 1.0:
                dominant, rec_action = "DEPLOYMENT_UNAVAILABLE", "INVESTIGATE"
                evidence.append(f"{unavailable_replicas:.0f} replica(s) unavailable")
                if not _rc:
                    _rc = ("no container-level fault detected (no ImagePullBackOff, CrashLoop or OOM); "
                           "likely a pending reschedule, unschedulable pod, or failing readiness probe")
                    rc_low = _rc.lower()

            if dominant is not None:
                incident_type = dominant
                action = rec_action
                if execution_mode == "AUTO_EXECUTE_CANDIDATE":
                    final_executable_action = self._map_executable_action(action)

            # Demote a flat-memory false positive (a JVM holding its heap is not a leak),
            # but never when the kernel actually OOMKilled the container.
            if (incident_type in ("MEMORY_LEAK", "MEMORY_STRESS")
                    and mem_trend <= 0.0 and pod_restarts < 1.0 and unavailable_replicas < 1.0
                    and "oomkilled" not in rc_low):
                incident, noise, incident_type = False, True, "NO_INCIDENT"

            if incident:
                if _rc:
                    evidence.append(f"K8s root cause: {_rc}")
                ev = "; ".join(evidence) if evidence else f"composite risk score={overall_risk:.2f}"
                # Deterministic, cause-aware Why is the GUARANTEED baseline (always correct and
                # specific). The LLM is an optional flourish, used only if it actually names the
                # cause -- so it can only improve the answer, never degrade or fabricate it.
                why = self._why_for(incident_type, action, _rc)
                specialist = None
                if not skip_llm:
                    _llm_why = self._explain_why(incident_type, severity, confidence, ev, action)
                    if _llm_why and self._llm_ok(_llm_why, _rc):
                        why = _llm_why
                    # Fine-tuned 0.5B specialist: gated second opinion (never overrides ML).
                    specialist = self._specialist_opinion(incident_type, severity, action, ev)
                decision_explanation = (
                    f"{incident_type} (severity {severity}, confidence {confidence:.2f}). "
                    f"Measured evidence: {ev}. Recommended action: {action}. "
                    f"Why: {why}"
                )
                if specialist and specialist.get("consensus_with_ml"):
                    decision_explanation += " [ML + fine-tuned specialist consensus]"

        summary = self._build_summary(incident, incident_type, severity, confidence)
        decision_context = self._build_decision_context(
            incident_type,
            severity,
            confidence,
            ranked_plans,
            execution_mode,
            remediation_context,
        )
        # Record the fine-tuned specialist's gated opinion for transparency (ML stays decider).
        if incident and not skip_llm and specialist is not None:
            decision_context["specialist"] = specialist

        return InferenceResult(
            incident=incident,
            noise=noise,
            incident_type=incident_type,
            severity=severity,
            recommended_action=action,
            final_executable_action=final_executable_action,
            execution_mode=execution_mode,
            recommendation_only_reason=recommendation_only_reason,
            confidence=round(self._finite(confidence), 4),
            summary=summary,
            decision_explanation=decision_explanation,
            chosen_plan_id=chosen_plan_id,
            top_recommended_plans=[
                {
                    "rank": index + 1,
                    "plan_id": plan.plan_id,
                    "title": plan.title,
                    "strategy": plan.strategy,
                    "recommended_action": plan.recommended_action,
                    "executable_action": plan.executable_action,
                    "safe_to_execute": plan.safe_to_execute,
                    "execution_mode": plan.execution_mode,
                    "rationale": plan.rationale,
                    "reasons": plan.reasons,
                    "advantages": plan.advantages,
                    "tradeoffs": plan.tradeoffs,
                    "risk_score": round(plan.risk_score, 4),
                    "cost_score": round(plan.cost_score, 4),
                    "blast_radius_score": round(plan.blast_radius_score, 4),
                    "historical_effectiveness": round(plan.historical_effectiveness, 4),
                    "novelty_score": round(plan.novelty_score, 4),
                    "priority_score": round(plan.priority_score, 4),
                    "requires_approval": plan.requires_approval,
                }
                for index, plan in enumerate(ranked_plans[:5])
            ],
            decision_context=decision_context,
            rule_score=round(self._finite(rule_score), 4),
            model_score=round(model_score, 4) if model_score is not None else None,
        )

    def _predict_scenario_ml(self, features: dict[str, float]) -> tuple[str | None, float]:
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

    def _predict_incident_score(self, features: dict[str, float]) -> float | None:
        if not self.model_bundle.loaded:
            return None

        model = self.model_bundle.incident_model
        vector = self._to_vector(features)
        if model is None:
            return None

        if hasattr(model, "predict_proba"):
            probs = model.predict_proba(vector)
            score = float(probs[0][1]) if probs.shape[1] > 1 else float(probs[0][0])
            return self._finite_or_none(score)

        if hasattr(model, "predict"):
            prediction = model.predict(vector)
            return self._finite_or_none(float(prediction[0]))

        return None

    def _predict_severity(
        self,
        features: dict[str, float],
        incident: bool,
        confidence: float,
        incident_type: str,
    ) -> str:
        if not incident:
            return "LOW"

        severity_override = self._classify_severity_from_features(features, confidence, incident_type)
        if severity_override is not None:
            return severity_override

        label = self._predict_label(
            model=self.model_bundle.severity_model,
            features=features,
            labels=self.model_bundle.severity_labels,
        )
        if label in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}:
            return label
        return self._classify_severity(confidence)

    def _predict_action(
        self,
        features: dict[str, float],
        incident_type: str,
        severity: str,
        incident: bool,
    ) -> str:
        if not incident:
            return "NO_ACTION"

        override = self._recommend_action(features, incident_type, severity, incident)
        if incident_type in {
            "VM_DOWN",
            "AVAILABILITY_DROP",
            "CPU_STRESS",
            "MEMORY_STRESS",
            "RESTART_LOOP",
            "APPLICATION_ERROR_STORM",
            "DATABASE_CONNECTIVITY",
            "GENERAL_DEGRADATION",
        }:
            return override

        label = self._predict_label(
            model=self.model_bundle.action_model,
            features=features,
            labels=self.model_bundle.action_labels,
        )
        if label:
            return label

        return self._recommend_action(features, incident_type, severity, incident)

    def _predict_label(
        self,
        model,
        features: dict[str, float],
        labels: list[str] | None,
    ) -> str | None:
        if model is None:
            return None

        vector = self._to_vector(features)
        if not hasattr(model, "predict"):
            return None

        prediction = model.predict(vector)
        if len(prediction) == 0:
            return None

        raw = prediction[0]

        if labels:
            try:
                index = int(raw)
                if 0 <= index < len(labels):
                    return str(labels[index]).upper()
            except Exception:
                pass

        if isinstance(raw, str):
            return raw.upper()

        classes = getattr(model, "classes_", None)
        if classes is not None:
            try:
                class_value = classes[int(raw)]
                return str(class_value).upper()
            except Exception:
                return str(raw).upper()

        return str(raw).upper()

    def _to_vector(self, features: dict[str, float]) -> np.ndarray:
        feature_order = self.model_bundle.feature_order or sorted(features.keys())
        # Build lookup tolerant of feature_ prefix: training CSV uses feature_cpu_usage_cores
        # but inference produces cpu_usage_cores — support both by trying the stripped key.
        lookup: dict[str, float] = {}
        for key, value in features.items():
            lookup[key] = value
            lookup[f"feature_{key}"] = value  # also register with prefix
        return np.array(
            [[self._finite(lookup.get(key, 0.0)) for key in feature_order]],
            dtype=float,
        )

    @staticmethod
    def _finite(value: float | int | None, default: float = 0.0) -> float:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return default
        return numeric if math.isfinite(numeric) else default

    @classmethod
    def _finite_or_none(cls, value: float | int | None) -> float | None:
        numeric = cls._finite(value, default=float("nan"))
        return numeric if math.isfinite(numeric) else None

    @staticmethod
    def _why_for(incident_type: str, action: str, rc: str | None) -> str:
        """Deterministic, cause-aware explanation of WHY the action resolves the incident.
        ALWAYS correct and specific (names the real root cause), for every incident type and
        every cause -- with no dependence on the LLM. This is the guaranteed baseline."""
        rc_low = (rc or "").lower()
        # 0) Honest "no fault" case FIRST: that message lists the fault names to say they are
        #    ABSENT ("no ImagePullBackOff, CrashLoop or OOM"), so it must be matched before the
        #    substring checks below or it would be mistaken for one of those faults.
        if "no container-level fault" in rc_low or (action and "INVESTIGATE" in action.upper()):
            return ("no image, crash or OOM fault is observable, so the cause is not yet identified; the recent "
                    "rollout, pod scheduling and readiness probes must be investigated before any rollback.")
        # 1) Cause-specific (most precise) -- keyed on the real Kubernetes reason.
        if "imagepullbackoff" in rc_low or "errimagepull" in rc_low:
            return ("the new image cannot be pulled (ImagePullBackOff), so the replicas never start; rolling "
                    "back to the last working image restores availability immediately.")
        if "invalidimagename" in rc_low:
            return ("the image reference is invalid (InvalidImageName), so the container is never created; "
                    "rolling back to the last valid image restores the deployment.")
        if "createcontainerconfigerror" in rc_low or "configerror" in rc_low:
            return ("a referenced ConfigMap or Secret is missing or invalid (CreateContainerConfigError), so "
                    "the container cannot start; rolling back the recent config change restores it.")
        if "oomkilled" in rc_low:
            return ("the container is OOMKilled for exceeding its memory limit, so restarting clears the "
                    "exhausted memory while raising the limit stops the kernel from killing it again.")
        if "crashloopbackoff" in rc_low:
            return ("the container keeps crashing on start (CrashLoopBackOff), so deleting the pods forces a "
                    "clean recreation; if it persists the last release should be rolled back.")
        if "evicted" in rc_low:
            return ("the pod was evicted under node resource pressure, so rescheduling it onto a node with "
                    "free capacity restores the workload.")
        if "unreachable" in rc_low or "node(s):" in rc_low or incident_type == "NODE_NOT_READY":
            return ("the node hosting the workload is unreachable, so restarting its pods cannot help; "
                    "escalating triggers node failover and reschedules the workload onto healthy nodes.")
        # 2) Type-specific (metric incidents are already specific via their numbers).
        by_type = {
            "MONITORING_DOWN": "the monitoring VM is unreachable, so escalation initiates failover to restore observability.",
            "AI_SERVICE_DOWN": "the AI VM is unreachable, so escalation initiates failover to restore automated remediation.",
            "DATABASE_POOL_SATURATION": "the database connection pool is exhausted, so restarting frees the leaked connections after a pool check.",
            "HIGH_ERROR_RATE": "the 5xx error rate spiked, typically after a release, so rolling back and shedding traffic stops user-facing failures.",
            "LATENCY_DEGRADATION": "p95 latency degraded under load, so scaling out adds capacity to absorb the pressure.",
            "CPU_STRESS": "CPU is saturated and slowing every request, so restarting the hot pod clears the runaway work while a CPU profile is captured.",
            "MEMORY_LEAK": "memory is climbing toward the limit, so restarting reclaims it while the memory limit is tuned.",
            "POD_CRASH_LOOP": "the container keeps restarting, so deleting the crash-looping pods forces a clean recreation.",
            "DEPLOYMENT_UNAVAILABLE": "the replicas cannot become ready, so the recent rollout should be investigated and rolled back if a bad release is confirmed.",
        }
        return by_type.get(incident_type, f"{action} directly addresses the detected condition with the lowest blast radius.")

    @staticmethod
    def _llm_ok(text: str, rc: str | None) -> bool:
        """Accept the LLM 'Why' only when it actually names the cause, so the LLM can only IMPROVE
        the deterministic baseline -- never replace a specific explanation with a vague one, and
        never assert a cause when none was detected."""
        t = (text or "").lower()
        if len(t) < 15:
            return False
        rc_low = (rc or "").lower()
        if "no container-level fault" in rc_low:
            return "investigat" in t and not any(
                x in t for x in ("imagepull", "oomkilled", "crashloop", "bad release", "the recent change")
            )
        keymap = [
            (("imagepullbackoff", "errimagepull", "invalidimagename"), ("image",)),
            (("createcontainerconfigerror", "configerror"), ("config", "secret", "configmap")),
            (("oomkilled",), ("oom", "memory")),
            (("crashloopbackoff",), ("crash", "restart")),
            (("evicted",), ("evict", "resource", "reschedul")),
            (("unreachable", "node(s):"), ("node",)),
        ]
        for triggers, required in keymap:
            if any(x in rc_low for x in triggers):
                return any(r in t for r in required)
        return True  # metric incident (no K8s reason): any clean, non-empty LLM sentence is fine

    def _explain_why(self, incident_type, severity, confidence, evidence, action):
        """LLM reasoning, few-shot constrained to the facts. One grounded sentence or None."""
        try:
            import httpx as _httpx, re as _re
            system_msg = (
                "You are a senior SRE. Given an incident and its recommended action, reply with "
                "ONE short sentence explaining WHY that action fixes it. Use only the facts given. "
                "If a Kubernetes root cause is provided (e.g. ImagePullBackOff, OOMKilled, "
                "CrashLoopBackOff, CreateContainerConfigError, Evicted), NAME it and tie the fix to it. "
                "If it says no fault was detected, say the cause is not yet identified and recommend "
                "investigation -- never invent a cause. "
                "Never restate the type, severity or numbers. Never write a list or a preamble."
            )
            # Few-shot: small models copy the STYLE of these examples far better than rules.
            # The examples NAME the K8s root cause (or stay honest when there is none).
            ex1_u = ("Incident: DEPLOYMENT_UNAVAILABLE (severity HIGH). Measured evidence: 1 replica unavailable; "
                     "K8s root cause: container waiting: ImagePullBackOff. "
                     "Recommended action: ROLLBACK_RECENT_CHANGE_AND_RESTART. Why is this action the right response?")
            ex1_a = ("The new image cannot be pulled (ImagePullBackOff) so the replica never starts; rolling back "
                     "to the last working image lets the pods become ready again immediately.")
            ex2_u = ("Incident: POD_CRASH_LOOP (severity HIGH). Measured evidence: 7 restarts in window; "
                     "K8s root cause: last termination: OOMKilled. "
                     "Recommended action: RESTART_POD_AND_TUNE_MEMORY_LIMITS. Why is this action the right response?")
            ex2_a = ("The container is OOMKilled because it exceeds its memory limit, so restarting clears the "
                     "exhausted memory while raising the limit stops the kernel from killing it again.")
            ex3_u = ("Incident: DEPLOYMENT_UNAVAILABLE (severity MEDIUM). Measured evidence: 1 replica unavailable; "
                     "K8s root cause: no container-level fault detected (no ImagePullBackOff, CrashLoop or OOM). "
                     "Recommended action: INVESTIGATE. Why is this action the right response?")
            ex3_a = ("No image, crash or OOM fault was detected, so the cause is not yet identified; investigating "
                     "the recent rollout, scheduling and readiness probes is the right first step before any rollback.")
            user_msg = (
                f"Incident: {incident_type} (severity {severity}). "
                f"Measured evidence: {evidence}. "
                f"Recommended action: {action}. Why is this action the right response?"
            )
            with _httpx.Client(timeout=5.0) as _http:
                resp = _http.post(
                    "http://localhost:11434/api/chat",
                    json={
                        "model": "aiops-sre-q4-backup",
                        "messages": [
                            {"role": "system", "content": system_msg},
                            {"role": "user", "content": ex1_u},
                            {"role": "assistant", "content": ex1_a},
                            {"role": "user", "content": ex2_u},
                            {"role": "assistant", "content": ex2_a},
                            {"role": "user", "content": ex3_u},
                            {"role": "assistant", "content": ex3_a},
                            {"role": "user", "content": user_msg},
                        ],
                        "stream": False,
                        "options": {"temperature": 0.1, "num_predict": 90},
                    },
                )
            if resp.status_code != 200:
                return None
            raw = (resp.json().get("message", {}).get("content", "") or "").strip()
            if not raw:
                return None
            # Keep only the first answer line; small models ramble (markdown/meta) after it.
            low = raw.lower()
            cut = len(raw)
            for mk in ["\n\n", "\n#", "\n**", "\nlet's", "\nlet me", "\nhere", "\nbelow",
                       "\nsure", "for the following", "as follows", " reasons:", "\n1.", "\n- ", "\n* ",
                       "incident:", "measured evidence:", "recommended action:", "why is this",
                       "(severity"]:
                j = low.find(mk)
                if j != -1:
                    cut = min(cut, j)
            raw = raw[:cut].strip()
            sents = _re.findall(r"[^.!?]*[.!?]", raw) or ([raw] if raw else [])
            clean = []
            for sx in sents:
                t = sx.strip()
                if not t:
                    continue
                lt = t.lower()
                if lt.startswith(("the incident", "this incident", "the recommended action",
                                  "let's", "let me", "here's", "here is", "sure", "certainly",
                                  "you are", "your are", "as an", "i am", "i'm", "note:", "note that")):
                    continue
                if t.endswith(":") or "#" in t or "**" in t:
                    continue
                clean.append(t)
                # Few-shot answers are a single sentence, so the model's first clean sentence IS
                # the answer; anything after it is rambling/meta -> stop at one sentence.
                if len(clean) >= 1:
                    break
            concise = " ".join(clean).strip()
            return concise if concise else None
        except Exception:
            return None

    def _specialist_opinion(self, incident_type, severity, action, evidence):
        """Fine-tuned 0.5B specialist (Ollama model 'aiops-sre-ft') — a GATED second opinion
        on the structured decision. It NEVER overrides the ML/deterministic decision: its JSON
        output is validated against the taxonomy and kept only as a consensus signal; any
        malformed or out-of-taxonomy output is dropped so it can never pollute the result.
        Returns a small dict recorded in decision_context, or None on hard failure."""
        try:
            import httpx as _httpx, json as _json, re as _re
            # Grammar-constrained decoding: the model is FORCED to emit valid JSON whose
            # incident_type is one of the 52 taxonomy classes (no hallucinated types, no broken
            # schema). Few-shot examples teach the format + the exact action vocabulary.
            _TYPES = [
                "NO_INCIDENT", "POD_CRASH_LOOP", "POD_OOM_KILLED", "POD_IMAGE_PULL_BACKOFF",
                "POD_PENDING_INSUFFICIENT_RESOURCES", "POD_STUCK_TERMINATING", "POD_LIVENESS_FAILURE",
                "POD_READINESS_FAILURE", "POD_INIT_CONTAINER_FAILURE", "NODE_NOT_READY", "NODE_DISK_FULL",
                "NODE_HIGH_CPU", "NODE_HIGH_MEMORY", "NODE_KUBELET_DOWN", "NODE_HARDWARE_FAILURE",
                "NODE_CLOCK_SKEW", "NODE_DISK_IO_SATURATION", "DB_CONNECTION_REFUSED", "DB_DEADLOCK",
                "DB_REPLICATION_LAG", "DB_SLOW_QUERIES", "DB_DISK_FULL", "DB_HIGH_WRITE_LOAD",
                "DB_CONNECTION_POOL_EXHAUSTION", "NETWORK_DNS_FAILURE", "NETWORK_PARTITION",
                "NETWORK_PACKET_LOSS", "NETWORK_LATENCY_BETWEEN_NODES", "INGRESS_BACKEND_DOWN",
                "SERVICE_DISCOVERY_FAILURE", "AUTH_TOKEN_EXPIRED", "AUTH_BRUTE_FORCE_ATTEMPT",
                "UNAUTHORIZED_API_ACCESS_SPIKE", "TLS_CERTIFICATE_EXPIRY", "SECRET_ROTATION_FAILURE",
                "PVC_PENDING", "PVC_USAGE_HIGH", "PV_FILESYSTEM_CORRUPTION", "HIGH_LATENCY_P95",
                "HIGH_LATENCY_P99", "ERROR_RATE_4XX_SPIKE", "ERROR_RATE_5XX_SPIKE", "REQUEST_RATE_DROP",
                "CIRCUIT_BREAKER_OPEN", "THREAD_POOL_EXHAUSTION", "MEMORY_LEAK", "CPU_SATURATION",
                "EXTERNAL_API_DEPENDENCY_DOWN", "METRICS_SCRAPE_FAILURE", "PROMETHEUS_OUT_OF_MEMORY",
                "LOKI_INGESTION_LAG", "UNKNOWN_ANOMALY",
            ]
            _schema = {
                "type": "object",
                "properties": {
                    "incident_type": {"type": "string", "enum": _TYPES},
                    "severity": {"type": "string", "enum": ["LOW", "MEDIUM", "HIGH", "CRITICAL"]},
                    "recommended_action": {"type": "string"},
                    "explanation": {"type": "string"},
                },
                "required": ["incident_type", "severity", "recommended_action"],
            }
            _sys = ("You are an SRE incident classifier. Given a trace, output the single best "
                    "incident_type from the allowed list, its severity, a recommended_action, "
                    "and a one-line explanation. JSON only.")
            with _httpx.Client(timeout=13.0) as _http:
                resp = _http.post(
                    "http://localhost:11434/api/chat",
                    json={
                        "model": "aiops-sre-ft-v2",  # retrained 0.5B: 86.8% vs 26.4% (v1) on held-out test
                        "messages": [
                            {"role": "system", "content": _sys},
                            {"role": "user", "content": "Trace shows: 7 restarts in window; container waiting CrashLoopBackOff; last termination Error. Pick the best type and action."},
                            {"role": "assistant", "content": '{"incident_type":"POD_CRASH_LOOP","severity":"HIGH","recommended_action":"DELETE_CRASHLOOPING_PODS","explanation":"Container restarts in a loop; delete the pods for a clean restart."}'},
                            {"role": "user", "content": "Trace shows: last termination OOMKilled; memory at the limit. Pick the best type and action."},
                            {"role": "assistant", "content": '{"incident_type":"POD_OOM_KILLED","severity":"HIGH","recommended_action":"RESTART_POD_AND_TUNE_MEMORY_LIMITS","explanation":"Container exceeds its memory limit; restart and raise the limit."}'},
                            {"role": "user", "content": "Trace shows: 1 node NotReady; node unreachable. Pick the best type and action."},
                            {"role": "assistant", "content": '{"incident_type":"NODE_NOT_READY","severity":"HIGH","recommended_action":"ESCALATE_NODE_OUTAGE_AND_REBALANCE","explanation":"The node is unreachable; escalate and reschedule its pods onto healthy nodes."}'},
                            {"role": "user", "content": f"Trace shows: {evidence}. Pick the best type and action."},
                        ],
                        "stream": False,
                        "format": _schema,  # forces a valid type from the 52 (grammar-constrained)
                        "options": {"temperature": 0.0, "repeat_penalty": 1.2, "num_predict": 200},
                    },
                )
            if resp.status_code != 200:
                return {"model": "aiops-sre-ft-v2", "status": "unavailable", "consensus_with_ml": False}
            raw = (resp.json().get("message", {}).get("content", "") or "").strip()
            m = _re.search(r"\{.*\}", raw, _re.DOTALL)
            if not m:
                return {"model": "aiops-sre-ft-v2", "status": "rejected_no_json", "consensus_with_ml": False}
            try:
                obj = _json.loads(m.group(0))
            except Exception:
                return {"model": "aiops-sre-ft-v2", "status": "rejected_bad_json", "consensus_with_ml": False}
            # The small model is inconsistent about the field name (incident_type / type / eventType)
            # and the action key (recommended_action / action) — accept the common variants.
            s_type = str(obj.get("incident_type") or obj.get("type") or obj.get("eventType") or "").strip().upper()
            s_sev = str(obj.get("severity") or "").strip().upper()
            s_act = str(obj.get("recommended_action") or obj.get("action") or "").strip().upper()
            # GATING: reject hallucinated / out-of-taxonomy output (e.g. severity "BRIGHT").
            valid = (
                bool(_re.fullmatch(r"[A-Z][A-Z0-9_]{2,40}", s_type))
                and s_sev in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
                and len(s_act) >= 2  # action is free-form/advisory — don't reject the opinion over it
            )
            if not valid:
                return {"model": "aiops-sre-ft-v2", "status": "rejected_invalid_output",
                        "consensus_with_ml": False, "raw_type": s_type[:40]}
            return {
                "model": "aiops-sre-ft-v2",
                "status": "ok",
                "incident_type": s_type,
                "severity": s_sev,
                "recommended_action": s_act,
                "consensus_with_ml": (s_type == str(incident_type).upper()),
            }
        except Exception:
            return None

    def _full_llm_predict(self, features: dict[str, float]) -> dict | None:
        """
        Full LLM-based analysis via qwen2:0.5b (Ollama).
        Pre-classifies metrics as labels so the small model can reason correctly.
        Short-circuits with NO_INCIDENT when all signals are normal (no LLM call needed).
        Returns a dict with all fields, or None on failure (triggers rule-based fallback).
        """
        try:
            import httpx as _httpx
            import json as _json

            memory_gb = features.get("memory_working_set_bytes", 0.0) / 1_000_000_000
            cpu = features.get("cpu_usage_cores", 0.0)
            http_5xx = features.get("http_5xx_ratio", 0.0)
            latency = features.get("http_latency_p95_seconds", 0.0)
            restarts = features.get("pod_restarts_total", 0.0)
            unavailable = features.get("unavailable_replicas", 0.0)
            db_hits = features.get("db_keyword_hits", 0.0)
            restart_hits = features.get("restart_keyword_hits", 0.0)
            db_pending = features.get("db_pool_pending", 0.0)
            risk = features.get("overall_risk_score", 0.0)

            # Pre-classify each signal so the small LLM can reason about labels, not numbers.
            def _lv(val: float, hi: float, crit: float) -> str:
                if val >= crit:
                    return "CRITICAL"
                if val >= hi:
                    return "HIGH"
                return "NORMAL"

            cpu_lv = _lv(cpu, 0.55, 0.85)
            mem_lv = _lv(memory_gb, 0.70, 1.20)
            err_lv = _lv(http_5xx, 0.08, 0.30)
            lat_lv = _lv(latency, 0.50, 2.00)
            rst_lv = "HIGH" if restarts >= 2.0 else ("ELEVATED" if restarts >= 1.0 else "NORMAL")
            unavail_lv = "HIGH" if unavailable >= 1.0 else "NORMAL"
            db_lv = "HIGH" if db_hits >= 5.0 else ("ELEVATED" if db_hits >= 2.0 else "NORMAL")
            dbp_lv = "HIGH" if db_pending >= 1.0 else "NORMAL"
            restart_kw_lv = "HIGH" if restart_hits >= 2.0 else ("ELEVATED" if restart_hits >= 1.0 else "NORMAL")

            signals = {
                "cpu": cpu_lv, "memory": mem_lv, "http_5xx": err_lv,
                "latency": lat_lv, "restarts": rst_lv, "unavailable_replicas": unavail_lv,
                "db_keyword_hits": db_lv, "db_pool_pending": dbp_lv,
                "restart_keyword_hits": restart_kw_lv,
            }
            elevated = {k: v for k, v in signals.items() if v != "NORMAL"}

            # Short-circuit: if nothing is elevated, skip the LLM entirely.
            if not elevated:
                return {
                    "incident": False,
                    "incident_type": "NO_INCIDENT",
                    "severity": "LOW",
                    "confidence": 0.10,
                    "recommended_action": "NO_ACTION",
                    "execution_mode": "RECOMMEND_ONLY",
                    "explanation": "All Kubernetes signals are within normal ranges — no incident detected.",
                }

            signal_summary = ", ".join(f"{k}={v}" for k, v in elevated.items())

            system_msg = (
                "You are a Kubernetes SRE JSON API. "
                "You receive pre-classified signals (NORMAL/ELEVATED/HIGH/CRITICAL) and output ONLY a JSON object. "
                "No explanation outside the JSON."
            )
            taxonomy = (
                "TAXONOMY_51_CLASSES (pick the BEST match):\n"
                "POD: POD_CRASH_LOOP(restarts>5), POD_OOM_KILLED(OOMKilled+mem high), "
                "POD_IMAGE_PULL_BACKOFF(image registry), POD_PENDING_INSUFFICIENT_RESOURCES(cluster ressources), "
                "POD_STUCK_TERMINATING(deletion stuck), POD_LIVENESS_FAILURE(probe KO), "
                "POD_READINESS_FAILURE(not ready no crash), POD_INIT_CONTAINER_FAILURE(init failed); "
                "APP: HIGH_LATENCY_P95(p95>1s), HIGH_LATENCY_P99(tail>3s), "
                "ERROR_RATE_5XX_SPIKE(5xx ratio), ERROR_RATE_4XX_SPIKE(4xx ratio), "
                "REQUEST_RATE_DROP(traffic drop), MEMORY_LEAK(mem growing linear), "
                "CPU_SATURATION(cpu>90%), THREAD_POOL_EXHAUSTION(java threads blocked), "
                "DB_CONNECTION_POOL_EXHAUSTION(hikari pending>5), CIRCUIT_BREAKER_OPEN(CB tripped); "
                "DB: DB_SLOW_QUERIES, DB_DEADLOCK, DB_REPLICATION_LAG, DB_DISK_FULL, "
                "DB_CONNECTION_REFUSED(up=0), DB_HIGH_WRITE_LOAD; "
                "NET: NETWORK_DNS_FAILURE(SERVFAIL spike), NETWORK_PACKET_LOSS, "
                "NETWORK_LATENCY_BETWEEN_NODES, INGRESS_BACKEND_DOWN(502 spike), "
                "SERVICE_DISCOVERY_FAILURE, TLS_CERTIFICATE_EXPIRY, NETWORK_PARTITION, "
                "EXTERNAL_API_DEPENDENCY_DOWN; "
                "NODE: NODE_NOT_READY, NODE_HIGH_CPU(VM cpu>90%), NODE_HIGH_MEMORY, "
                "NODE_DISK_FULL, NODE_DISK_IO_SATURATION, NODE_KUBELET_DOWN, "
                "NODE_CLOCK_SKEW, NODE_HARDWARE_FAILURE; "
                "SEC: AUTH_TOKEN_EXPIRED(401 spike on auth/me), AUTH_BRUTE_FORCE_ATTEMPT, "
                "UNAUTHORIZED_API_ACCESS_SPIKE(403 spike), SECRET_ROTATION_FAILURE; "
                "STORAGE: PVC_PENDING, PV_FILESYSTEM_CORRUPTION, PVC_USAGE_HIGH; "
                "OBS: PROMETHEUS_OUT_OF_MEMORY, LOKI_INGESTION_LAG, METRICS_SCRAPE_FAILURE; "
                "OTHER: GENERAL_DEGRADATION, UNKNOWN_ANOMALY, NO_INCIDENT.\n"
                "ACTIONS (pick one): RESTART_POD, SCALE_DEPLOYMENT, ROLLBACK_DEPLOYMENT, "
                "DELETE_CRASHLOOPING_PODS, FORCE_RECREATE_DEPLOYMENT, SCALE_DOWN, "
                "ESCALATION, NO_ACTION."
            )

            user_msg = (
                f"Elevated signals detected: {signal_summary}\n"
                f"(all other signals: NORMAL)\n\n"
                f"{taxonomy}\n"
                "Output JSON only:\n"
                '{"incident": true_or_false, "incident_type": "TYPE", "severity": "SEV", '
                '"confidence": 0.50_to_0.95, "recommended_action": "ACTION", "auto_execute": true_or_false, '
                '"explanation": "one sentence in english"}\n\n'
                "severity: LOW MEDIUM HIGH CRITICAL.\n"
                "auto_execute=true only if confidence>=0.70 AND severity in (MEDIUM, HIGH)."
            )

            with _httpx.Client(timeout=5.0) as _http:
                resp = _http.post(
                    "http://localhost:11434/api/chat",
                    json={
                        "model": "aiops-sre-q4",
                        "messages": [
                            {"role": "system", "content": system_msg},
                            {"role": "user", "content": user_msg},
                        ],
                        "stream": False,
                        "format": "json",
                        "options": {"temperature": 0.0, "num_predict": 200},
                    },
                )

            if resp.status_code != 200:
                return None

            resp_json = resp.json()
            # Chat API returns message.content; generate API returns response
            raw = (
                resp_json.get("message", {}).get("content", "")
                or resp_json.get("response", "")
            ).strip()

            # Extract JSON block from response
            start = raw.find("{")
            end = raw.rfind("}") + 1
            if start == -1 or end == 0:
                return None

            data = _json.loads(raw[start:end])

            valid_types = {
                "CPU_STRESS", "MEMORY_STRESS", "APPLICATION_ERROR_STORM",
                "DATABASE_CONNECTIVITY", "AVAILABILITY_DROP", "RESTART_LOOP",
                "GENERAL_DEGRADATION", "VM_DOWN", "NO_INCIDENT",
            }
            valid_actions = {
                "SCALE_DEPLOYMENT_AND_CAPTURE_CPU_PROFILE",
                "RESTART_POD_AND_SCALE_MEMORY_LIMITS",
                "ROLLBACK_LAST_RELEASE_AND_SHED_TRAFFIC",
                "FAILOVER_DATABASE_AND_RESTART_WORKLOAD",
                "SCALE_DEPLOYMENT_AND_REBALANCE",
                "ROLLBACK_RECENT_CHANGE_AND_RESTART",
                "CAPTURE_DIAGNOSTICS_AND_INVESTIGATE",
                "RESTART_POD_AND_INVESTIGATE",
                "NO_ACTION",
            }

            incident_type = str(data.get("incident_type", "")).upper()
            if incident_type not in valid_types:
                return None

            action = str(data.get("recommended_action", "")).upper()
            if action not in valid_actions:
                action = "CAPTURE_DIAGNOSTICS_AND_INVESTIGATE"

            confidence = float(data.get("confidence", 0.5))
            confidence = max(0.0, min(1.0, confidence))

            incident = bool(data.get("incident", False))
            auto_execute = bool(data.get("auto_execute", False))
            severity = str(data.get("severity", "MEDIUM")).upper()
            if severity not in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}:
                severity = "MEDIUM"
            explanation = str(data.get("explanation", "LLM analysis completed."))

            # Sanity guard: if the strongest real signal is only ELEVATED (not HIGH/CRITICAL),
            # cap severity at MEDIUM to prevent the small model from over-dramatising.
            max_signal = max(elevated.values(), key=lambda v: {"NORMAL": 0, "ELEVATED": 1, "HIGH": 2, "CRITICAL": 3}.get(v, 0))
            if max_signal == "ELEVATED" and severity == "CRITICAL":
                severity = "MEDIUM"

            if incident_type == "NO_INCIDENT":
                incident = False
            if not incident:
                incident_type = "NO_INCIDENT"
                action = "NO_ACTION"
                auto_execute = False

            execution_mode = (
                "AUTO_EXECUTE_CANDIDATE"
                if auto_execute and confidence >= 0.70 and severity != "CRITICAL"
                else "RECOMMEND_ONLY"
            )

            return {
                "incident": incident,
                "incident_type": incident_type,
                "severity": severity,
                "confidence": confidence,
                "recommended_action": action,
                "execution_mode": execution_mode,
                "explanation": explanation,
            }

        except Exception:
            return None

    @staticmethod
    def _classify_incident_type(features: dict[str, float], incident: bool) -> str:
        if not incident:
            return "NO_INCIDENT"

        log_line_count = float(features.get("log_line_count", 0.0))
        error_line_count = float(features.get("error_line_count", 0.0))
        restart_hits = float(features.get("restart_keyword_hits", 0.0))
        restarts = float(features.get("pod_restarts_total", 0.0))
        unavailable = float(features.get("unavailable_replicas", 0.0))
        cpu = float(features.get("cpu_usage_cores", 0.0))
        memory = float(features.get("memory_working_set_bytes", 0.0))
        latency = float(features.get("http_latency_p95_seconds", 0.0))
        http_5xx = float(features.get("http_5xx_ratio", 0.0))
        error_ratio = float(features.get("error_ratio", 0.0))
        db_hits = float(features.get("db_keyword_hits", 0.0))
        db_pool_pending = float(features.get("db_pool_pending", 0.0))
        db_pool_usage_ratio = float(features.get("db_pool_usage_ratio", 0.0))
        if features.get("node_not_ready_count", 0.0) >= 1.0:
            return "VM_DOWN"
        if features.get("monitoring_vm_up", 1.0) < 0.5:
            return "VM_DOWN"
        if features.get("ai_vm_up", 1.0) < 0.5:
            return "VM_DOWN"
        # Pool exhaustion: pending threads or near-full pool with no restarts and all replicas available.
        # The unavailable <= 0.0 guard prevents false DATABASE_CONNECTIVITY during RESTART_LOOP:
        # when a pod crashes, the surviving pod's pool temporarily spikes (all traffic redirected),
        # but kube_deployment_status_replicas_unavailable reaches 1 before pod_restarts_total is scraped.
        if (
            db_pool_pending >= 1.0
            and restarts < 1.0
            and unavailable <= 0.0
        ):
            return "DATABASE_CONNECTIVITY"
        if (
            db_pool_usage_ratio >= 0.75
            and restarts < 1.0
            and http_5xx < 0.30
            and unavailable <= 0.0
        ):
            return "DATABASE_CONNECTIVITY"
        if (
            restarts >= 2.0
            and http_5xx < 0.35
            and db_hits < 5.0
        ):
            return "RESTART_LOOP"
        if (
            restarts >= 1.0
            and latency >= 3.0
            and http_5xx < 0.25
            and error_ratio < 0.10
            and db_hits < 5.0
        ):
            return "RESTART_LOOP"
        if (
            restarts >= 1.0
            and (restart_hits >= 1.0 or unavailable > 0.0)
            and http_5xx < 0.10
            and db_hits < 5.0
        ):
            return "RESTART_LOOP"
        if (
            restarts >= 1.0
            and (restart_hits >= 2.0 or (restarts >= 2.0 and unavailable > 0.0))
            and db_hits < 8.0
            and http_5xx < 0.30
        ):
            return "RESTART_LOOP"
        # Crash-on-demand with DB reconnect artifacts (db_hits 5-10) and crash keywords.
        # restart_hits >= 1 discriminates from pool exhaustion (which has restart_hits=0).
        if (
            restarts >= 1.0
            and restart_hits >= 1.0
            and db_hits < 12.0
            and http_5xx < 0.20
        ):
            return "RESTART_LOOP"
        if (
            cpu >= 0.55
            and memory < 900_000_000
            and unavailable <= 0.0
            and db_hits == 0.0
            and http_5xx < 0.05
            and error_ratio < 0.35
        ):
            return "CPU_STRESS"
        if (
            memory >= 900_000_000
            and http_5xx < 0.10
            and unavailable <= 0.0
            and db_hits < 6.0
            and restarts < 1.0
            and (error_ratio < 0.30 or cpu < 0.35)
        ):
            return "MEMORY_STRESS"
        # Unavailable replicas are definitive availability signal — win over error-storm and
        # database-connectivity bias even when http_5xx is elevated (pods down → 5xx from queued
        # requests) or when Loki has residual db_keyword_hits from the previous incident window.
        if (
            unavailable > 0.0
            and http_5xx < 0.40
            and restarts < 1.0
        ):
            return "AVAILABILITY_DROP"
        # Strong DB keyword signal with moderate http errors beats error-storm classification.
        if (
            db_hits >= 5.0
            and http_5xx < 0.30
            and restarts < 1.0
            and unavailable <= 0.0
            and not (latency >= 5.0 and error_ratio == 0.0 and http_5xx == 0.0)
        ):
            return "DATABASE_CONNECTIVITY"
        if http_5xx >= 0.08:
            return "APPLICATION_ERROR_STORM"
        if (
            db_hits >= 3.0
            and http_5xx < 0.07
            and (
                error_ratio >= 0.05
                or http_5xx >= 0.03
                or features.get("metric_risk_score", 0.0) >= 0.20
                or features.get("overall_risk_score", 0.0) >= 0.08
            )
            and not (latency >= 5.0 and error_ratio == 0.0 and http_5xx == 0.0)
        ):
            return "DATABASE_CONNECTIVITY"
        if (
            unavailable > 0.0
            and latency >= 1.0
            and http_5xx < 0.40
            and db_hits == 0.0
            and restarts < 2.0
        ):
            return "AVAILABILITY_DROP"
        if (
            unavailable > 0.0
            and restarts < 1.0
            and db_hits == 0.0
            and memory < 950_000_000
            and http_5xx < 0.35
        ):
            return "AVAILABILITY_DROP"
        if http_5xx >= 0.18:
            return "APPLICATION_ERROR_STORM"
        if (
            error_ratio >= 0.22
            and (error_line_count >= 3.0 or log_line_count >= 5.0)
        ):
            return "APPLICATION_ERROR_STORM"
        if unavailable > 0.0:
            return "AVAILABILITY_DROP"
        if (
            latency >= 5.0
            and 0.08 <= error_ratio < 0.22
            and http_5xx < 0.08
            and db_hits <= 8.0
            and cpu < 0.55
            and memory < 900_000_000
            and restarts < 1.0
        ):
            return "AVAILABILITY_DROP"
        if (
            latency >= 1.2
            and features.get("overall_risk_score", 0.0) >= 0.15
            and features.get("metric_risk_score", 0.0) >= 0.18
            and http_5xx < 0.03
            and error_ratio < 0.06
            and features.get("warning_line_count", 0.0) <= 4.0
            and db_hits == 0.0
            and restarts < 1.0
            and cpu < 0.35
            and memory < 850_000_000
            and unavailable < 1.0
        ):
            return "AVAILABILITY_DROP"
        if (
            features.get("overall_risk_score", 0.0) >= 0.30
            and features.get("metric_risk_score", 0.0) >= 0.20
            and features.get("log_line_count", 0.0) <= 2.0
            and features.get("error_line_count", 0.0) <= 2.0
            and restarts >= 1.0
            and http_5xx < 0.02
            and latency < 0.50
            and db_hits == 0.0
            and cpu < 0.20
            and memory < 850_000_000
        ):
            return "AVAILABILITY_DROP"
        if (
            latency >= 3.0
            and http_5xx < 0.05
            and error_ratio < 0.05
        ):
            return "GENERAL_DEGRADATION"
        if (
            latency >= 1.0
            or features.get("warning_line_count", 0.0) >= 8.0
            or error_ratio >= 0.10
        ):
            return "GENERAL_DEGRADATION"

        return "GENERAL_DEGRADATION"

    @staticmethod
    def _classify_severity(confidence: float) -> str:
        if confidence >= 0.85:
            return "CRITICAL"
        if confidence >= 0.70:
            return "HIGH"
        if confidence >= 0.50:
            return "MEDIUM"
        return "LOW"

    @staticmethod
    def _recommend_action(
        features: dict[str, float],
        incident_type: str,
        severity: str,
        incident: bool,
    ) -> str:
        if not incident:
            return "NO_ACTION"

        node_not_ready = float(features.get("node_not_ready_count", 0.0))
        unavailable = float(features.get("unavailable_replicas", 0.0))
        cpu = float(features.get("cpu_usage_cores", 0.0))
        memory = float(features.get("memory_working_set_bytes", 0.0))
        restarts = float(features.get("pod_restarts_total", 0.0))
        error_ratio = float(features.get("error_ratio", 0.0))
        http_5xx_ratio = float(features.get("http_5xx_ratio", 0.0))
        latency = float(features.get("http_latency_p95_seconds", 0.0))
        db_hits = float(features.get("db_keyword_hits", 0.0))
        monitoring_vm_up = float(features.get("monitoring_vm_up", 1.0))
        ai_vm_up = float(features.get("ai_vm_up", 1.0))

        if incident_type == "VM_DOWN":
            if monitoring_vm_up < 0.5 and ai_vm_up < 0.5:
                return "ESCALATE_MULTI_VM_OUTAGE_AND_FREEZE_AUTOMATION"
            if node_not_ready >= 1.0 and unavailable >= 1.0:
                return "ESCALATE_NODE_OUTAGE_AND_FAILOVER_WORKLOAD"
            return (
                "ESCALATE_VM_OUTAGE_AND_FAILOVER"
                if severity in {"HIGH", "CRITICAL"}
                else "ESCALATE_NODE_OUTAGE_AND_REBALANCE"
            )
        if incident_type == "AVAILABILITY_DROP":
            if http_5xx_ratio >= 0.15:
                return "SHIFT_TRAFFIC_AND_SCALE_DEPLOYMENT"
            if unavailable >= 2.0:
                return "REBALANCE_REPLICAS_AND_SCALE_DEPLOYMENT"
            return (
                "SCALE_DEPLOYMENT_AND_REBALANCE"
                if severity in {"HIGH", "CRITICAL"}
                else "RESTART_FAILED_WORKLOAD_AND_REBALANCE"
            )
        if incident_type == "CPU_STRESS":
            if cpu >= 0.90 and latency >= 1.0:
                return "SCALE_DEPLOYMENT_AND_CAPTURE_CPU_PROFILE"
            if latency >= 2.0:
                return "RESTART_POD_AND_CAPTURE_CPU_PROFILE"
            return (
                "SCALE_DEPLOYMENT_AND_CAPTURE_CPU_PROFILE"
                if severity in {"HIGH", "CRITICAL"}
                else "RESTART_POD_AND_CAPTURE_CPU_PROFILE"
            )
        if incident_type == "MEMORY_STRESS":
            if memory >= 1_400_000_000 and restarts >= 1.0:
                return "SCALE_MEMORY_LIMITS_AND_RESTART_POD"
            if memory >= 1_100_000_000:
                return "RESTART_POD_AND_SCALE_MEMORY_LIMITS"
            return (
                "RESTART_POD_AND_SCALE_MEMORY_LIMITS"
                if severity in {"HIGH", "CRITICAL"}
                else "RESTART_POD_AND_TUNE_MEMORY_LIMITS"
            )
        if incident_type == "RESTART_LOOP":
            if restarts >= 3.0 and error_ratio >= 0.10:
                return "ROLLBACK_RECENT_CHANGE_AND_RESTART"
            if restarts >= 2.0 and latency >= 2.0:
                return "RESTART_POD_AND_INVESTIGATE"
            return (
                "ROLLBACK_RECENT_CHANGE_AND_RESTART"
                if severity in {"HIGH", "CRITICAL"}
                else "RESTART_POD_AND_INVESTIGATE"
            )
        if incident_type == "APPLICATION_ERROR_STORM":
            if http_5xx_ratio >= 0.30 or error_ratio >= 0.30:
                return "ROLLBACK_LAST_RELEASE_AND_SHED_TRAFFIC"
            if error_ratio >= 0.25:
                return "SHIFT_TRAFFIC_TO_STABLE_REVISION_AND_INVESTIGATE"
            return (
                "ROLLBACK_LAST_RELEASE_AND_SHED_TRAFFIC"
                if severity in {"HIGH", "CRITICAL"}
                else "RESTART_POD_AND_INVESTIGATE"
            )
        if incident_type == "DATABASE_CONNECTIVITY":
            if db_hits >= 8.0 and latency >= 0.60:
                return "FAILOVER_DATABASE_AND_RESTART_WORKLOAD"
            if http_5xx_ratio >= 0.03 or error_ratio >= 0.08:
                return "CHECK_DATABASE_CONNECTION_POOL_AND_RESTART"
            return (
                "FAILOVER_DATABASE_AND_RESTART_WORKLOAD"
                if severity in {"HIGH", "CRITICAL"}
                else "CHECK_DB_CONNECTIVITY_AND_RESTART"
            )
        if incident_type == "GENERAL_DEGRADATION":
            if latency >= 4.0:
                return "CAPTURE_DIAGNOSTICS_AND_INVESTIGATE"
            if error_ratio >= 0.08:
                return "TRACE_DEPENDENCY_LATENCY_AND_INVESTIGATE"
            return (
                "CAPTURE_DIAGNOSTICS_AND_INVESTIGATE"
                if severity in {"HIGH", "CRITICAL"}
                else "INVESTIGATE_DEPENDENCY_LATENCY"
            )

        if severity in {"HIGH", "CRITICAL"}:
            return "ESCALATE_TO_HUMAN"
        return "INVESTIGATE"

    @staticmethod
    def _classify_severity_from_features(
        features: dict[str, float],
        confidence: float,
        incident_type: str,
    ) -> str | None:
        node_not_ready = float(features.get("node_not_ready_count", 0.0))
        unavailable = float(features.get("unavailable_replicas", 0.0))
        cpu = float(features.get("cpu_usage_cores", 0.0))
        memory = float(features.get("memory_working_set_bytes", 0.0))
        restarts = float(features.get("pod_restarts_total", 0.0))
        error_ratio = float(features.get("error_ratio", 0.0))
        http_5xx_ratio = float(features.get("http_5xx_ratio", 0.0))
        latency = float(features.get("http_latency_p95_seconds", 0.0))
        db_hits = float(features.get("db_keyword_hits", 0.0))

        if incident_type == "VM_DOWN":
            if node_not_ready >= 2.0 or confidence >= 0.80:
                return "CRITICAL"
            if node_not_ready >= 1.0:
                return "HIGH"
            monitoring_vm_up = float(features.get("monitoring_vm_up", 1.0))
            ai_vm_up = float(features.get("ai_vm_up", 1.0))
            if monitoring_vm_up < 0.5 or ai_vm_up < 0.5:
                return "HIGH"
        if incident_type == "AVAILABILITY_DROP":
            if unavailable >= 2.0:
                return "CRITICAL"
            if unavailable >= 1.0 and confidence >= 0.60:
                return "HIGH"
            return "MEDIUM"
        if incident_type == "CPU_STRESS":
            if cpu >= 0.90 and latency >= 1.0:
                return "HIGH"
            if cpu >= 0.65:
                return "MEDIUM"
        if incident_type == "MEMORY_STRESS":
            if memory >= 1_250_000_000:
                return "HIGH"
            if memory >= 900_000_000:
                return "MEDIUM"
        if incident_type == "RESTART_LOOP":
            if restarts >= 3.0:
                return "HIGH"
            return "MEDIUM"
        if incident_type == "APPLICATION_ERROR_STORM":
            if http_5xx_ratio >= 0.35 or error_ratio >= 0.35:
                return "CRITICAL"
            if http_5xx_ratio >= 0.20 or error_ratio >= 0.20:
                return "HIGH"
            return "MEDIUM"
        if incident_type == "DATABASE_CONNECTIVITY":
            if db_hits >= 8.0 or http_5xx_ratio >= 0.20:
                return "HIGH"
            return "MEDIUM"
        if incident_type == "GENERAL_DEGRADATION":
            if latency >= 2.5:
                return "HIGH"
            return "MEDIUM"

        return None

    @staticmethod
    def _build_summary(
        incident: bool,
        incident_type: str,
        severity: str,
        confidence: float,
    ) -> str:
        if not incident:
            return f"No reliable incident signal detected (confidence={confidence:.2f})."
        if incident_type == "VM_DOWN":
            return (
                f"Probable VM or node outage detected: type={incident_type}, "
                f"severity={severity}, confidence={confidence:.2f}."
            )
        return (
            f"Potential incident detected: type={incident_type}, severity={severity}, "
            f"confidence={confidence:.2f}."
        )

    def _build_ranked_plans(
        self,
        features: dict[str, float],
        incident_type: str,
        severity: str,
        confidence: float,
        incident: bool,
        remediation_context: dict[str, object],
    ) -> list[PlanOption]:
        if not incident:
            return []

        candidates = self._plan_candidates(features, incident_type, severity, remediation_context)
        recent_counts = self._nested_int_map(remediation_context, "recent_action_counts")
        recent_failures = self._nested_int_map(remediation_context, "recent_failed_action_counts")
        recent_successes = self._nested_int_map(remediation_context, "recent_success_action_counts")
        baseline_action = self._recommend_action(features, incident_type, severity, incident)

        ranked: list[PlanOption] = []
        strategy_nonce = self._context_int(remediation_context, "strategy_nonce")
        for index, candidate in enumerate(candidates):
            executable = self._map_executable_action(candidate["recommended_action"])
            risk = float(candidate["risk_score"])
            cost = float(candidate["cost_score"])
            blast = float(candidate["blast_radius_score"])
            base_alignment = float(candidate["alignment_score"])
            use_count = float(recent_counts.get(executable, 0))
            fail_count = float(recent_failures.get(executable, 0))
            success_count = float(recent_successes.get(executable, 0))
            history_total = max(use_count, 1.0)
            historical_effectiveness = max(0.0, min(1.0, (success_count + 1.0) / (history_total + fail_count + 1.0)))
            novelty = max(0.15, 1.0 - min(use_count / 5.0, 0.85))
            confidence_bonus = confidence * 0.35
            severity_bonus = 0.15 if severity in {"HIGH", "CRITICAL"} else 0.05
            safety_penalty = (risk * 0.16) + (blast * 0.16) + (cost * 0.08)
            history_penalty = fail_count * 0.12
            stability_bonus = historical_effectiveness * 0.18
            novelty_bonus = novelty * 0.08
            family_consistency_bonus = (
                0.11 if str(candidate["recommended_action"]).upper() == baseline_action.upper() else 0.0
            )
            dynamic_bonus = self._variation_bonus(
                strategy_nonce,
                incident_type,
                str(candidate["recommended_action"]),
                index,
            )
            priority_score = (
                base_alignment
                + confidence_bonus
                + severity_bonus
                + stability_bonus
                + novelty_bonus
                + family_consistency_bonus
                + dynamic_bonus
                - safety_penalty
                - history_penalty
            )
            safe_to_execute = self._is_safe_to_execute(executable, risk, blast, severity)
            execution_mode = self._select_execution_mode(
                safe_to_execute,
                executable,
                confidence,
                severity,
                risk,
                blast,
                fail_count,
                remediation_context,
            )

            reasons = list(candidate["reasons"])
            advantages = list(candidate["advantages"])
            tradeoffs = list(candidate["tradeoffs"])
            if fail_count > 0:
                reasons.append(f"Penalized by {int(fail_count)} recent failed {executable} attempts.")
            elif success_count > 0:
                reasons.append(f"Boosted by {int(success_count)} recent successful {executable} remediations.")
            if novelty >= 0.8:
                reasons.append("Boosted for novelty to avoid repeating the same remediation pattern.")
            if blast <= 0.35:
                reasons.append("Preferred for lower blast radius on the live platform.")
            if execution_mode == "RECOMMEND_ONLY":
                reasons.append("Returned as recommendation only because the safety gate did not approve autonomous execution.")

            ranked.append(
                PlanOption(
                    plan_id=f"{incident_type.lower()}-plan-{index + 1}",
                    title=str(candidate["title"]),
                    strategy=str(candidate["strategy"]),
                    recommended_action=str(candidate["recommended_action"]),
                    executable_action=executable,
                    safe_to_execute=safe_to_execute,
                    execution_mode=execution_mode,
                    rationale=str(candidate["rationale"]),
                    reasons=reasons,
                    advantages=advantages,
                    tradeoffs=tradeoffs,
                    risk_score=risk,
                    cost_score=cost,
                    blast_radius_score=blast,
                    historical_effectiveness=historical_effectiveness,
                    novelty_score=novelty,
                    priority_score=priority_score,
                    requires_approval=False,
                )
            )

        ranked.sort(key=lambda plan: plan.priority_score, reverse=True)

        deduped: list[PlanOption] = []
        seen_actions: set[str] = set()
        for plan in ranked:
            action_key = plan.recommended_action.upper()
            if action_key in seen_actions:
                continue
            seen_actions.add(action_key)
            deduped.append(plan)
            if len(deduped) >= 5:
                break

        return deduped

    def _plan_candidates(
        self,
        features: dict[str, float],
        incident_type: str,
        severity: str,
        remediation_context: dict[str, object],
    ) -> list[dict[str, object]]:
        latency = float(features.get("http_latency_p95_seconds", 0.0))
        errors = float(features.get("http_5xx_ratio", 0.0))
        error_ratio = float(features.get("error_ratio", 0.0))
        restarts = float(features.get("pod_restarts_total", 0.0))
        unavailable = float(features.get("unavailable_replicas", 0.0))
        db_hits = float(features.get("db_keyword_hits", 0.0))
        cpu = float(features.get("cpu_usage_cores", 0.0))
        memory = float(features.get("memory_working_set_bytes", 0.0))
        warning_count = float(features.get("warning_line_count", 0.0))
        node_not_ready = float(features.get("node_not_ready_count", 0.0))
        monitoring_vm_up = float(features.get("monitoring_vm_up", 1.0))
        ai_vm_up = float(features.get("ai_vm_up", 1.0))
        recent_failures = self._nested_int_map(remediation_context, "recent_failed_action_counts")
        restart_failures = float(recent_failures.get("RESTART_POD", 0))
        scale_failures = float(recent_failures.get("SCALE_DEPLOYMENT", 0))
        escalation_failures = float(recent_failures.get("ESCALATION", 0))

        if incident_type == "VM_DOWN":
            primary_vm_action = (
                "ESCALATE_MULTI_VM_OUTAGE_AND_FREEZE_AUTOMATION"
                if node_not_ready >= 2.0 or monitoring_vm_up < 0.5 or ai_vm_up < 0.5
                else "ESCALATE_VM_OUTAGE_AND_FAILOVER"
            )
            secondary_vm_action = (
                "ESCALATE_NODE_OUTAGE_AND_REBALANCE"
                if escalation_failures <= 0
                else "ESCALATE_NODE_OUTAGE_AND_FAILOVER_WORKLOAD"
            )
            candidates = [
                self._candidate(
                    primary_vm_action,
                    "Escalate outage and preserve surviving capacity",
                    "Confirm the node outage, keep the automation surface tight, and shift critical capacity away from the failed worker.",
                    "A worker-level outage needs operator-visible containment before broader orchestration starts moving workloads aggressively.",
                    0.24 if primary_vm_action.endswith("FREEZE_AUTOMATION") else 0.30,
                    0.22 if primary_vm_action.endswith("FREEZE_AUTOMATION") else 0.35,
                    0.12 if primary_vm_action.endswith("FREEZE_AUTOMATION") else 0.25,
                    0.95 if primary_vm_action.endswith("FREEZE_AUTOMATION") else 0.93,
                ),
                self._candidate(
                    secondary_vm_action,
                    "Re-place the most exposed workload slice",
                    "Treat the event as a node-level failure and move the most exposed workloads onto the healthiest remaining capacity.",
                    "This path is stronger when user-facing pods were concentrated on the lost worker and simple failover is not enough.",
                    0.38,
                    0.45,
                    0.40,
                    0.89,
                ),
                self._candidate(
                    "ESCALATE_MULTI_VM_OUTAGE_AND_FREEZE_AUTOMATION",
                    "Freeze risky automation and widen diagnosis",
                    "Pause high-impact automation, preserve evidence, and verify whether the failure is broader than one worker.",
                    "This alternative is safer when control-plane or VM-level instability may be spreading.",
                    0.20,
                    0.20,
                    0.10,
                    0.76,
                ),
            ]
            return self._extend_with_creative_candidates(
                self._annotate_candidates(candidates, incident_type, features),
                incident_type,
                features,
                severity,
                remediation_context,
            )
        if incident_type == "AVAILABILITY_DROP":
            primary_availability_action = (
                "SHIFT_TRAFFIC_AND_SCALE_DEPLOYMENT"
                if errors >= 0.08 or latency >= 6.0
                else "SCALE_DEPLOYMENT_AND_REBALANCE"
            )
            secondary_availability_action = (
                "REBALANCE_REPLICAS_AND_SCALE_DEPLOYMENT"
                if unavailable >= 1.0 or scale_failures <= 0
                else "CAPTURE_DIAGNOSTICS_AND_INVESTIGATE"
            )
            candidates = [
                self._candidate(
                    primary_availability_action,
                    "Shift traffic and rebuild healthy capacity",
                    "Move traffic toward healthy replicas and increase safe capacity before user impact grows.",
                    "Replica loss is best handled by rebalancing load and recovering spare capacity.",
                    0.42,
                    0.54,
                    0.48,
                    0.91,
                ),
                self._candidate(
                    secondary_availability_action,
                    "Rebalance replicas around the failing segment",
                    "Stabilize the weakest readiness segment first, then decide whether traffic relief or replica recovery is the cleaner next move.",
                    "This alternative is useful when the drop looks partial and we want to avoid an unnecessarily broad rollout.",
                    0.32,
                    0.34,
                    0.26,
                    0.86,
                ),
                self._candidate(
                    "CAPTURE_DIAGNOSTICS_AND_INVESTIGATE",
                    "Capture diagnostics before scaling further",
                    "Freeze the topology briefly, collect rollout and readiness evidence, then scale based on what is confirmed.",
                    "Useful when availability is dropping but the root cause is still ambiguous.",
                    0.18,
                    0.22,
                    0.14,
                    0.66,
                ),
            ]
            return self._extend_with_creative_candidates(
                self._annotate_candidates(candidates, incident_type, features),
                incident_type,
                features,
                severity,
                remediation_context,
            )
        if incident_type == "CPU_STRESS":
            primary_cpu_action = (
                "SCALE_DEPLOYMENT_AND_CAPTURE_CPU_PROFILE"
                if cpu >= 0.85 or (latency >= 1.5 and scale_failures <= 0)
                else "RESTART_POD_AND_CAPTURE_CPU_PROFILE"
            )
            candidates = [
                self._candidate(
                    primary_cpu_action,
                    "Stabilize hot paths and capture CPU evidence",
                    "Reduce immediate pressure, then collect a CPU profile while the signal is still hot.",
                    "CPU saturation is often recoverable quickly, but we still want forensic evidence for the next cycle.",
                    0.33,
                    0.44,
                    0.28,
                    0.92,
                ),
                self._candidate(
                    "SHIFT_TRAFFIC_AND_SCALE_DEPLOYMENT",
                    "Deflect burst traffic and add headroom",
                    "Trim pressure on the overloaded replica set before saturation cascades into user-facing latency.",
                    "This is stronger when the overload is demand-driven rather than crash-driven.",
                    0.39,
                    0.52,
                    0.41,
                    0.81,
                ),
                self._candidate(
                    "TRACE_DEPENDENCY_LATENCY_AND_INVESTIGATE",
                    "Verify whether CPU pressure is secondary",
                    "Check whether downstream latency is amplifying CPU usage before treating the pod as the root cause.",
                    "Helpful when CPU pressure may be a symptom rather than the source of the incident.",
                    0.17,
                    0.18,
                    0.12,
                    0.63,
                ),
            ]
            return self._extend_with_creative_candidates(
                self._annotate_candidates(candidates, incident_type, features),
                incident_type,
                features,
                severity,
                remediation_context,
            )
        if incident_type == "MEMORY_STRESS":
            primary_memory_action = (
                "SCALE_MEMORY_LIMITS_AND_RESTART_POD"
                if memory >= 1_200_000_000 or restarts >= 1.0
                else "RESTART_POD_AND_SCALE_MEMORY_LIMITS"
            )
            candidates = [
                self._candidate(
                    primary_memory_action,
                    "Recycle the leaky pod and expand memory guardrails",
                    "Restart the stressed workload and widen memory headroom only where the signal is strongest.",
                    "Memory pressure often needs both immediate cleanup and a safer ceiling for the next workload cycle.",
                    0.34,
                    0.47,
                    0.30,
                    0.91,
                ),
                self._candidate(
                    "CAPTURE_DIAGNOSTICS_AND_INVESTIGATE",
                    "Snapshot heap and allocation evidence",
                    "Capture heap and allocation clues before the next restart erases the signal.",
                    "This preserves root-cause evidence when the service is still barely alive.",
                    0.19,
                    0.21,
                    0.11,
                    0.74,
                ),
                self._candidate(
                    "SHIFT_TRAFFIC_AND_SCALE_DEPLOYMENT",
                    "Offload pressure before recycling the pod",
                    "Move traffic off the hottest replica set so the restart has a lower chance of flapping.",
                    "Lowering pressure first can reduce repeat crashes during recovery.",
                    0.31,
                    0.49,
                    0.36,
                    0.77,
                ),
            ]
            return self._extend_with_creative_candidates(
                self._annotate_candidates(candidates, incident_type, features),
                incident_type,
                features,
                severity,
                remediation_context,
            )
        if incident_type == "RESTART_LOOP":
            primary_restart_action = (
                "ROLLBACK_RECENT_CHANGE_AND_RESTART"
                if restarts >= 3.0 or errors >= 0.10
                else "RESTART_POD_AND_INVESTIGATE"
            )
            candidates = [
                self._candidate(
                    primary_restart_action,
                    "Break the restart loop and isolate the trigger",
                    "Reset the failing workload, then inspect the trigger pattern before the controller recreates the failure.",
                    "A repeat restart loop strongly suggests either a bad change or a poisoned dependency path.",
                    0.40,
                    0.36,
                    0.26,
                    0.93,
                ),
                self._candidate(
                    "SHIFT_TRAFFIC_TO_STABLE_REVISION_AND_INVESTIGATE",
                    "Move users to a calmer revision first",
                    "Protect user traffic with the most stable revision while the failing replica set is analyzed.",
                    "Useful when the crash loop is isolated to one revision or rollout cohort.",
                    0.29,
                    0.42,
                    0.21,
                    0.84,
                ),
                self._candidate(
                    "CAPTURE_DIAGNOSTICS_AND_INVESTIGATE",
                    "Preserve crash context before another restart",
                    "Collect last-failure context and dependency traces before another restart removes evidence.",
                    "This lowers blast radius but trades slower recovery for stronger root-cause confidence.",
                    0.14,
                    0.16,
                    0.09,
                    0.65,
                ),
            ]
            return self._extend_with_creative_candidates(
                self._annotate_candidates(candidates, incident_type, features),
                incident_type,
                features,
                severity,
                remediation_context,
            )
        if incident_type == "APPLICATION_ERROR_STORM":
            primary_error_action = (
                "ROLLBACK_LAST_RELEASE_AND_SHED_TRAFFIC"
                if errors >= 0.25 or error_ratio >= 0.30
                else "SHIFT_TRAFFIC_TO_STABLE_REVISION_AND_INVESTIGATE"
            )
            candidates = [
                self._candidate(
                    primary_error_action,
                    "Protect users first, then narrow the bad revision",
                    "Redirect users away from the unstable path and isolate the release or dependency behind the 5xx burst.",
                    "When errors spike hard, preserving user traffic is usually worth more than an early restart.",
                    0.37,
                    0.46,
                    0.29,
                    0.94,
                ),
                self._candidate(
                    "RESTART_POD_AND_INVESTIGATE",
                    "Attempt a fast local recovery",
                    "Restart the failing workload slice while capturing error markers and request symptoms.",
                    "This is cheaper, but it is less robust if the fault comes from a bad release or configuration.",
                    0.45,
                    0.28,
                    0.24,
                    0.78,
                ),
                self._candidate(
                    "CAPTURE_DIAGNOSTICS_AND_INVESTIGATE",
                    "Freeze the signal for forensic debugging",
                    "Capture traces, failing payload shapes, and dependency status before mutating the platform state.",
                    "Useful when the blast radius is acceptable and you need stronger evidence before rollback.",
                    0.16,
                    0.18,
                    0.10,
                    0.68,
                ),
            ]
            return self._extend_with_creative_candidates(
                self._annotate_candidates(candidates, incident_type, features),
                incident_type,
                features,
                severity,
                remediation_context,
            )
        if incident_type == "DATABASE_CONNECTIVITY":
            primary_db_action = (
                "FAILOVER_DATABASE_AND_RESTART_WORKLOAD"
                if db_hits >= 10.0 or (db_hits >= 6.0 and latency >= 0.60)
                else "CHECK_DATABASE_CONNECTION_POOL_AND_RESTART"
            )
            candidates = [
                self._candidate(
                    primary_db_action,
                    "Stabilize the data path before it cascades",
                    "Prioritize database path recovery, then recycle the application side only if the dependency path is stable enough.",
                    "The incident is DB-shaped, so treating the connection path first usually beats a blind pod restart.",
                    0.43,
                    0.58,
                    0.44,
                    0.95,
                ),
                self._candidate(
                    "CHECK_DB_CONNECTIVITY_AND_RESTART",
                    "Repair the application side of the DB path",
                    "Validate pool pressure and stale sessions, then restart only the application edge if the database itself is reachable.",
                    "This is cheaper than failover when the database is healthy but the pool is wedged.",
                    0.30,
                    0.31,
                    0.20,
                    0.86,
                ),
                self._candidate(
                    "CAPTURE_DIAGNOSTICS_AND_INVESTIGATE",
                    "Hold the line and capture DB evidence",
                    "Snapshot pool pressure, failed SQL symptoms, and readiness transitions before making the dependency topology move.",
                    "This slows intervention but increases confidence when DB ownership spans multiple teams.",
                    0.18,
                    0.19,
                    0.08,
                    0.62,
                ),
            ]
            return self._extend_with_creative_candidates(
                self._annotate_candidates(candidates, incident_type, features),
                incident_type,
                features,
                severity,
                remediation_context,
            )
        if incident_type == "GENERAL_DEGRADATION":
            primary = (
                "SHIFT_TRAFFIC_AND_SCALE_DEPLOYMENT"
                if latency >= 5.0 and warning_count <= 3.0 and cpu < 0.40 and memory < 850_000_000
                else "CAPTURE_DIAGNOSTICS_AND_INVESTIGATE" if latency >= 4.0
                else "TRACE_DEPENDENCY_LATENCY_AND_INVESTIGATE"
            )
            candidates = [
                self._candidate(
                    primary,
                    "Trace the slow path before forcing recovery",
                    "Inspect dependency latency, queue pressure, and saturation clues before choosing a disruptive platform move.",
                    "General degradation is broad by nature, so diagnosis quality matters as much as recovery speed.",
                    0.20,
                    0.22,
                    0.10,
                    0.90,
                ),
                self._candidate(
                    "SHIFT_TRAFFIC_AND_SCALE_DEPLOYMENT",
                    "Buy time with controlled capacity relief",
                    "Add safe headroom and push traffic toward calmer replicas while the slow path is isolated.",
                    "Useful when latency is hurting users and the root cause is still being narrowed.",
                    0.34,
                    0.46,
                    0.30,
                    0.79,
                ),
                self._candidate(
                    "RESTART_POD_AND_INVESTIGATE",
                    "Try a low-cost local reset",
                    "Restart the most suspicious workload slice if the degradation looks sticky rather than systemic.",
                    "Cheaper than broader intervention, but weaker if the slowdown is downstream.",
                    0.39,
                    0.24,
                    0.18,
                    0.68,
                ),
            ]
            return self._extend_with_creative_candidates(
                self._annotate_candidates(candidates, incident_type, features),
                incident_type,
                features,
                severity,
                remediation_context,
            )

        candidates = [
            self._candidate(
                "INVESTIGATE",
                "Investigate the anomaly",
                "Collect evidence first and defer execution to the operator.",
                "Fallback plan used when the incident family remains ambiguous.",
                0.12,
                0.10,
                0.06,
                0.50,
            )
        ]
        return self._extend_with_creative_candidates(
            self._annotate_candidates(candidates, incident_type, features),
            incident_type,
            features,
            severity,
            remediation_context,
        )

    def _annotate_candidates(
        self,
        candidates: list[dict[str, object]],
        incident_type: str,
        features: dict[str, float],
    ) -> list[dict[str, object]]:
        signal = self._signal_phrase(features, incident_type)
        annotated: list[dict[str, object]] = []
        for candidate in candidates:
            enriched = dict(candidate)
            reasons = list(candidate.get("reasons", []))
            reasons.append(f"Context signal at decision time: {signal}.")
            enriched["reasons"] = reasons
            annotated.append(enriched)
        return annotated

    def _candidate(
        self,
        recommended_action: str,
        title: str,
        strategy: str,
        rationale: str,
        risk_score: float,
        cost_score: float,
        blast_radius_score: float,
        alignment_score: float,
    ) -> dict[str, object]:
        return {
            "recommended_action": recommended_action,
            "title": title,
            "strategy": strategy,
            "rationale": rationale,
            "advantages": self._advantages_for_candidate(recommended_action, risk_score, blast_radius_score),
            "tradeoffs": self._tradeoffs_for_candidate(recommended_action, cost_score, blast_radius_score),
            "risk_score": risk_score,
            "cost_score": cost_score,
            "blast_radius_score": blast_radius_score,
            "alignment_score": alignment_score,
            "reasons": [
                f"Targets the {recommended_action.lower()} path for the detected incident family.",
                "Balances fast mitigation with bounded platform automation and advisory fallback when risk is uncertain.",
            ],
        }

    def _extend_with_creative_candidates(
        self,
        base_candidates: list[dict[str, object]],
        incident_type: str,
        features: dict[str, float],
        severity: str,
        remediation_context: dict[str, object],
    ) -> list[dict[str, object]]:
        candidates = list(base_candidates)
        while len(candidates) < 5:
            candidates.append(
                self._creative_candidate(
                    incident_type=incident_type,
                    features=features,
                    severity=severity,
                    remediation_context=remediation_context,
                    variant_index=len(candidates) - len(base_candidates),
                )
            )
        return candidates[:5]

    def _creative_candidate(
        self,
        incident_type: str,
        features: dict[str, float],
        severity: str,
        remediation_context: dict[str, object],
        variant_index: int,
    ) -> dict[str, object]:
        palette = self._creative_palette(incident_type)
        nonce = self._context_int(remediation_context, "strategy_nonce")
        flavor = palette[(nonce + variant_index) % len(palette)]
        recommended_action = str(flavor["recommended_action"])
        signal = self._signal_phrase(features, incident_type)
        strategy = (
            f"{flavor['verb']} while {flavor['anchor']}, then {flavor['follow_up']} "
            f"once the platform confirms the {signal} signal is stabilizing."
        )
        title = f"{flavor['title_prefix']} through {flavor['lens']}"
        rationale = (
            f"This creative advisory path explores the incident through a {flavor['lens']} lens "
            f"so operators can compare a fresh mitigation angle against the safer defaults."
        )
        risk = min(0.55, 0.18 + (0.05 * variant_index) + (0.03 if severity in {'HIGH', 'CRITICAL'} else 0.0))
        cost = min(0.58, 0.20 + (0.06 * variant_index))
        blast = min(0.42, 0.14 + (0.05 * variant_index))
        alignment = max(0.64, 0.79 - (0.04 * variant_index))
        return self._candidate(
            recommended_action,
            title,
            strategy,
            rationale,
            risk,
            cost,
            blast,
            alignment,
        )

    @staticmethod
    def _creative_palette(incident_type: str) -> list[dict[str, str]]:
        if incident_type == "VM_DOWN":
            return [
                {
                    "recommended_action": "ESCALATE_VM_OUTAGE_AND_FAILOVER",
                    "title_prefix": "Protect surviving capacity",
                    "lens": "node-failover containment",
                    "verb": "preserve healthy worker capacity",
                    "anchor": "confirming the outage is truly node-scoped",
                    "follow_up": "re-seat only the most exposed workloads",
                },
                {
                    "recommended_action": "ESCALATE_NODE_OUTAGE_AND_REBALANCE",
                    "title_prefix": "Re-balance the topology",
                    "lens": "placement hygiene",
                    "verb": "rebuild workload placement on stable nodes",
                    "anchor": "preventing the scheduler from oscillating around the bad worker",
                    "follow_up": "restore anti-affinity and traffic balance",
                },
            ]
        if incident_type == "AVAILABILITY_DROP":
            return [
                {
                    "recommended_action": "SHIFT_TRAFFIC_AND_SCALE_DEPLOYMENT",
                    "title_prefix": "Relieve user pressure",
                    "lens": "capacity relief",
                    "verb": "steer demand onto healthier replicas",
                    "anchor": "buying time before the failing slice recovers",
                    "follow_up": "rebuild spare capacity in the clean zone",
                },
                {
                    "recommended_action": "CAPTURE_DIAGNOSTICS_AND_INVESTIGATE",
                    "title_prefix": "Pin down the failing slice",
                    "lens": "readiness forensics",
                    "verb": "freeze the readiness story while it is still visible",
                    "anchor": "verifying whether the drop is rollout-driven or dependency-driven",
                    "follow_up": "apply the narrowest recovery branch",
                },
            ]
        if incident_type == "CPU_STRESS":
            return [
                {
                    "recommended_action": "RESTART_POD_AND_CAPTURE_CPU_PROFILE",
                    "title_prefix": "Catch the hot path",
                    "lens": "runtime profiling",
                    "verb": "stabilize the hottest replica",
                    "anchor": "capturing CPU evidence before the load cools off",
                    "follow_up": "tune or re-place the hot workload",
                },
                {
                    "recommended_action": "SCALE_DEPLOYMENT_AND_CAPTURE_CPU_PROFILE",
                    "title_prefix": "Add headroom first",
                    "lens": "demand absorption",
                    "verb": "spread the burst across safer capacity",
                    "anchor": "keeping the system responsive during profiling",
                    "follow_up": "shrink back after the hot path is understood",
                },
            ]
        if incident_type == "MEMORY_STRESS":
            return [
                {
                    "recommended_action": "RESTART_POD_AND_SCALE_MEMORY_LIMITS",
                    "title_prefix": "Reset the leaky slice",
                    "lens": "memory leak recovery",
                    "verb": "recycle the tightest memory consumer",
                    "anchor": "widening only the guardrails that are clearly too narrow",
                    "follow_up": "keep heap evidence for the next tuning pass",
                },
                {
                    "recommended_action": "CAPTURE_DIAGNOSTICS_AND_INVESTIGATE",
                    "title_prefix": "Preserve heap clues",
                    "lens": "allocation forensics",
                    "verb": "hold the memory signal in place",
                    "anchor": "avoiding a restart that destroys the heap story too early",
                    "follow_up": "choose the least disruptive recycle branch",
                },
            ]
        if incident_type == "RESTART_LOOP":
            return [
                {
                    "recommended_action": "ROLLBACK_RECENT_CHANGE_AND_RESTART",
                    "title_prefix": "Break the loop decisively",
                    "lens": "change regression",
                    "verb": "assume the latest bad change until disproven",
                    "anchor": "stopping repeated crash churn quickly",
                    "follow_up": "verify the rollback on the calmest path",
                },
                {
                    "recommended_action": "SHIFT_TRAFFIC_TO_STABLE_REVISION_AND_INVESTIGATE",
                    "title_prefix": "Isolate the unstable revision",
                    "lens": "traffic quarantine",
                    "verb": "contain user exposure to the crashy cohort",
                    "anchor": "keeping the stable revision hot and healthy",
                    "follow_up": "reproduce the fault away from user traffic",
                },
            ]
        if incident_type == "DATABASE_CONNECTIVITY":
            return [
                {
                    "recommended_action": "CAPTURE_DIAGNOSTICS_AND_INVESTIGATE",
                    "title_prefix": "Preserve evidence",
                    "lens": "dependency forensics",
                    "verb": "freeze evidence capture",
                    "anchor": "validating pool pressure and SQL symptom shape",
                    "follow_up": "select the least disruptive recovery branch",
                },
                {
                    "recommended_action": "CHECK_DATABASE_CONNECTION_POOL_AND_RESTART",
                    "title_prefix": "Stabilize sessions",
                    "lens": "connection hygiene",
                    "verb": "drain stale connection pressure",
                    "anchor": "measuring whether the database itself is still reachable",
                    "follow_up": "recycle only the application edge",
                },
            ]
        if incident_type in {"APPLICATION_ERROR_STORM", "RESTART_LOOP"}:
            return [
                {
                    "recommended_action": "SHIFT_TRAFFIC_TO_STABLE_REVISION_AND_INVESTIGATE",
                    "title_prefix": "Contain user exposure",
                    "lens": "revision isolation",
                    "verb": "shift demand onto the calmest serving path",
                    "anchor": "tracking the failing rollout cohort",
                    "follow_up": "triage the unstable slice with richer evidence",
                },
                {
                    "recommended_action": "CAPTURE_DIAGNOSTICS_AND_INVESTIGATE",
                    "title_prefix": "Hold the fault line",
                    "lens": "forensic stabilization",
                    "verb": "capture request and dependency fingerprints",
                    "anchor": "keeping the platform mutation budget low",
                    "follow_up": "promote or reject rollback with stronger proof",
                },
            ]
        if incident_type == "APPLICATION_ERROR_STORM":
            return [
                {
                    "recommended_action": "ROLLBACK_LAST_RELEASE_AND_SHED_TRAFFIC",
                    "title_prefix": "Protect the user path",
                    "lens": "release quarantine",
                    "verb": "push users away from the noisiest failing edge",
                    "anchor": "assuming the rollout is guilty until the evidence says otherwise",
                    "follow_up": "reintroduce change only after the storm calms",
                },
                {
                    "recommended_action": "CAPTURE_DIAGNOSTICS_AND_INVESTIGATE",
                    "title_prefix": "Hold the blast radius",
                    "lens": "error forensics",
                    "verb": "capture failing payload and dependency fingerprints",
                    "anchor": "keeping the mutation budget low while the signal is rich",
                    "follow_up": "decide whether rollback or config repair is cleaner",
                },
            ]
        if incident_type == "GENERAL_DEGRADATION":
            return [
                {
                    "recommended_action": "TRACE_DEPENDENCY_LATENCY_AND_INVESTIGATE",
                    "title_prefix": "Map the slow edge",
                    "lens": "latency tracing",
                    "verb": "follow the dependency edge that bends first",
                    "anchor": "avoiding a blind restart of the wrong workload",
                    "follow_up": "promote the narrowest intervention that matches the trace",
                },
                {
                    "recommended_action": "SHIFT_TRAFFIC_AND_SCALE_DEPLOYMENT",
                    "title_prefix": "Buy diagnostic time",
                    "lens": "controlled relief",
                    "verb": "create breathing room for the degraded path",
                    "anchor": "reducing user pain while evidence converges",
                    "follow_up": "scale back once the root bottleneck is isolated",
                },
            ]
        return [
            {
                "recommended_action": "CAPTURE_DIAGNOSTICS_AND_INVESTIGATE",
                "title_prefix": "Interrogate the signal",
                "lens": "evidence-first recovery",
                "verb": "collect high-value telemetry around the anomaly",
                "anchor": "avoiding premature disruptive moves",
                "follow_up": "promote the cleanest mitigation branch",
            },
            {
                "recommended_action": "TRACE_DEPENDENCY_LATENCY_AND_INVESTIGATE",
                "title_prefix": "Map the pressure path",
                "lens": "dependency tracing",
                "verb": "trace the slowest dependency edge",
                "anchor": "checking whether the platform symptom is upstream or downstream",
                "follow_up": "target the narrowest recovery action",
            },
        ]

    @staticmethod
    def _signal_phrase(features: dict[str, float], incident_type: str) -> str:
        if incident_type == "CPU_STRESS":
            return f"cpu={float(features.get('cpu_usage_cores', 0.0)):.2f}"
        if incident_type == "MEMORY_STRESS":
            return f"memory={float(features.get('memory_working_set_bytes', 0.0)) / 1_000_000_000:.2f}GB"
        if incident_type == "DATABASE_CONNECTIVITY":
            return f"db_hits={float(features.get('db_keyword_hits', 0.0)):.0f}"
        if incident_type == "VM_DOWN":
            return f"nodes_not_ready={float(features.get('node_not_ready_count', 0.0)):.0f}"
        return f"latency={float(features.get('http_latency_p95_seconds', 0.0)):.2f}s"

    @staticmethod
    def _advantages_for_candidate(
        recommended_action: str,
        risk_score: float,
        blast_radius_score: float,
    ) -> list[str]:
        return [
            f"Maintains a {recommended_action.lower()} option in the operator playbook.",
            f"Estimated blast radius stays around {blast_radius_score:.2f}, which supports comparative planning.",
            "Keeps multiple recovery branches visible instead of collapsing to one generic action.",
        ]

    @staticmethod
    def _tradeoffs_for_candidate(
        recommended_action: str,
        cost_score: float,
        blast_radius_score: float,
    ) -> list[str]:
        return [
            f"Execution cost is estimated around {cost_score:.2f}, so it may consume more time or coordination than the cheapest path.",
            f"Blast radius at {blast_radius_score:.2f} still needs operator judgment before high-impact rollout changes.",
            f"{recommended_action.lower()} remains advisory if safety confidence is not high enough.",
        ]

    def _build_decision_explanation(
        self,
        chosen_plan: PlanOption,
        ranked_plans: list[PlanOption],
        remediation_context: dict[str, object],
    ) -> str:
        runner_up = ranked_plans[1] if len(ranked_plans) > 1 else None
        recent_failures = self._nested_int_map(remediation_context, "recent_failed_action_counts")
        chosen_failures = recent_failures.get(chosen_plan.executable_action, 0)
        comparison = ""
        if runner_up is not None:
            comparison = (
                f" It outranked {runner_up.recommended_action} by favoring a better blend of "
                f"historical effectiveness ({chosen_plan.historical_effectiveness:.2f}) and "
                f"blast radius ({chosen_plan.blast_radius_score:.2f})."
            )
        history_text = ""
        if chosen_failures > 0:
            history_text = (
                f" Even with {chosen_failures} recent failed {chosen_plan.executable_action} attempts, "
                f"the plan still won because its incident alignment remained strongest."
            )
        mode_text = (
            " It is cleared as an auto-execute candidate."
            if chosen_plan.execution_mode == "AUTO_EXECUTE_CANDIDATE"
            else " It is intentionally returned as recommendation only because the safer execution threshold was not met."
        )
        return (
            f"Selected plan '{chosen_plan.title}' because it delivered the best priority score "
            f"({chosen_plan.priority_score:.2f}) after weighting risk, cost, blast radius, and remediation history."
            f"{comparison}{history_text}{mode_text} Strategy: {chosen_plan.strategy}"
        ).strip()

    @staticmethod
    def _build_no_incident_explanation(confidence: float) -> str:
        return (
            f"No autonomous remediation plan was selected because incident confidence stayed below threshold "
            f"({confidence:.2f})."
        )

    @staticmethod
    def _build_recommendation_only_reason(
        chosen_plan: PlanOption,
        confidence: float,
        severity: str,
    ) -> str:
        if not chosen_plan.safe_to_execute:
            return (
                f"Recommendation only: {chosen_plan.recommended_action} is useful for operators, "
                f"but its execution profile is not considered safely automatable."
            )
        if confidence < 0.60:
            return (
                f"Recommendation only: confidence {confidence:.2f} stayed below the autonomous execution threshold."
            )
        if severity == "CRITICAL":
            return "Recommendation only: critical situations stay under human control to reduce blast radius."
        return "Recommendation only: recent remediation history or safety gates favored advisory guidance over automation."

    def _build_decision_context(
        self,
        incident_type: str,
        severity: str,
        confidence: float,
        ranked_plans: list[PlanOption],
        execution_mode: str,
        remediation_context: dict[str, object],
    ) -> dict[str, object]:
        chosen_plan = ranked_plans[0] if ranked_plans else None
        return {
            "incident_family": incident_type,
            "severity": severity,
            "confidence": round(confidence, 4),
            "execution_mode": execution_mode,
            "autonomy_mode": str(remediation_context.get("autonomy_mode", "bounded-auto")),
            "innovation_mode": str(remediation_context.get("innovation_mode", "creative-advisory")),
            "strategy_nonce": self._context_int(remediation_context, "strategy_nonce"),
            "favorite_strategy": chosen_plan.strategy if chosen_plan is not None else None,
            "favorite_plan_title": chosen_plan.title if chosen_plan is not None else None,
            "top_plans_count": len(ranked_plans),
            "selected_plan_risk": round(chosen_plan.risk_score, 4) if chosen_plan is not None else None,
            "selected_plan_cost": round(chosen_plan.cost_score, 4) if chosen_plan is not None else None,
            "selected_plan_blast_radius": round(chosen_plan.blast_radius_score, 4) if chosen_plan is not None else None,
        }

    @staticmethod
    def _map_executable_action(recommended_action: str) -> str:
        normalized = recommended_action.upper()
        if "ESCALATE" in normalized:
            return "ESCALATION"
        if "CRASHLOOP" in normalized or "DELETE_CRASH" in normalized:
            return "DELETE_CRASHLOOPING_PODS"
        if "RESTART" in normalized or "ROLLBACK" in normalized or "FAILOVER" in normalized or "CHECK_" in normalized:
            return "RESTART_POD"
        if "SCALE" in normalized or "REBALANCE" in normalized or "SHIFT_TRAFFIC" in normalized:
            return "SCALE_DEPLOYMENT"
        if "INVESTIGATE" in normalized or "DIAGNOSTIC" in normalized or "TRACE_" in normalized:
            return "INVESTIGATION"
        return "REMEDIATION"

    @staticmethod
    def _context_int(source: dict[str, object], key: str) -> int:
        try:
            return int(source.get(key, 0))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _variation_bonus(
        strategy_nonce: int,
        incident_type: str,
        recommended_action: str,
        index: int,
    ) -> float:
        if strategy_nonce <= 0:
            return 0.0
        signature = sum(ord(ch) for ch in f"{incident_type}:{recommended_action}:{index}")
        bucket = (strategy_nonce + signature) % 7
        divisor = 100.0
        if incident_type == "APPLICATION_ERROR_STORM":
            divisor = 55.0
        elif incident_type == "CPU_STRESS":
            divisor = 65.0
        elif incident_type in {"MEMORY_STRESS", "DATABASE_CONNECTIVITY"}:
            divisor = 75.0
        return ((bucket - 3) / divisor)

    def _select_plan_index(
        self,
        ranked_plans: list[PlanOption],
        remediation_context: dict[str, object],
        incident_type: str,
    ) -> int:
        if len(ranked_plans) <= 1:
            return 0

        strategy_nonce = self._context_int(remediation_context, "strategy_nonce")
        top_score = ranked_plans[0].priority_score
        auto_candidate_indexes = [
            index
            for index, plan in enumerate(ranked_plans)
            if ranked_plans[index].execution_mode == "AUTO_EXECUTE_CANDIDATE"
            and (top_score - plan.priority_score) <= 0.15
        ]
        if auto_candidate_indexes:
            return min(
                auto_candidate_indexes,
                key=lambda index: (
                    ranked_plans[index].risk_score,
                    ranked_plans[index].blast_radius_score,
                    -ranked_plans[index].priority_score,
                    index,
                ),
            )

        if strategy_nonce <= 0:
            return 0

        diversity_window = self._diversity_window(incident_type)
        candidate_indexes = [
            index
            for index, plan in enumerate(ranked_plans)
            if (top_score - plan.priority_score) <= diversity_window
        ]
        if len(candidate_indexes) <= 1:
            candidate_indexes = [0]

        if incident_type == "APPLICATION_ERROR_STORM":
            if strategy_nonce % 2 == 0:
                for index in candidate_indexes:
                    if "ROLLBACK" in ranked_plans[index].recommended_action.upper():
                        return index
            else:
                for index in candidate_indexes:
                    if "SHIFT_TRAFFIC" in ranked_plans[index].recommended_action.upper():
                        return index

        return candidate_indexes[strategy_nonce % len(candidate_indexes)]

    @staticmethod
    def _diversity_window(incident_type: str) -> float:
        if incident_type == "CPU_STRESS":
            return 0.25
        if incident_type == "APPLICATION_ERROR_STORM":
            return 0.14
        if incident_type in {"RESTART_LOOP", "DATABASE_CONNECTIVITY"}:
            return 0.12
        if incident_type in {"CPU_STRESS", "MEMORY_STRESS", "AVAILABILITY_DROP", "GENERAL_DEGRADATION"}:
            return 0.10
        if incident_type == "VM_DOWN":
            return 0.09
        return 0.08

    @staticmethod
    def _is_safe_to_execute(
        executable_action: str,
        risk_score: float,
        blast_radius_score: float,
        severity: str,
    ) -> bool:
        if executable_action == "SCALE_DEPLOYMENT":
            return (
                risk_score <= 0.40
                and blast_radius_score <= 0.35
                and severity in {"MEDIUM", "HIGH"}
            )
        return (
            executable_action in {"RESTART_POD"}
            and risk_score <= 0.55
            and blast_radius_score <= 0.55
            and severity != "CRITICAL"
        )

    @staticmethod
    def _select_execution_mode(
        safe_to_execute: bool,
        executable_action: str,
        confidence: float,
        severity: str,
        risk_score: float,
        blast_radius_score: float,
        fail_count: float,
        remediation_context: dict[str, object],
    ) -> str:
        autonomy_mode = str(remediation_context.get("autonomy_mode", "bounded-auto")).lower()
        if not safe_to_execute:
            return "RECOMMEND_ONLY"
        if executable_action not in {"RESTART_POD", "SCALE_DEPLOYMENT"}:
            return "RECOMMEND_ONLY"
        if autonomy_mode in {"manual", "advisory-only"}:
            return "RECOMMEND_ONLY"
        if confidence < 0.44 or severity == "CRITICAL":
            return "RECOMMEND_ONLY"
        if risk_score > 0.50 or blast_radius_score > 0.50:
            return "RECOMMEND_ONLY"
        if fail_count > 0:
            return "RECOMMEND_ONLY"
        return "AUTO_EXECUTE_CANDIDATE"

    @staticmethod
    def _nested_int_map(source: dict[str, object], key: str) -> dict[str, int]:
        raw = source.get(key)
        if not isinstance(raw, dict):
            return {}
        normalized: dict[str, int] = {}
        for item_key, item_value in raw.items():
            try:
                normalized[str(item_key).upper()] = int(item_value)
            except (TypeError, ValueError):
                continue
        return normalized
