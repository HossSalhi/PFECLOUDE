import numpy as np

from app.models.inference import InferenceEngine
from app.models.loader import ModelBundle


class FixedIncidentModel:
    def __init__(self, score: float) -> None:
        self.score = score

    def predict_proba(self, vector):
        return np.array([[1.0 - self.score, self.score]], dtype=float)


def build_engine(model_score: float | None = None) -> InferenceEngine:
    incident_model = FixedIncidentModel(model_score) if model_score is not None else None
    # Keyword args so the test stays robust to ModelBundle field changes
    # (gained scenario_model/scenario_labels when the 52-class scenario classifier landed).
    return InferenceEngine(model_bundle=ModelBundle(
        incident_model=incident_model,
        severity_model=None,
        action_model=None,
        scenario_model=None,
        feature_order=None,
        severity_labels=None,
        action_labels=None,
        scenario_labels=None,
    ))


def test_vm_down_is_classified_and_escalated() -> None:
    engine = build_engine()

    result = engine.predict(
        {
            "overall_risk_score": 0.36,
            "node_not_ready_count": 1.0,
            "monitoring_vm_up": 1.0,
            "ai_vm_up": 1.0,
            "unavailable_replicas": 0.0,
            "cpu_usage_cores": 0.0,
            "memory_working_set_bytes": 0.0,
        }
    )

    assert result.incident is True
    assert result.incident_type == "VM_DOWN"
    assert result.recommended_action == "ESCALATE_VM_OUTAGE_AND_FAILOVER"
    assert result.execution_mode == "RECOMMEND_ONLY"
    assert result.chosen_plan_id is not None
    assert result.top_recommended_plans[0]["recommended_action"] == "ESCALATE_VM_OUTAGE_AND_FAILOVER"


def test_cpu_and_memory_stress_have_different_actions() -> None:
    engine = build_engine()

    cpu_result = engine.predict(
        {
            "overall_risk_score": 0.52,
            "cpu_usage_cores": 0.72,
            "memory_working_set_bytes": 700_000_000.0,
            "node_not_ready_count": 0.0,
            "monitoring_vm_up": 1.0,
            "ai_vm_up": 1.0,
        }
    )
    memory_result = engine.predict(
        {
            "overall_risk_score": 0.52,
            "cpu_usage_cores": 0.20,
            "memory_working_set_bytes": 1_300_000_000.0,
            "node_not_ready_count": 0.0,
            "monitoring_vm_up": 1.0,
            "ai_vm_up": 1.0,
        }
    )

    assert cpu_result.incident_type == "CPU_STRESS"
    assert cpu_result.recommended_action == "RESTART_POD_AND_CAPTURE_CPU_PROFILE"
    assert cpu_result.top_recommended_plans[0]["execution_mode"] in {"AUTO_EXECUTE_CANDIDATE", "RECOMMEND_ONLY"}
    assert memory_result.incident_type == "MEMORY_STRESS"
    assert memory_result.recommended_action == "RESTART_POD_AND_SCALE_MEMORY_LIMITS"
    assert len(cpu_result.top_recommended_plans) >= 3
    assert cpu_result.top_recommended_plans[0]["advantages"]
    assert cpu_result.top_recommended_plans[0]["tradeoffs"]
    assert "priority score" in cpu_result.decision_explanation.lower()


def test_recommendation_only_is_used_when_action_is_not_safe() -> None:
    engine = build_engine()

    result = engine.predict(
        {
            "overall_risk_score": 0.48,
            "node_not_ready_count": 1.0,
            "unavailable_replicas": 1.0,
        }
    )

    assert result.incident is True
    assert result.execution_mode == "RECOMMEND_ONLY"
    assert result.final_executable_action is None
    assert result.recommendation_only_reason is not None


def test_strategy_nonce_can_change_the_top_recommendation() -> None:
    engine = build_engine()
    features = {
        "overall_risk_score": 0.46,
        "metric_risk_score": 0.20,
        "db_keyword_hits": 8.0,
        "http_5xx_ratio": 0.06,
        "error_ratio": 0.10,
    }

    result_a = engine.predict(features, remediation_context={"strategy_nonce": 11})
    result_b = engine.predict(features, remediation_context={"strategy_nonce": 14})

    assert len(result_a.top_recommended_plans) >= 3
    assert len(result_b.top_recommended_plans) >= 3
    assert result_a.decision_context["strategy_nonce"] == 11
    assert result_b.decision_context["strategy_nonce"] == 14


def test_restart_loop_error_storm_and_db_connectivity_are_distinguished() -> None:
    engine = build_engine()

    restart_result = engine.predict(
        {
            "overall_risk_score": 0.44,
            "pod_restarts_total": 3.0,
            "http_5xx_ratio": 0.05,
        }
    )
    error_result = engine.predict(
        {
            "overall_risk_score": 0.48,
            "http_5xx_ratio": 0.28,
            "error_ratio": 0.30,
            "warning_line_count": 4.0,
        }
    )
    db_result = engine.predict(
        {
            "overall_risk_score": 0.46,
            "db_keyword_hits": 6.0,
            "error_ratio": 0.18,
            "http_5xx_ratio": 0.12,
        }
    )

    assert restart_result.incident_type == "RESTART_LOOP"
    assert restart_result.recommended_action == "ROLLBACK_RECENT_CHANGE_AND_RESTART"
    assert error_result.incident_type == "APPLICATION_ERROR_STORM"
    assert error_result.recommended_action == "ROLLBACK_LAST_RELEASE_AND_SHED_TRAFFIC"
    assert db_result.incident_type == "DATABASE_CONNECTIVITY"
    assert db_result.recommended_action == "CHECK_DB_CONNECTIVITY_AND_RESTART"


def test_single_restart_with_recovery_latency_is_classified_as_restart_loop() -> None:
    engine = build_engine(model_score=0.7685)

    result = engine.predict(
        {
            "overall_risk_score": 0.149,
            "metric_risk_score": 0.298,
            "pod_restarts_total": 1.0,
            "http_latency_p95_seconds": 4.99,
            "http_5xx_ratio": 0.0,
            "error_ratio": 0.0,
        }
    )

    assert result.incident is True
    assert result.incident_type == "RESTART_LOOP"
    assert result.recommended_action == "RESTART_POD_AND_INVESTIGATE"


def test_general_degradation_prefers_latency_investigation() -> None:
    engine = build_engine()

    result = engine.predict(
        {
            "overall_risk_score": 0.40,
            "http_latency_p95_seconds": 1.9,
            "warning_line_count": 12.0,
            "error_ratio": 0.11,
        }
    )

    assert result.incident is True
    assert result.incident_type == "GENERAL_DEGRADATION"
    assert result.recommended_action == "TRACE_DEPENDENCY_LATENCY_AND_INVESTIGATE"


def test_availability_drop_soft_brownout_is_not_reclassified_as_general_degradation() -> None:
    engine = build_engine(model_score=0.7685)

    result = engine.predict(
        {
            "overall_risk_score": 0.17245,
            "metric_risk_score": 0.209096,
            "warning_line_count": 1.0,
            "db_keyword_hits": 0.0,
            "error_ratio": 0.034188,
            "cpu_usage_cores": 0.0454,
            "memory_working_set_bytes": 718_299_136.0,
            "pod_restarts_total": 0.0,
            "unavailable_replicas": 0.0,
            "http_latency_p95_seconds": 1.429951,
            "http_5xx_ratio": 0.0,
        }
    )

    assert result.incident is True
    assert result.incident_type == "AVAILABILITY_DROP"
    assert result.recommended_action in {
        "RESTART_FAILED_WORKLOAD_AND_REBALANCE",
        "SCALE_DEPLOYMENT_AND_REBALANCE",
        "REBALANCE_REPLICAS_AND_SCALE_DEPLOYMENT",
        "SHIFT_TRAFFIC_AND_SCALE_DEPLOYMENT",
    }


def test_availability_drop_sparse_runtime_signature_is_preserved() -> None:
    engine = build_engine(model_score=0.7685)

    result = engine.predict(
        {
            "overall_risk_score": 0.394865,
            "metric_risk_score": 0.238731,
            "log_line_count": 1.0,
            "error_line_count": 1.0,
            "warning_line_count": 0.0,
            "error_ratio": 1.0,
            "cpu_usage_cores": 0.014285,
            "memory_working_set_bytes": 788_676_608.0,
            "pod_restarts_total": 1.0,
            "unavailable_replicas": 0.0,
            "http_latency_p95_seconds": 0.08836,
            "http_5xx_ratio": 0.0,
            "db_keyword_hits": 0.0,
        }
    )

    assert result.incident is True
    assert result.incident_type == "AVAILABILITY_DROP"


def test_availability_drop_with_unavailable_replica_beats_error_storm_bias() -> None:
    engine = build_engine(model_score=0.7685)

    result = engine.predict(
        {
            "overall_risk_score": 0.50,
            "metric_risk_score": 0.45,
            "log_line_count": 8.0,
            "error_line_count": 8.0,
            "error_ratio": 1.0,
            "http_latency_p95_seconds": 4.4,
            "http_5xx_ratio": 0.31,
            "unavailable_replicas": 1.0,
            "pod_restarts_total": 0.0,
            "db_keyword_hits": 0.0,
        }
    )

    assert result.incident is True
    assert result.incident_type == "AVAILABILITY_DROP"
    assert result.recommended_action in {
        "SCALE_DEPLOYMENT_AND_REBALANCE",
        "SHIFT_TRAFFIC_AND_SCALE_DEPLOYMENT",
        "REBALANCE_REPLICAS_AND_SCALE_DEPLOYMENT",
    }


def test_observed_availability_drop_signature_can_now_surface_auto_candidate() -> None:
    engine = build_engine(model_score=0.7685)

    result = engine.predict(
        {
            "overall_risk_score": 0.50,
            "metric_risk_score": 0.45,
            "log_line_count": 13.0,
            "error_line_count": 3.0,
            "warning_line_count": 0.0,
            "error_ratio": 0.230769,
            "cpu_usage_cores": 0.039249,
            "memory_working_set_bytes": 720_003_072.0,
            "pod_restarts_total": 0.0,
            "unavailable_replicas": 1.0,
            "pod_count": 2.0,
            "node_not_ready_count": 0.0,
            "monitoring_vm_up": 1.0,
            "ai_vm_up": 1.0,
            "http_latency_p95_seconds": 0.006849,
            "http_5xx_ratio": 0.306557,
            "db_keyword_hits": 0.0,
        }
    )

    assert result.incident is True
    assert result.incident_type == "AVAILABILITY_DROP"
    assert result.execution_mode == "AUTO_EXECUTE_CANDIDATE"
    assert result.final_executable_action == "SCALE_DEPLOYMENT"


def test_availability_drop_can_produce_auto_execute_scale_candidate() -> None:
    engine = build_engine(model_score=0.7685)

    result = engine.predict(
        {
            "overall_risk_score": 0.28,
            "metric_risk_score": 0.24,
            "unavailable_replicas": 1.0,
            "http_latency_p95_seconds": 1.6,
            "http_5xx_ratio": 0.02,
            "error_ratio": 0.03,
            "cpu_usage_cores": 0.10,
            "memory_working_set_bytes": 700_000_000.0,
            "pod_restarts_total": 0.0,
            "db_keyword_hits": 0.0,
        }
    )

    assert result.incident is True
    assert result.incident_type == "AVAILABILITY_DROP"
    assert result.execution_mode in {"AUTO_EXECUTE_CANDIDATE", "RECOMMEND_ONLY"}
    assert any(
        plan["executable_action"] == "SCALE_DEPLOYMENT"
        and plan["execution_mode"] == "AUTO_EXECUTE_CANDIDATE"
        for plan in result.top_recommended_plans
    )


def test_auto_execute_candidate_is_preferred_over_nonce_variation() -> None:
    engine = build_engine(model_score=0.7685)
    features = {
        "overall_risk_score": 0.50,
        "metric_risk_score": 0.45,
        "log_line_count": 8.0,
        "error_line_count": 8.0,
        "error_ratio": 1.0,
        "http_latency_p95_seconds": 4.4,
        "http_5xx_ratio": 0.31,
        "unavailable_replicas": 1.0,
        "pod_restarts_total": 0.0,
        "db_keyword_hits": 0.0,
    }

    result_a = engine.predict(features, remediation_context={"strategy_nonce": 11})
    result_b = engine.predict(features, remediation_context={"strategy_nonce": 14})

    assert result_a.incident_type == "AVAILABILITY_DROP"
    assert result_b.incident_type == "AVAILABILITY_DROP"
    assert result_a.execution_mode == "AUTO_EXECUTE_CANDIDATE"
    assert result_b.execution_mode == "AUTO_EXECUTE_CANDIDATE"
    assert result_a.final_executable_action == "SCALE_DEPLOYMENT"
    assert result_b.final_executable_action == "SCALE_DEPLOYMENT"


def test_single_error_line_does_not_force_application_error_storm() -> None:
    engine = build_engine(model_score=0.78)

    result = engine.predict(
        {
            "overall_risk_score": 0.16,
            "metric_risk_score": 0.18,
            "log_line_count": 1.0,
            "error_line_count": 1.0,
            "error_ratio": 1.0,
            "warning_line_count": 10.0,
            "http_latency_p95_seconds": 1.8,
            "http_5xx_ratio": 0.0,
        }
    )

    assert result.incident is True
    assert result.incident_type == "GENERAL_DEGRADATION"


def test_db_connectivity_can_trigger_without_http_5xx() -> None:
    engine = build_engine(model_score=0.7685)

    result = engine.predict(
        {
            "overall_risk_score": 0.148,
            "metric_risk_score": 0.277,
            "db_keyword_hits": 10.0,
            "http_5xx_ratio": 0.0,
            "error_ratio": 0.0,
        }
    )

    assert result.incident is True
    assert result.incident_type == "DATABASE_CONNECTIVITY"
    assert result.recommended_action == "FAILOVER_DATABASE_AND_RESTART_WORKLOAD"


def test_contextual_actions_vary_for_same_incident_family() -> None:
    engine = build_engine()

    app_error_traffic_result = engine.predict(
        {
            "overall_risk_score": 0.48,
            "http_5xx_ratio": 0.22,
            "error_ratio": 0.26,
            "warning_line_count": 3.0,
        }
    )
    app_error_restart_result = engine.predict(
        {
            "overall_risk_score": 0.40,
            "http_5xx_ratio": 0.05,
            "error_ratio": 0.09,
            "warning_line_count": 6.0,
        }
    )
    db_failover_result = engine.predict(
        {
            "overall_risk_score": 0.18,
            "metric_risk_score": 0.24,
            "db_keyword_hits": 10.0,
            "http_latency_p95_seconds": 0.9,
        }
    )
    db_pool_result = engine.predict(
        {
            "overall_risk_score": 0.18,
            "metric_risk_score": 0.20,
            "db_keyword_hits": 8.0,
            "http_5xx_ratio": 0.06,
            "error_ratio": 0.10,
        }
    )

    assert app_error_traffic_result.incident_type == "APPLICATION_ERROR_STORM"
    assert app_error_traffic_result.recommended_action == "SHIFT_TRAFFIC_TO_STABLE_REVISION_AND_INVESTIGATE"
    assert app_error_restart_result.incident_type == "GENERAL_DEGRADATION"
    assert app_error_restart_result.recommended_action == "TRACE_DEPENDENCY_LATENCY_AND_INVESTIGATE"
    assert db_failover_result.incident_type == "DATABASE_CONNECTIVITY"
    assert db_failover_result.recommended_action == "FAILOVER_DATABASE_AND_RESTART_WORKLOAD"
    assert db_pool_result.incident_type == "DATABASE_CONNECTIVITY"
    assert db_pool_result.recommended_action == "CHECK_DATABASE_CONNECTION_POOL_AND_RESTART"


def test_high_latency_alone_can_trigger_general_degradation() -> None:
    engine = build_engine(model_score=0.7685)

    result = engine.predict(
        {
            "overall_risk_score": 0.119,
            "metric_risk_score": 0.238,
            "http_latency_p95_seconds": 4.99,
            "http_5xx_ratio": 0.0,
            "error_ratio": 0.0,
        }
    )

    assert result.incident is True
    assert result.incident_type == "GENERAL_DEGRADATION"
    assert result.recommended_action == "CAPTURE_DIAGNOSTICS_AND_INVESTIGATE"


def test_cpu_stress_is_not_downgraded_to_general_degradation_by_latency() -> None:
    engine = build_engine(model_score=0.7685)

    result = engine.predict(
        {
            "overall_risk_score": 0.193,
            "metric_risk_score": 0.386,
            "cpu_usage_cores": 0.84,
            "memory_working_set_bytes": 663_920_640.0,
            "http_latency_p95_seconds": 4.99,
            "http_5xx_ratio": 0.0,
            "error_ratio": 0.0,
        }
    )

    assert result.incident is True
    assert result.incident_type == "CPU_STRESS"
    assert result.recommended_action == "RESTART_POD_AND_CAPTURE_CPU_PROFILE"


def test_observed_memory_stress_signature_beats_general_degradation_bias() -> None:
    engine = build_engine(model_score=0.7685)

    result = engine.predict(
        {
            "overall_risk_score": 0.27,
            "metric_risk_score": 0.34,
            "memory_working_set_bytes": 957_751_296.0,
            "cpu_usage_cores": 0.11,
            "http_5xx_ratio": 0.0,
            "error_ratio": 0.217391,
            "unavailable_replicas": 0.0,
            "pod_restarts_total": 0.0,
            "db_keyword_hits": 0.0,
        }
    )

    assert result.incident is True
    assert result.incident_type == "MEMORY_STRESS"


def test_observed_restart_loop_signature_beats_memory_and_db_noise() -> None:
    engine = build_engine(model_score=0.7685)

    result = engine.predict(
        {
            "overall_risk_score": 0.33,
            "metric_risk_score": 0.41,
            "memory_working_set_bytes": 1_286_594_560.0,
            "cpu_usage_cores": 0.18,
            "http_5xx_ratio": 0.0,
            "error_ratio": 0.0,
            "unavailable_replicas": 1.0,
            "pod_restarts_total": 1.036404,
            "db_keyword_hits": 8.0,
            "restart_keyword_hits": 3.0,
        }
    )

    assert result.incident is True
    assert result.incident_type == "RESTART_LOOP"


def test_observed_availability_drop_signature_beats_error_storm_bias() -> None:
    engine = build_engine(model_score=0.7685)

    result = engine.predict(
        {
            "overall_risk_score": 0.28,
            "metric_risk_score": 0.31,
            "cpu_usage_cores": 0.07,
            "memory_working_set_bytes": 760_000_000.0,
            "http_latency_p95_seconds": 0.065678,
            "http_5xx_ratio": 0.296322,
            "error_ratio": 0.28,
            "unavailable_replicas": 1.0,
            "pod_restarts_total": 0.0,
            "db_keyword_hits": 0.0,
        }
    )

    assert result.incident is True
    assert result.incident_type == "AVAILABILITY_DROP"


def test_observed_cpu_stress_signature_beats_log_error_bias() -> None:
    engine = build_engine(model_score=0.7685)

    result = engine.predict(
        {
            "overall_risk_score": 0.21623,
            "metric_risk_score": 0.244888,
            "cpu_usage_cores": 1.0072,
            "memory_working_set_bytes": 771_900_000.0,
            "http_5xx_ratio": 0.0,
            "error_ratio": 0.3333,
            "http_latency_p95_seconds": 3.9430,
            "unavailable_replicas": 0.0,
            "pod_restarts_total": 0.0,
            "db_keyword_hits": 0.0,
        }
    )

    assert result.incident is True
    assert result.incident_type == "CPU_STRESS"


def test_high_latency_is_not_misclassified_as_database_connectivity() -> None:
    engine = build_engine(model_score=0.7685)

    result = engine.predict(
        {
            "overall_risk_score": 0.132,
            "metric_risk_score": 0.241,
            "log_risk_score": 0.022,
            "db_keyword_hits": 22.0,
            "http_latency_p95_seconds": 9.8,
            "http_5xx_ratio": 0.0,
            "error_ratio": 0.0,
        }
    )

    assert result.incident is True
    assert result.incident_type == "GENERAL_DEGRADATION"


def test_nan_inputs_are_sanitized_to_json_safe_values() -> None:
    engine = build_engine()

    result = engine.predict(
        {
            "overall_risk_score": float("nan"),
            "cpu_usage_cores": float("nan"),
            "memory_working_set_bytes": float("nan"),
            "http_latency_p95_seconds": float("nan"),
            "http_5xx_ratio": float("nan"),
        }
    )

    assert result.incident is False
    assert result.confidence == 0.0
    assert result.rule_score == 0.0
    assert result.model_score is None
    assert result.top_recommended_plans == []


def test_history_can_penalize_repeated_failed_strategies() -> None:
    engine = build_engine()

    result = engine.predict(
        {
            "overall_risk_score": 0.48,
            "http_5xx_ratio": 0.22,
            "error_ratio": 0.26,
        },
        remediation_context={
            "recent_action_counts": {
                "SCALE_DEPLOYMENT": 3,
                "RESTART_POD": 1,
            },
            "recent_failed_action_counts": {
                "SCALE_DEPLOYMENT": 2,
            },
            "recent_success_action_counts": {
                "RESTART_POD": 1,
            },
        },
    )

    assert result.incident_type == "APPLICATION_ERROR_STORM"
    assert len(result.top_recommended_plans) >= 3
    assert result.top_recommended_plans[0]["recommended_action"] in {
        "ROLLBACK_LAST_RELEASE_AND_SHED_TRAFFIC",
        "RESTART_POD_AND_INVESTIGATE",
        "SHIFT_TRAFFIC_TO_STABLE_REVISION_AND_INVESTIGATE",
    }
    assert "history" in result.decision_explanation.lower() or "recent" in result.decision_explanation.lower()
