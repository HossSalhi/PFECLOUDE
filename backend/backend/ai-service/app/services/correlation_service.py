import logging
import math
from dataclasses import dataclass, field

from app.clients.prometheus_client import PrometheusClient
from app.core.config import Settings
from app.utils.time_windows import compute_window

logger = logging.getLogger(__name__)


@dataclass
class AppSignal:
    app: str
    namespace: str
    error_rate: float
    cpu_usage: float
    memory_usage: float
    is_anomalous: bool
    anomaly_score: float


@dataclass
class CorrelationResult:
    correlated: bool
    root_cause: str
    affected_apps: list[str]
    probable_cause: str
    confidence: float
    signals: list[AppSignal] = field(default_factory=list)
    explanation: str = ""


class CorrelationService:
    """
    Detects correlated failures across multiple apps in the same namespace.
    If 2+ apps show simultaneous anomalies, infers a shared root cause
    (network, node, storage, etc.) rather than treating them as isolated incidents.
    """

    ERROR_RATE_THRESHOLD = 0.05   # 5% error rate = anomalous
    CPU_THRESHOLD = 0.80          # 80% CPU = anomalous
    MEMORY_THRESHOLD = 0.85       # 85% memory = anomalous
    MIN_APPS_FOR_CORRELATION = 2  # need at least 2 affected apps

    def __init__(self, settings: Settings) -> None:
        self.prometheus = PrometheusClient(
            base_url=settings.prometheus_base_url,
            timeout_seconds=settings.prometheus_timeout_seconds,
        )

    def correlate(self, namespace: str, window_minutes: int = 5) -> CorrelationResult:
        window_start, window_end = compute_window(window_minutes, None)

        # 1. Discover apps in namespace from Prometheus
        apps = self._discover_apps(namespace)
        if not apps:
            return CorrelationResult(
                correlated=False,
                root_cause="no_data",
                affected_apps=[],
                probable_cause="No apps discovered in namespace",
                confidence=0.0,
                explanation=f"No Prometheus metrics found for namespace={namespace}"
            )

        # 2. Collect signals for each app
        signals: list[AppSignal] = []
        for app in apps:
            signal = self._collect_signal(namespace, app, window_end)
            signals.append(signal)

        # 3. Find anomalous apps
        anomalous = [s for s in signals if s.is_anomalous]

        if len(anomalous) < self.MIN_APPS_FOR_CORRELATION:
            return CorrelationResult(
                correlated=False,
                root_cause="isolated",
                affected_apps=[s.app for s in anomalous],
                probable_cause="Isolated failure (fewer than 2 apps affected)",
                confidence=0.0,
                signals=signals,
                explanation=f"{len(anomalous)}/{len(signals)} apps anomalous — below correlation threshold"
            )

        # 4. Infer root cause from signal patterns
        root_cause, probable_cause, confidence = self._infer_root_cause(anomalous)

        ratio = len(anomalous) / len(signals)
        explanation = (
            f"{len(anomalous)}/{len(signals)} apps affected simultaneously in namespace '{namespace}'. "
            f"Pattern suggests: {probable_cause}. "
            f"Affected: {[s.app for s in anomalous]}"
        )

        return CorrelationResult(
            correlated=True,
            root_cause=root_cause,
            affected_apps=[s.app for s in anomalous],
            probable_cause=probable_cause,
            confidence=round(confidence * min(ratio * 1.5, 1.0), 3),
            signals=signals,
            explanation=explanation
        )

    def _discover_apps(self, namespace: str) -> list[str]:
        try:
            result = self.prometheus.query_raw(
                f'count by (app) (up{{namespace="{namespace}"}})'
            )
            apps = []
            for item in (result or []):
                metric = item.get("metric", {})
                app = metric.get("app") or metric.get("container") or metric.get("pod", "").split("-")[0]
                if app and app not in apps:
                    apps.append(app)
            return apps
        except Exception as exc:
            logger.warning("Could not discover apps in namespace %s: %s", namespace, exc)
            return []

    def _collect_signal(self, namespace: str, app: str, at_time) -> AppSignal:
        error_rate = 0.0
        cpu_usage = 0.0
        memory_usage = 0.0

        try:
            # HTTP error rate
            total = self.prometheus.query_scalar(
                f'sum(rate(http_server_requests_seconds_count{{namespace="{namespace}",application="{app}"}}[5m]))',
                at_time=at_time
            ) or 0.0
            errors = self.prometheus.query_scalar(
                f'sum(rate(http_server_requests_seconds_count{{namespace="{namespace}",application="{app}",status=~"5.."}}[5m]))',
                at_time=at_time
            ) or 0.0
            error_rate = (errors / total) if total > 0 else 0.0
        except Exception:
            pass

        try:
            cpu_usage = self.prometheus.query_scalar(
                f'avg(rate(container_cpu_usage_seconds_total{{namespace="{namespace}",container="{app}"}}[5m]))',
                at_time=at_time
            ) or 0.0
        except Exception:
            pass

        try:
            used = self.prometheus.query_scalar(
                f'sum(container_memory_working_set_bytes{{namespace="{namespace}",container="{app}"}})',
                at_time=at_time
            ) or 0.0
            limit = self.prometheus.query_scalar(
                f'sum(kube_pod_container_resource_limits{{namespace="{namespace}",container="{app}",resource="memory"}})',
                at_time=at_time
            ) or 1.0
            memory_usage = used / limit if limit > 0 else 0.0
        except Exception:
            pass

        anomaly_score = self._compute_anomaly_score(error_rate, cpu_usage, memory_usage)
        is_anomalous = anomaly_score > 0.4

        return AppSignal(
            app=app,
            namespace=namespace,
            error_rate=round(error_rate, 4),
            cpu_usage=round(cpu_usage, 4),
            memory_usage=round(memory_usage, 4),
            is_anomalous=is_anomalous,
            anomaly_score=round(anomaly_score, 4)
        )

    def _compute_anomaly_score(self, error_rate: float, cpu: float, memory: float) -> float:
        score = 0.0
        if error_rate > self.ERROR_RATE_THRESHOLD:
            score += min(error_rate / 0.5, 1.0) * 0.5
        if cpu > self.CPU_THRESHOLD:
            score += (cpu - self.CPU_THRESHOLD) / (1 - self.CPU_THRESHOLD) * 0.25
        if memory > self.MEMORY_THRESHOLD:
            score += (memory - self.MEMORY_THRESHOLD) / (1 - self.MEMORY_THRESHOLD) * 0.25
        return min(score, 1.0)

    def _infer_root_cause(self, anomalous: list[AppSignal]) -> tuple[str, str, float]:
        avg_error = sum(s.error_rate for s in anomalous) / len(anomalous)
        avg_cpu = sum(s.cpu_usage for s in anomalous) / len(anomalous)
        avg_mem = sum(s.memory_usage for s in anomalous) / len(anomalous)

        if avg_error > 0.3:
            return "NETWORK_OR_DEPENDENCY", "Network disruption or shared dependency failure (high error rates across all apps)", 0.75
        if avg_cpu > 0.85:
            return "NODE_CPU_PRESSURE", "Node-level CPU pressure (all pods on same node starving)", 0.70
        if avg_mem > 0.85:
            return "MEMORY_PRESSURE", "Node memory pressure or memory leak in shared library", 0.68
        if avg_error > 0.05:
            return "INFRASTRUCTURE_DEGRADATION", "Infrastructure degradation affecting all services simultaneously", 0.65

        return "CORRELATED_UNKNOWN", "Multiple apps affected simultaneously — shared infrastructure cause suspected", 0.55
