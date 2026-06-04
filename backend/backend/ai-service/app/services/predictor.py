import logging
import math
import re
import time

_SAFE_LABEL = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9\-_.]*$')

from app.clients.loki_client import LokiClient
from app.clients.prometheus_client import PrometheusClient
from app.core.config import Settings
from app.features.combine import combine_features
from app.features.log_embeddings import LogEmbeddingEncoder
from app.features.log_features import extract_log_features, score_log_risk
from app.features.metric_features import (
    build_metric_queries,
    default_metric_features,
    score_metric_risk,
)
from app.models.inference import InferenceEngine
from app.models.loader import load_model_bundle
from app.models.schemas import PredictEvidence, PredictRequest, PredictResponse
from app.services.memory_store import ChromaMemoryStore, IncidentMemory
from app.utils.time_windows import compute_window

logger = logging.getLogger(__name__)


class PredictorService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.loki_client = LokiClient(
            base_url=settings.loki_base_url,
            timeout_seconds=settings.loki_timeout_seconds,
        )
        self.prometheus_client = PrometheusClient(
            base_url=settings.prometheus_base_url,
            timeout_seconds=settings.prometheus_timeout_seconds,
        )
        model_bundle = load_model_bundle(settings.model_path)
        self.inference_engine = InferenceEngine(model_bundle=model_bundle)
        self.log_embedding_encoder = LogEmbeddingEncoder(
            model_name=settings.log_embedding_model_name,
            fallback_dim=settings.log_embedding_fallback_dim,
            max_lines=settings.log_embedding_max_lines,
        )
        self.memory_store = ChromaMemoryStore(
            persist_dir=settings.chroma_persist_dir,
            mode=settings.chroma_mode,
            http_host=settings.chroma_http_host,
            http_port=settings.chroma_http_port,
            http_ssl=settings.chroma_http_ssl,
        ) if settings.memory_enabled else None

        # ── MLOps: Drift Detection + Model Registry + Auto-Trainer + A/B Testing ──
        if settings.mlops_enabled:
            from app.services.drift_detector import DriftDetector
            from app.services.model_registry import ModelRegistry
            from app.services.auto_trainer import AutoTrainer
            from app.services.ab_tester import ABTester

            self.drift_detector = DriftDetector(
                persist_path=settings.drift_history_path,
            )
            self.model_registry = ModelRegistry(
                registry_file=settings.model_registry_file,
                registry_dir=settings.model_registry_dir,
            )
            self.auto_trainer = AutoTrainer(
                model_registry=self.model_registry,
                drift_detector=self.drift_detector,
                dataset_path=settings.dataset_path,
                retrain_trigger_count=settings.retrain_trigger_incidents,
            )
            self.ab_tester = ABTester(
                model_registry=self.model_registry,
                sample_target=settings.ab_test_sample_target,
                auto_promote=settings.ab_test_auto_promote,
            )
        else:
            self.drift_detector = None
            self.model_registry = None
            self.auto_trainer = None
            self.ab_tester = None

    @property
    def model_loaded(self) -> bool:
        return self.inference_engine.model_loaded

    def predict(self, request: PredictRequest) -> PredictResponse:
        window_minutes = request.window_minutes or self.settings.default_window_minutes
        window_start, window_end = compute_window(window_minutes, request.end_time)

        if self.settings.enable_upstream_queries:
            namespace = request.namespace if _SAFE_LABEL.match(request.namespace or "") else "default"
            app       = request.app       if _SAFE_LABEL.match(request.app or "")       else "unknown"
            pod_pattern = f"{app}-.*"
            log_query = f'{{namespace="{namespace}",pod=~"{pod_pattern}"}}'

            try:
                lines = self.loki_client.query_range(
                    query=log_query,
                    start=window_start,
                    end=window_end,
                )
            except Exception as exc:
                logger.warning(
                    "Loki query failed for %s/%s, using empty log window: %s",
                    request.namespace, request.app, exc,
                )
                lines = []

            metric_queries = build_metric_queries(
                namespace=request.namespace,
                app=request.app,
                window_minutes=window_minutes,
                monitoring_vm_ip=self.settings.monitoring_vm_ip,
                ai_camunda_vm_ip=self.settings.ai_camunda_vm_ip,
                monitoring_prometheus_port=self.settings.monitoring_prometheus_port,
                ai_service_port=self.settings.ai_service_port,
            )
            metric_features = default_metric_features()
            try:
                metric_features.update(
                    self.prometheus_client.batch_query(metric_queries, at_time=window_end)
                )
            except Exception as exc:
                logger.warning(
                    "Prometheus query failed for %s/%s, using default metric features: %s",
                    request.namespace, request.app, exc,
                )

            if request.feature_overrides:
                metric_features.update(
                    {
                        key: self._finite(value)
                        for key, value in request.feature_overrides.items()
                    }
                )
        else:
            lines = []
            metric_features = default_metric_features()
            if request.feature_overrides:
                metric_features.update(
                    {
                        key: self._finite(value)
                        for key, value in request.feature_overrides.items()
                    }
                )

        log_features = extract_log_features(lines)
        log_embedding = self.log_embedding_encoder.encode(lines)
        log_embedding_features = self.log_embedding_encoder.as_feature_map(log_embedding)
        log_score = score_log_risk(log_features)
        metric_score = score_metric_risk(metric_features)
        combined = combine_features(
            log_features,
            metric_features,
            log_score,
            metric_score,
            extra_features=log_embedding_features,
        )
        combined = {
            key: self._finite(value)
            for key, value in combined.items()
        }

        # Niveau 2 — record features for drift detection
        if self.drift_detector is not None:
            self.drift_detector.record(combined)

        # Niveau 3 — A/B shadow testing (silent, does not affect response)
        if self.ab_tester is not None and self.ab_tester.active:
            try:
                feature_vec = self.inference_engine._to_vector(combined)
                primary_incident = int(
                    self.inference_engine.model_bundle.incident_model.predict(feature_vec)[0]
                ) if self.inference_engine.model_bundle.incident_model is not None else 0
                self.ab_tester.run_shadow(feature_vec, primary_incident)
            except Exception as exc:
                logger.debug("ABTester shadow run skipped: %s", exc)

        # Niveau 1 — Enrich context with ChromaDB semantic memory
        remediation_ctx = dict(request.remediation_context or {})
        if self.memory_store and self.memory_store.available:
            similar = self.memory_store.query_similar(combined)
            if similar:
                existing = remediation_ctx.get("past_incidents") or []
                remediation_ctx["past_incidents"] = similar + [
                    p for p in existing if p not in similar
                ]

        # Root-cause enrichment: when signals indicate a fault, fetch the REAL Kubernetes
        # reason (ImagePullBackOff, OOMKilled, CrashLoopBackOff, Evicted, ...) so the AI
        # "Why" names the actual cause instead of staying generic. Gated to fault cases to
        # avoid extra Prometheus queries on healthy apps.
        if not getattr(request, "skip_llm", False) and (
            combined.get("unavailable_replicas", 0.0) >= 1.0
            or combined.get("pod_restarts_total", 0.0) >= 1.0
            or combined.get("node_not_ready_count", 0.0) >= 1.0
            or combined.get("metric_risk_score", 0.0) >= 0.05
        ):
            root_cause = self._collect_root_cause(request.namespace, request.app)
            if root_cause:
                remediation_ctx["root_cause"] = root_cause

        result = self.inference_engine.predict(
            combined,
            remediation_context=remediation_ctx,
            skip_llm=getattr(request, "skip_llm", False),
        )
        evidence = PredictEvidence(
            log_score=round(self._finite(log_score), 4),
            metric_score=round(self._finite(metric_score), 4),
            rule_score=round(self._finite(result.rule_score), 4),
            model_score=round(self._finite(result.model_score), 4)
            if result.model_score is not None
            else None,
        )

        return PredictResponse(
            incident=result.incident,
            noise=result.noise,
            incident_type=result.incident_type,
            severity=result.severity,
            recommended_action=result.recommended_action,
            final_executable_action=result.final_executable_action,
            execution_mode=result.execution_mode,
            recommendation_only_reason=result.recommendation_only_reason,
            confidence=round(self._finite(result.confidence), 4),
            summary=result.summary,
            decision_explanation=result.decision_explanation,
            chosen_plan_id=result.chosen_plan_id,
            top_recommended_plans=result.top_recommended_plans,
            evidence=evidence,
            features={k: round(self._finite(v), 6) for k, v in combined.items()},
            decision_context=result.decision_context,
            window_start=window_start,
            window_end=window_end,
        )

    def learn(
        self,
        features: dict[str, float],
        incident_type: str,
        severity: str,
        action: str,
        executable: str,
        success: bool,
        minutes_to_resolve: int,
        app: str,
        namespace: str,
        confidence: float,
    ) -> bool:
        """Store resolved incident in ChromaDB and trigger auto-retraining if needed."""
        stored = False
        if self.memory_store and self.memory_store.available:
            memory = IncidentMemory(
                incident_type=incident_type,
                severity=severity,
                action=action,
                executable=executable,
                success=success,
                minutes_to_resolve=minutes_to_resolve,
                app=app,
                namespace=namespace,
                confidence=confidence,
                timestamp=time.time(),
            )
            self.memory_store.store(features, memory)
            stored = True

        # Niveau 2 — auto-retraining: count new incidents and check triggers
        if self.auto_trainer is not None:
            self.auto_trainer.record_new_incident()
            self.auto_trainer.maybe_trigger_async()

        return stored

    def memory_count(self) -> int:
        if not self.memory_store:
            return 0
        return self.memory_store.count()

    def _collect_root_cause(self, namespace: str, app: str) -> str | None:
        """Query kube-state-metrics 'reason' series for the REAL Kubernetes root cause of an
        incident (ImagePullBackOff, ErrImagePull, CrashLoopBackOff, CreateContainerConfigError,
        OOMKilled, Evicted, ...). Returns a short human string or None. Best-effort and durable
        for any app/incident: it reads standard KSM metrics, not app-specific signals."""
        if not self.settings.enable_upstream_queries:
            return None
        if not (_SAFE_LABEL.match(namespace or "") and _SAFE_LABEL.match(app or "")):
            return None
        pod_re = f"{app}-.*"
        ignore = {"ContainerCreating", "PodInitializing", "Completed", ""}
        parts: list[str] = []
        try:
            def _reasons(metric: str) -> list[str]:
                rows = self.prometheus_client.query_raw(
                    f'{metric}{{namespace="{namespace}",pod=~"{pod_re}"}} == 1'
                )
                seen: list[str] = []
                for row in rows:
                    reason = (row.get("metric", {}) or {}).get("reason")
                    if reason and reason not in ignore and reason not in seen:
                        seen.append(reason)
                return seen

            waiting = _reasons("kube_pod_container_status_waiting_reason")
            if waiting:
                parts.append("container waiting: " + ", ".join(waiting))
            terminated = _reasons("kube_pod_container_status_last_terminated_reason")
            if terminated:
                parts.append("last termination: " + ", ".join(terminated))
            pod_reason = _reasons("kube_pod_status_reason")
            if pod_reason:
                parts.append("pod status: " + ", ".join(pod_reason))
            # Node-level fallback (covers NODE_NOT_READY / a VM going down): name the
            # NotReady node(s) and the underlying pressure condition when present.
            if not parts:
                notready = self.prometheus_client.query_raw(
                    'kube_node_status_condition{condition="Ready",status="false"} == 1'
                )
                bad_nodes = sorted({
                    (row.get("metric", {}) or {}).get("node")
                    for row in notready
                    if (row.get("metric", {}) or {}).get("node")
                })
                if bad_nodes:
                    conds = self.prometheus_client.query_raw(
                        'kube_node_status_condition{condition=~"MemoryPressure|DiskPressure|'
                        'PIDPressure|NetworkUnavailable",status="true"} == 1'
                    )
                    pressures = sorted({
                        row["metric"].get("condition")
                        for row in conds
                        if (row.get("metric", {}) or {}).get("node") in bad_nodes
                        and row["metric"].get("condition")
                    })
                    detail = (": " + ", ".join(pressures)) if pressures else ""
                    parts.append(f"node(s) NotReady [{', '.join(bad_nodes)}]{detail}")
        except Exception as exc:
            logger.debug("root-cause collection failed for %s/%s: %s", namespace, app, exc)
            return None
        return "; ".join(parts) if parts else None

    @staticmethod
    def _finite(value: float | int | None, default: float = 0.0) -> float:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return default
        return numeric if math.isfinite(numeric) else default
