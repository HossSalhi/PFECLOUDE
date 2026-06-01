"""
Autonomous AIOps Monitor — runs on iacamunda VM.

Independently monitors the entire K8s cluster without relying on the backend.
Discovers apps via Prometheus, runs proactive anomaly detection every N seconds,
and triggers backend incident creation when anomalies are detected.

Cycle:
  1. Query Prometheus for all active (namespace, container) pairs
  2. For each app: ProactiveService.predict_proactive()
  3. If severity HIGH/CRITICAL predicted: POST /api/ai/predict to backend
  4. Backend creates incident → triggers Camunda workflow → SSE notification
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import httpx

from app.clients.prometheus_client import PrometheusClient
from app.core.config import Settings
from app.services.proactive_service import ProactiveService

logger = logging.getLogger(__name__)

_SYSTEM_NAMESPACES = frozenset({
    "kube-system", "kube-public", "kube-node-lease",
    "monitoring", "cert-manager", "ingress-nginx",
    "local-path-storage", "metallb-system",
})


@dataclass
class MonitorCycleStatus:
    enabled: bool
    running: bool
    total_cycles: int
    incidents_triggered: int
    last_run_utc: Optional[str]
    last_apps_analyzed: int
    last_anomalies_found: int
    recent_errors: list[str] = field(default_factory=list)
    cooldowns: dict[str, str] = field(default_factory=dict)


class AutonomousMonitor:
    """
    Background service started at app startup.
    Proactively polls metrics and calls the backend when incidents are predicted.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._prometheus = PrometheusClient(
            base_url=settings.prometheus_base_url,
            timeout_seconds=settings.prometheus_timeout_seconds,
        )
        self._proactive = ProactiveService(settings)
        self._task: Optional[asyncio.Task] = None
        self._running = False

        self._jwt_token: Optional[str] = None
        self._jwt_expires_at: float = 0.0

        self._total_cycles = 0
        self._incidents_triggered = 0
        self._last_run_utc: Optional[str] = None
        self._last_apps_analyzed = 0
        self._last_anomalies_found = 0
        self._recent_errors: list[str] = []

        # (namespace, app) → monotonic timestamp when cooldown expires
        self._cooldowns: dict[tuple[str, str], float] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        if not self._settings.autonomous_monitor_enabled:
            logger.info("Autonomous monitor disabled (AIOPS_AUTONOMOUS_MONITOR_ENABLED=false)")
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="autonomous-monitor")
        logger.info(
            "Autonomous monitor started — interval=%ds cooldown=%dm",
            self._settings.autonomous_monitor_interval_seconds,
            self._settings.autonomous_monitor_cooldown_minutes,
        )

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Autonomous monitor stopped")

    def get_status(self) -> MonitorCycleStatus:
        cooldown_info = {
            f"{ns}/{app}": datetime.fromtimestamp(
                time.time() + (expires - time.monotonic()), tz=timezone.utc
            ).isoformat()
            for (ns, app), expires in self._cooldowns.items()
            if time.monotonic() < expires
        }
        return MonitorCycleStatus(
            enabled=self._settings.autonomous_monitor_enabled,
            running=self._running,
            total_cycles=self._total_cycles,
            incidents_triggered=self._incidents_triggered,
            last_run_utc=self._last_run_utc,
            last_apps_analyzed=self._last_apps_analyzed,
            last_anomalies_found=self._last_anomalies_found,
            recent_errors=list(self._recent_errors[-5:]),
            cooldowns=cooldown_info,
        )

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def _loop(self) -> None:
        await asyncio.sleep(30)  # wait for app fully ready
        while self._running:
            try:
                await self._run_cycle()
            except Exception as exc:
                msg = f"{datetime.now(timezone.utc).isoformat()} — {exc}"
                logger.exception("Monitor cycle error: %s", exc)
                self._recent_errors.append(msg)
            await asyncio.sleep(self._settings.autonomous_monitor_interval_seconds)

    async def _run_cycle(self) -> None:
        self._total_cycles += 1
        self._last_run_utc = datetime.now(timezone.utc).isoformat()
        loop = asyncio.get_event_loop()

        apps = await loop.run_in_executor(None, self._discover_apps)
        self._last_apps_analyzed = len(apps)

        if not apps:
            logger.debug("Monitor cycle %d: no apps in Prometheus", self._total_cycles)
            return

        logger.info("Monitor cycle %d: %d app(s) to analyze", self._total_cycles, len(apps))

        anomalies = 0
        for namespace, app in apps:
            if self._in_cooldown(namespace, app):
                logger.debug("Cooldown active for %s/%s, skipping", namespace, app)
                continue

            explanation = await loop.run_in_executor(
                None, self._check_app, namespace, app
            )

            if explanation:
                anomalies += 1
                logger.warning("ANOMALY %s/%s: %s", namespace, app, explanation)
                triggered = await self._trigger_backend(namespace, app)
                if triggered:
                    self._incidents_triggered += 1
                    cooldown_secs = self._settings.autonomous_monitor_cooldown_minutes * 60
                    self._cooldowns[(namespace, app)] = time.monotonic() + cooldown_secs
                    logger.info(
                        "Incident triggered for %s/%s — cooldown %dm",
                        namespace, app, self._settings.autonomous_monitor_cooldown_minutes,
                    )

        self._last_anomalies_found = anomalies
        logger.info(
            "Monitor cycle %d done — %d app(s), %d anomaly(s), %d total incidents",
            self._total_cycles, len(apps), anomalies, self._incidents_triggered,
        )

    # ------------------------------------------------------------------
    # App discovery via Prometheus
    # ------------------------------------------------------------------

    def _discover_apps(self) -> list[tuple[str, str]]:
        """
        Query Prometheus for all active (namespace, container) pairs.
        Works for any app deployed on the cluster — no static config needed.
        """
        results = self._prometheus.query_raw(
            'group by (namespace, container) '
            '(rate(container_cpu_usage_seconds_total'
            '{container!="",container!="POD"}[5m]))'
        )

        allowed: set[str] = set()
        if self._settings.autonomous_monitor_namespaces:
            allowed = {
                ns.strip()
                for ns in self._settings.autonomous_monitor_namespaces.split(",")
                if ns.strip()
            }

        seen: set[tuple[str, str]] = set()
        apps: list[tuple[str, str]] = []
        for item in results:
            metric = item.get("metric", {})
            ns = metric.get("namespace", "")
            container = metric.get("container", "")
            if not ns or not container:
                continue
            if ns in _SYSTEM_NAMESPACES:
                continue
            if allowed and ns not in allowed:
                continue
            key = (ns, container)
            if key not in seen:
                seen.add(key)
                apps.append(key)

        return apps

    # ------------------------------------------------------------------
    # Analysis
    # ------------------------------------------------------------------

    def _check_app(self, namespace: str, app: str) -> Optional[str]:
        """Return anomaly explanation if HIGH/CRITICAL predicted, else None."""
        try:
            result = self._proactive.predict_proactive(namespace, app, window_minutes=10)
            if result.predicted_incident and result.severity in ("HIGH", "CRITICAL"):
                return result.explanation
        except Exception as exc:
            logger.debug("Analysis failed for %s/%s: %s", namespace, app, exc)
        return None

    def _in_cooldown(self, namespace: str, app: str) -> bool:
        expires = self._cooldowns.get((namespace, app), 0.0)
        return time.monotonic() < expires

    # ------------------------------------------------------------------
    # Backend notification
    # ------------------------------------------------------------------

    async def _trigger_backend(self, namespace: str, app: str) -> bool:
        """
        Call backend POST /api/ai/predict so the backend creates the incident,
        triggers the Camunda workflow, and pushes an SSE notification to the UI.
        """
        token = await self._get_jwt()
        if not token:
            logger.warning("Cannot trigger backend for %s/%s: no JWT", namespace, app)
            return False

        payload = {
            "namespace": namespace,
            "app": app,
            "windowMinutes": 5,
            "createIncident": True,
        }
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

        for ip in self._settings.autonomous_monitor_backend_ips.split(","):
            ip = ip.strip()
            if not ip:
                continue
            url = f"http://{ip}:30880/api/ai/predict"
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.post(url, json=payload, headers=headers)
                if resp.status_code in (200, 201):
                    data = resp.json()
                    logger.info(
                        "Backend ACK for %s/%s: incidentCreated=%s id=%s",
                        namespace, app,
                        data.get("incidentCreated"),
                        data.get("incidentId"),
                    )
                    return True
                logger.debug("Backend %s returned %d", ip, resp.status_code)
            except Exception as exc:
                logger.debug("Backend %s unreachable: %s", ip, exc)

        logger.warning("All backend IPs unreachable for %s/%s", namespace, app)
        return False

    async def _get_jwt(self) -> Optional[str]:
        if self._jwt_token and time.monotonic() < self._jwt_expires_at:
            return self._jwt_token

        for ip in self._settings.autonomous_monitor_backend_ips.split(","):
            ip = ip.strip()
            if not ip:
                continue
            url = f"http://{ip}:30880/api/auth/login"
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(
                        url,
                        json={
                            "username": self._settings.autonomous_monitor_username,
                            "password": self._settings.autonomous_monitor_password,
                        },
                    )
                if resp.status_code == 200:
                    data = resp.json()
                    self._jwt_token = data.get("accessToken")
                    self._jwt_expires_at = time.monotonic() + 55 * 60  # refresh before 1h expiry
                    logger.debug("JWT acquired from %s", ip)
                    return self._jwt_token
                logger.debug("Auth at %s returned %d", ip, resp.status_code)
            except Exception as exc:
                logger.debug("Auth at %s failed: %s", ip, exc)

        logger.warning("JWT acquisition failed from all backend IPs")
        return None
