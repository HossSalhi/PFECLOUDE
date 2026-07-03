from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

ExecutableAction = Literal["RESTART_POD", "SCALE_DEPLOYMENT", "INVESTIGATE", "ESCALATE", "NO_ACTION"]


class LearnRequest(BaseModel):
    features: dict[str, float] = Field(..., description="Metric+log features at incident time")
    incident_type: str
    severity: str
    action: str
    executable: ExecutableAction
    success: bool = Field(..., description="True if incident was resolved, False if failed")
    minutes_to_resolve: int = Field(default=0, ge=0)
    app: str = "backend"
    namespace: str = "pfe"
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class LearnResponse(BaseModel):
    stored: bool
    memory_count: int


Severity = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]


class PredictRequest(BaseModel):
    namespace: str = Field(..., min_length=1, description="Kubernetes namespace")
    app: str = Field(..., min_length=1, description="Application label or base app name")
    window_minutes: int | None = Field(
        default=None,
        ge=1,
        le=120,
        description="Lookback window in minutes",
    )
    end_time: datetime | None = Field(
        default=None,
        description="Optional UTC end-time for windowed analysis",
    )
    feature_overrides: dict[str, float] | None = Field(
        default=None,
        description="Optional trusted runtime features supplied by the caller",
    )
    skip_llm: bool = Field(
        default=False,
        description="Skip LLM — use XGBoost-only prediction (fast check mode)",
    )
    remediation_context: dict[str, Any] | None = Field(
        default=None,
        description="Optional bounded-autonomy context with recent remediation history",
    )


class PredictEvidence(BaseModel):
    log_score: float
    metric_score: float
    rule_score: float
    model_score: float | None = None


class RecommendedPlan(BaseModel):
    rank: int
    plan_id: str
    title: str
    strategy: str
    recommended_action: str
    executable_action: str
    safe_to_execute: bool
    execution_mode: Literal["AUTO_EXECUTE_CANDIDATE", "RECOMMEND_ONLY"]
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


class PredictResponse(BaseModel):
    incident: bool
    noise: bool
    incident_type: str
    severity: Severity
    recommended_action: str
    confidence: float
    summary: str
    decision_explanation: str
    catalog_resolution: str | None = None
    chosen_plan_id: str | None = None
    final_executable_action: str | None = None
    execution_mode: Literal["AUTO_EXECUTE_CANDIDATE", "RECOMMEND_ONLY"] = "RECOMMEND_ONLY"
    recommendation_only_reason: str | None = None
    top_recommended_plans: list[RecommendedPlan]
    evidence: PredictEvidence
    features: dict[str, float]
    decision_context: dict[str, Any] = Field(default_factory=dict)
    window_start: datetime
    window_end: datetime


class CorrelateRequest(BaseModel):
    namespace: str = Field(..., min_length=1)
    window_minutes: int = Field(default=5, ge=1, le=60)


class AppSignalSchema(BaseModel):
    app: str
    namespace: str
    error_rate: float
    cpu_usage: float
    memory_usage: float
    is_anomalous: bool
    anomaly_score: float


class CorrelateResponse(BaseModel):
    correlated: bool
    root_cause: str
    affected_apps: list[str]
    probable_cause: str
    confidence: float
    signals: list[AppSignalSchema] = Field(default_factory=list)
    explanation: str = ""


class ProactiveRequest(BaseModel):
    namespace: str = Field(..., min_length=1)
    app: str = Field(..., min_length=1)
    window_minutes: int = Field(default=15, ge=5, le=60)


class MetricTrendSchema(BaseModel):
    metric_name: str
    current_value: float
    trend_slope: float
    time_to_threshold: float
    threshold: float
    is_trending_bad: bool
    severity: str


class ProactiveResponse(BaseModel):
    needs_attention: bool
    predicted_incident: bool
    time_horizon_minutes: float
    severity: str
    incident_type: str
    confidence: float
    trends: list[MetricTrendSchema] = Field(default_factory=list)
    explanation: str = ""
    recommended_action: str = "INVESTIGATE"
