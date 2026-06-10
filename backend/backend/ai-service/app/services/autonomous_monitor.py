"""
Autonomous AIOps Monitor — runs on iacamunda VM.

Independently monitors the entire K8s cluster without relying on the backend.
Discovers apps via Prometheus, runs proactive anomaly detection every N seconds,
and triggers backend incident creation when anomalies are detected.

Cycle:
  1. Query Prometheus for all active (namespace, container) pairs
  2. For each app: ProactiveService.predict_proactive()
  3. Multi-signal correlation: if 2+ metrics breach threshold simultaneously
     → emit single MULTI_SIGNAL_DEGRADATION instead of N separate incidents
  4. If severity HIGH/CRITICAL predicted: POST /api/ai/predict to backend
  5. Backend creates incident → triggers Camunda workflow → SSE notification
  6. Prometheus watchdog: if Prometheus itself is unreachable → VM_DOWN incident
  7. Orphan cleanup: close IN_PROGRESS incidents whose app no longer exists in Prometheus
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
    "local-path-storage", "metallb-system", "cnpg-system",
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
        self._maintenance_mode: bool = settings.maintenance_mode
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

        # Prometheus liveness — tracks consecutive failures to detect .160 outage
        self._prometheus_consecutive_failures: int = 0
        self._prometheus_down_incident_created: bool = False
        _PROMETHEUS_FAILURE_THRESHOLD = 3  # 3 consecutive misses → incident

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

    def set_maintenance_mode(self, enabled: bool) -> None:
        self._maintenance_mode = enabled
        logger.info("Maintenance mode %s", "ENABLED — monitor paused" if enabled else "DISABLED — monitor resuming")

    def is_maintenance_mode(self) -> bool:
        return self._maintenance_mode

    async def _run_cycle(self) -> None:
        if self._maintenance_mode:
            logger.info("Maintenance mode active — skipping monitor cycle %d", self._total_cycles + 1)
            return

        self._total_cycles += 1
        self._last_run_utc = datetime.now(timezone.utc).isoformat()
        loop = asyncio.get_event_loop()

        # Prometheus watchdog: detect .160 outage without Alertmanager
        await self._check_prometheus_health(loop)

        # Cluster/infra layer: detect NotReady nodes (NODE_NOT_READY) independently
        # of app discovery — a node can be down even when every app looks fine.
        await self._check_and_trigger_nodes(loop)

        apps = await loop.run_in_executor(None, self._discover_apps)
        self._last_apps_analyzed = len(apps)

        if not apps:
            logger.debug("Monitor cycle %d: no apps in Prometheus", self._total_cycles)
            return

        logger.info("Monitor cycle %d: %d app(s) to analyze", self._total_cycles, len(apps))

        # Orphan cleanup: close incidents for apps that no longer exist
        known_apps = {app for _, app in apps}
        await self._cleanup_orphan_incidents(known_apps)

        anomalies = 0

        # --- Multi-signal correlation window ---
        # Collect per-app proactive results first, then look for correlated patterns
        # before deciding how many incidents to create.
        app_results: list[tuple[str, str, str]] = []  # (namespace, app, explanation)
        for namespace, app in apps:
            if self._in_cooldown(namespace, app):
                logger.debug("Cooldown active for %s/%s, skipping", namespace, app)
                continue

            explanation = await loop.run_in_executor(
                None, self._check_app, namespace, app
            )
            if explanation:
                app_results.append((namespace, app, explanation))

        # Group by namespace: if 2+ apps in the same namespace are anomalous
        # within the same cycle, treat as correlated (shared infra cause).
        ns_groups: dict[str, list[tuple[str, str, str]]] = {}
        for ns, app, expl in app_results:
            ns_groups.setdefault(ns, []).append((ns, app, expl))

        for ns, group in ns_groups.items():
            if len(group) >= 2:
                # Correlated: create one MULTI_SIGNAL_DEGRADATION per namespace
                affected = [app for _, app, _ in group]
                logger.warning(
                    "MULTI-SIGNAL CORRELATION in %s: %d apps affected simultaneously: %s",
                    ns, len(affected), affected,
                )
                # Trigger on the first app but annotate all in explanation
                _, first_app, _ = group[0]
                if not self._in_cooldown(ns, f"__correlated_{ns}"):
                    triggered = await self._trigger_backend(
                        ns, first_app,
                        extra_context={
                            "correlatedApps": affected,
                            "correlationType": "MULTI_SIGNAL_DEGRADATION",
                        },
                    )
                    if triggered:
                        anomalies += 1
                        self._incidents_triggered += 1
                        secs = self._settings.autonomous_monitor_cooldown_minutes * 60
                        self._cooldowns[(ns, f"__correlated_{ns}")] = time.monotonic() + secs
                        for _, app, _ in group:
                            self._cooldowns[(ns, app)] = time.monotonic() + secs
            else:
                # Isolated anomaly — normal single-app incident
                ns_val, app, explanation = group[0]
                anomalies += 1
                logger.warning("ANOMALY %s/%s: %s", ns_val, app, explanation)
                triggered = await self._trigger_backend(ns_val, app)
                if triggered:
                    self._incidents_triggered += 1
                    cooldown_secs = self._settings.autonomous_monitor_cooldown_minutes * 60
                    self._cooldowns[(ns_val, app)] = time.monotonic() + cooldown_secs
                    logger.info(
                        "Incident triggered for %s/%s — cooldown %dm",
                        ns_val, app, self._settings.autonomous_monitor_cooldown_minutes,
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

        # HELIOS's own components — never monitor them (no self-incidents).
        excluded = {
            a.strip().lower()
            for a in (self._settings.autonomous_monitor_exclude_apps or "").split(",")
            if a.strip()
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
            if container.lower() in excluded:
                continue
            key = (ns, container)
            if key not in seen:
                seen.add(key)
                apps.append(key)

        return apps

    # ------------------------------------------------------------------
    # Prometheus watchdog (L7 — without Alertmanager)
    # ------------------------------------------------------------------

    async def _check_prometheus_health(self, loop) -> None:
        """
        Detect Prometheus (.160) outage purely from the AI side — no Alertmanager.
        After _PROMETHEUS_FAILURE_THRESHOLD consecutive query failures, create a
        MONITORING_DOWN incident. Clears the flag when Prometheus recovers.
        """
        _THRESHOLD = 3
        try:
            result = await loop.run_in_executor(
                None,
                lambda: self._prometheus.query_raw("up{job='prometheus'}"),
            )
            # Any non-exception response means Prometheus is reachable
            self._prometheus_consecutive_failures = 0
            if self._prometheus_down_incident_created:
                self._prometheus_down_incident_created = False
                logger.info("Prometheus recovered — clearing MONITORING_DOWN flag")
        except Exception as exc:
            self._prometheus_consecutive_failures += 1
            logger.warning(
                "Prometheus unreachable (%d/%d): %s",
                self._prometheus_consecutive_failures, _THRESHOLD, exc,
            )
            if (
                self._prometheus_consecutive_failures >= _THRESHOLD
                and not self._prometheus_down_incident_created
                and not self._in_cooldown("infra", "monitoring-vm")
            ):
                logger.error("Prometheus down for %d consecutive cycles — creating MONITORING_DOWN incident", _THRESHOLD)
                triggered = await self._trigger_backend(
                    "infra", "monitoring-vm",
                    extra_context={"incidentType": "MONITORING_DOWN"},
                )
                if triggered:
                    self._prometheus_down_incident_created = True
                    self._incidents_triggered += 1
                    secs = self._settings.autonomous_monitor_cooldown_minutes * 60 * 2
                    self._cooldowns[("infra", "monitoring-vm")] = time.monotonic() + secs

    # ------------------------------------------------------------------
    # Orphan incident cleanup (L24)
    # ------------------------------------------------------------------

    async def _cleanup_orphan_incidents(self, active_apps: set) -> None:
        """
        Close IN_PROGRESS incidents whose app no longer appears in Prometheus metrics.
        An app absent from Prometheus for >1 cycle is considered gone (deleted/scaled-to-0).
        NODE_NOT_READY incidents are handled separately by _resolve_recovered_node_incidents.
        """
        token = await self._get_jwt()
        if not token:
            return
        headers = {"Authorization": f"Bearer {token}"}
        for ip in self._settings.autonomous_monitor_backend_ips.split(","):
            ip = ip.strip()
            if not ip:
                continue
            base = f"http://{ip}:30880"
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.get(f"{base}/api/incidents?page=0&size=100", headers=headers)
                    if resp.status_code != 200:
                        continue
                    data = resp.json()
                    items = data.get("content", data) if isinstance(data, dict) else data
                    for inc in (items or []):
                        itype = inc.get("incidentType", "")
                        status = inc.get("status", "")
                        app = inc.get("app", "")
                        iid = inc.get("id")
                        if (
                            status not in ("RESOLVED", "CLOSED", "AUTO_RESOLVED")
                            and itype != "NODE_NOT_READY"
                            and app
                            and app not in active_apps
                            and iid
                        ):
                            r = await client.put(
                                f"{base}/api/incidents/{iid}/status/RESOLVED",
                                headers=headers,
                            )
                            if r.status_code in (200, 201, 204):
                                logger.info(
                                    "Auto-resolved orphan incident id=%s app=%s (app absent from cluster)",
                                    iid, app,
                                )
                return
            except Exception as exc:
                logger.debug("Orphan cleanup via %s failed: %s", ip, exc)

    # ------------------------------------------------------------------
    # Node / cluster outage detection (NODE_NOT_READY)
    # ------------------------------------------------------------------

    def _discover_not_ready_nodes(self) -> list[str]:
        """Query Prometheus (kube-state-metrics) for nodes whose Ready condition is false/unknown."""
        results = self._prometheus.query_raw(
            'kube_node_status_condition{condition="Ready",status=~"false|unknown"} == 1'
        )
        nodes: list[str] = []
        for item in results:
            node = item.get("metric", {}).get("node", "")
            if node and node not in nodes:
                nodes.append(node)
        return nodes

    async def _check_and_trigger_nodes(self, loop) -> None:
        """Create NODE_NOT_READY incidents for down nodes, and auto-resolve them when nodes recover."""
        try:
            nodes = await loop.run_in_executor(None, self._discover_not_ready_nodes)
        except Exception as exc:
            logger.debug("Node check failed: %s", exc)
            return
        for node in nodes:
            if self._in_cooldown("cluster", node):
                continue
            logger.warning("NODE NotReady detected: %s — creating NODE_NOT_READY incident", node)
            if await self._trigger_backend("kube-system", node):
                self._incidents_triggered += 1
                secs = self._settings.autonomous_monitor_cooldown_minutes * 60
                self._cooldowns[("cluster", node)] = time.monotonic() + secs
        # Mirror: auto-resolve NODE_NOT_READY incidents whose node is Ready again.
        await self._resolve_recovered_node_incidents(set(nodes))

    async def _resolve_recovered_node_incidents(self, not_ready: set) -> None:
        """Close open NODE_NOT_READY incidents for nodes that are no longer NotReady."""
        token = await self._get_jwt()
        if not token:
            return
        headers = {"Authorization": f"Bearer {token}"}
        for ip in self._settings.autonomous_monitor_backend_ips.split(","):
            ip = ip.strip()
            if not ip:
                continue
            base = f"http://{ip}:30880"
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.get(f"{base}/api/incidents?page=0&size=100", headers=headers)
                    if resp.status_code != 200:
                        continue
                    data = resp.json()
                    items = data.get("content", data) if isinstance(data, dict) else data
                    for inc in (items or []):
                        node = inc.get("app")
                        status = inc.get("status")
                        if (inc.get("incidentType") == "NODE_NOT_READY"
                                and status not in ("RESOLVED", "CLOSED", "AUTO_RESOLVED")
                                and node and node not in not_ready):
                            iid = inc.get("id")
                            r = await client.put(f"{base}/api/incidents/{iid}/status/RESOLVED", headers=headers)
                            if r.status_code in (200, 201, 204):
                                self._cooldowns.pop(("cluster", node), None)
                                logger.info("Auto-resolved NODE_NOT_READY incident id=%s — node %s recovered", iid, node)
                return  # handled via the first reachable backend
            except Exception as exc:
                logger.debug("Node auto-resolve via %s failed: %s", ip, exc)

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

    async def _trigger_backend(
        self,
        namespace: str,
        app: str,
        extra_context: dict | None = None,
    ) -> bool:
        """
        Call backend POST /api/ai/predict so the backend creates the incident,
        triggers the Camunda workflow, and pushes an SSE notification to the UI.
        extra_context is merged into the payload for enriched incident creation
        (e.g. correlatedApps, correlationType, incidentType override).
        """
        token = await self._get_jwt()
        if not token:
            logger.warning("Cannot trigger backend for %s/%s: no JWT", namespace, app)
            return False

        payload: dict = {
            "namespace": namespace,
            "app": app,
            "windowMinutes": 5,
            "createIncident": True,
        }
        if extra_context:
            payload.update(extra_context)
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

        for ip in self._settings.autonomous_monitor_backend_ips.split(","):
            ip = ip.strip()
            if not ip:
                continue
            url = f"http://{ip}:30880/api/ai/predict"
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
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
