import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.clients.prometheus_client import PrometheusClient
from app.core.config import Settings

logger = logging.getLogger(__name__)


@dataclass
class MetricTrend:
    metric_name: str
    current_value: float
    trend_slope: float        # units/minute
    time_to_threshold: float  # minutes until threshold breach (-1 = never)
    threshold: float
    is_trending_bad: bool
    severity: str             # LOW / MEDIUM / HIGH / CRITICAL


@dataclass
class ProactiveResult:
    needs_attention: bool
    predicted_incident: bool
    time_horizon_minutes: float
    severity: str
    incident_type: str
    confidence: float
    trends: list[MetricTrend] = field(default_factory=list)
    explanation: str = ""
    recommended_action: str = "INVESTIGATE"


class ProactiveService:
    """
    Detects anomalies BEFORE they degenerate into incidents.
    Uses linear regression on metric time series to predict threshold breaches.
    """

    THRESHOLDS = {
        "error_rate":   (0.05,  0.20,  0.40),   # warn, high, critical
        "cpu_usage":    (0.70,  0.85,  0.95),
        "memory_usage": (0.75,  0.88,  0.95),
        "p95_latency":  (0.500, 1.0,   2.0),    # seconds
    }

    PREDICTION_HORIZON_MINUTES = 15  # predict up to 15 min in the future

    def __init__(self, settings: Settings) -> None:
        self.prometheus = PrometheusClient(
            base_url=settings.prometheus_base_url,
            timeout_seconds=settings.prometheus_timeout_seconds,
        )

    def predict_proactive(self, namespace: str, app: str, window_minutes: int = 15) -> ProactiveResult:
        now = datetime.now(timezone.utc)
        trends: list[MetricTrend] = []

        # Collect time series for each key metric
        metric_queries = {
            "error_rate": (
                f'sum(rate(http_server_requests_seconds_count{{namespace="{namespace}",application="{app}",status=~"5.."}}[2m])) / '
                f'sum(rate(http_server_requests_seconds_count{{namespace="{namespace}",application="{app}"}}[2m]))',
                self.THRESHOLDS["error_rate"]
            ),
            "cpu_usage": (
                f'avg(rate(container_cpu_usage_seconds_total{{namespace="{namespace}",container="{app}"}}[2m]))',
                self.THRESHOLDS["cpu_usage"]
            ),
            "memory_usage": (
                f'sum(container_memory_working_set_bytes{{namespace="{namespace}",container="{app}"}}) / '
                f'sum(kube_pod_container_resource_limits{{namespace="{namespace}",container="{app}",resource="memory"}})',
                self.THRESHOLDS["memory_usage"]
            ),
            "p95_latency": (
                f'histogram_quantile(0.95, sum by(le)(rate(http_server_requests_seconds_bucket{{namespace="{namespace}",application="{app}"}}[2m])))',
                self.THRESHOLDS["p95_latency"]
            ),
        }

        for metric_name, (query, thresholds) in metric_queries.items():
            trend = self._analyze_trend(metric_name, query, thresholds, now, window_minutes)
            if trend is not None:
                trends.append(trend)

        if not trends:
            return ProactiveResult(
                needs_attention=False,
                predicted_incident=False,
                time_horizon_minutes=0,
                severity="LOW",
                incident_type="NO_DATA",
                confidence=0.0,
                explanation=f"No metrics available for {namespace}/{app}"
            )

        # Find the most critical trend
        bad_trends = [t for t in trends if t.is_trending_bad]
        critical_trends = [t for t in bad_trends if t.severity in ("HIGH", "CRITICAL")]
        high_trends = [t for t in bad_trends if t.severity == "HIGH"]

        if not bad_trends:
            return ProactiveResult(
                needs_attention=False,
                predicted_incident=False,
                time_horizon_minutes=0,
                severity="LOW",
                incident_type="HEALTHY",
                confidence=0.85,
                trends=trends,
                explanation=f"All metrics stable for {namespace}/{app} — no incident predicted"
            )

        # Determine severity and type
        if critical_trends:
            overall_severity = "CRITICAL"
            confidence = 0.80
        elif high_trends:
            overall_severity = "HIGH"
            confidence = 0.65
        else:
            overall_severity = "MEDIUM"
            confidence = 0.50

        # Find shortest time to incident
        times = [t.time_to_threshold for t in bad_trends if t.time_to_threshold > 0]
        min_time = min(times) if times else self.PREDICTION_HORIZON_MINUTES

        incident_type = self._classify_incident_type(bad_trends)
        action = self._recommend_action(bad_trends)
        explanation = self._build_explanation(namespace, app, bad_trends, min_time)

        return ProactiveResult(
            needs_attention=True,
            predicted_incident=len(critical_trends) > 0,
            time_horizon_minutes=round(min_time, 1),
            severity=overall_severity,
            incident_type=incident_type,
            confidence=confidence,
            trends=trends,
            explanation=explanation,
            recommended_action=action
        )

    def _analyze_trend(self, metric_name: str, query: str, thresholds: tuple,
                        now: datetime, window_minutes: int) -> MetricTrend | None:
        warn_threshold, high_threshold, critical_threshold = thresholds

        # Fetch time series: last window_minutes, one point per minute
        step = 60  # 1 minute steps
        start = now - timedelta(minutes=window_minutes)

        try:
            series = self.prometheus.query_range(query, start=start, end=now, step=step)
        except Exception as exc:
            logger.debug("Proactive metric query failed for %s: %s", metric_name, exc)
            return None

        if not series or len(series) < 3:
            return None

        # Parse values
        values = []
        for ts, val in series:
            try:
                v = float(val)
                if math.isfinite(v):
                    values.append(v)
            except (ValueError, TypeError):
                pass

        if len(values) < 3:
            return None

        current = values[-1]
        slope = self._linear_slope(values)  # units per data point (1 min)

        # Time to threshold breach
        time_to_threshold = -1.0
        if slope > 0:
            if current < critical_threshold:
                time_to_threshold = (critical_threshold - current) / slope
            elif current < high_threshold:
                time_to_threshold = (high_threshold - current) / slope

        # Determine if trending bad (slope positive + approaching threshold)
        is_trending_bad = (
            slope > 0 and
            current > warn_threshold * 0.7 and  # at least 70% of warning threshold
            (time_to_threshold < self.PREDICTION_HORIZON_MINUTES or current > high_threshold)
        )

        # Determine severity based on current value
        if current >= critical_threshold:
            severity = "CRITICAL"
        elif current >= high_threshold:
            severity = "HIGH"
        elif current >= warn_threshold:
            severity = "MEDIUM"
        else:
            severity = "LOW"

        # If trending toward critical within horizon, elevate
        if is_trending_bad and 0 < time_to_threshold < 5:
            severity = "CRITICAL"
        elif is_trending_bad and 0 < time_to_threshold < 10:
            severity = "HIGH"

        return MetricTrend(
            metric_name=metric_name,
            current_value=round(current, 4),
            trend_slope=round(slope, 6),
            time_to_threshold=round(time_to_threshold, 1),
            threshold=critical_threshold,
            is_trending_bad=is_trending_bad,
            severity=severity
        )

    def _linear_slope(self, values: list[float]) -> float:
        n = len(values)
        if n < 2:
            return 0.0
        x_mean = (n - 1) / 2.0
        y_mean = sum(values) / n
        numerator = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(values))
        denominator = sum((i - x_mean) ** 2 for i in range(n))
        return numerator / denominator if denominator != 0 else 0.0

    def _classify_incident_type(self, bad_trends: list[MetricTrend]) -> str:
        names = {t.metric_name for t in bad_trends}
        if "error_rate" in names:
            return "PREDICTED_ERROR_SURGE"
        if "cpu_usage" in names:
            return "PREDICTED_CPU_EXHAUSTION"
        if "memory_usage" in names:
            return "PREDICTED_OOM"
        if "p95_latency" in names:
            return "PREDICTED_LATENCY_DEGRADATION"
        return "PREDICTED_DEGRADATION"

    def _recommend_action(self, bad_trends: list[MetricTrend]) -> str:
        names = {t.metric_name for t in bad_trends}
        critical = {t.metric_name for t in bad_trends if t.severity == "CRITICAL"}
        if "memory_usage" in critical:
            return "SCALE_DEPLOYMENT"
        if "cpu_usage" in critical:
            return "SCALE_DEPLOYMENT"
        if "error_rate" in critical:
            return "RESTART_POD"
        return "INVESTIGATE"

    def _build_explanation(self, namespace: str, app: str,
                            bad_trends: list[MetricTrend], min_time: float) -> str:
        parts = []
        for t in bad_trends:
            tta = f"in ~{t.time_to_threshold:.0f} min" if t.time_to_threshold > 0 else "already breached"
            parts.append(
                f"{t.metric_name}={t.current_value:.3f} (slope={t.trend_slope:+.4f}/min, threshold {tta})"
            )
        return (
            f"Proactive analysis for {namespace}/{app}: "
            f"incident predicted in ~{min_time:.0f} min. "
            + " | ".join(parts)
        )
