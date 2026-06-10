"""
Autonomous AIOps Monitor — runs on iacamunda VM.

Independently monitors the entire K8s cluster without relying on the backend.
Discovers apps via Prometheus, runs proactive anomaly detection every N seconds,
and triggers backend incident creation when anomalies are detected.

Cycle:
  1. Query Prometheus for all active (namespace, container) pairs
  2. For each app: ProactiveService.predict_proactive()
  3. Debounce: anomaly must persist for autonomous_monitor_anomaly_streak consecutive cycles
  4. Multi-signal correlation: if 2+ apps in the same namespace are anomalous
     → emit single MULTI_SIGNAL_DEGRADATION instead of N separate incidents
  5. If severity HIGH/CRITICAL predicted: POST /api/ai/predict to backend (all IPs in parallel)
  6. Backend creates incident → triggers Camunda workflow → SSE notification
  7. Prometheus watchdog: if Prometheus itself is unreachable → VM_DOWN incident
  8. Orphan cleanup: close IN_PROGRESS incidents whose app no longer exists in Prometheus
"""

import asyncio
import json
import logging
import os
import time
from collections import deque
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

# Proactive JWT refresh: re-acquire 5 minutes before the local cache expires.
# The backend JWT is 24 h; we refresh every 50 min so the token is never stale.
_JWT_CACHE_SECONDS = 50 * 60
_JWT_REFRESH_EARLY_SECS = 5 * 60


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
        # Bounded deque — never grows beyond 100 entries (OOM protection)
        self._recent_errors: deque[str] = deque(maxlen=100)

        # (namespace, app) → monotonic timestamp when cooldown expires.
        # Persisted to disk so cooldowns survive service restarts and prevent
        # incident storms immediately after an unplanned restart.
        # Default is /data (persistent volume on .161) — NOT /tmp which is ephemeral.
        self._cooldown_file = os.environ.get(
            "AIOPS_COOLDOWN_STATE_FILE", "/data/helios_cooldowns.json"
        )
        self._cooldowns: dict[tuple[str, str], float] = self._load_cooldowns()

        # Debounce: track how many consecutive cycles each app has shown an anomaly.
        # An incident is created only when streak >= settings.autonomous_monitor_anomaly_streak.
        self._anomaly_streak: dict[tuple[str, str], int] = {}

        # Prometheus liveness — tracks consecutive failures to detect .160 outage
        self._prometheus_consecutive_failures: int = 0
        self._prometheus_down_incident_created: bool = False

    # ------------------------------------------------------------------
    # Cooldown persistence
    # ------------------------------------------------------------------

    def _load_cooldowns(self) -> dict[tuple[str, str], float]:
        """Restore unexpired cooldowns from the state file on startup."""
        try:
            if not os.path.exists(self._cooldown_file):
                return {}
            with open(self._cooldown_file) as f:
                raw: dict[str, float] = json.load(f)
            now = time.monotonic()
            # The file stores wall-clock expiry (epoch seconds) so the values
            # remain valid across restarts (monotonic resets on reboot).
            wall_now = time.time()
            result: dict[tuple[str, str], float] = {}
            for key, wall_expiry in raw.items():
                remaining = wall_expiry - wall_now
                if remaining > 0:
                    ns, app = key.split("/", 1)
                    result[(ns, app)] = now + remaining
            logger.info("Restored %d active cooldown(s) from %s", len(result), self._cooldown_file)
            return result
        except Exception as exc:
            logger.warning("Could not load cooldown state (%s) — starting fresh", exc)
            return {}

    def _save_cooldowns(self) -> None:
        """Persist active cooldowns as wall-clock expiry times so they survive restarts."""
        try:
            now_mono = time.monotonic()
            wall_now = time.time()
            serialisable: dict[str, float] = {
                f"{ns}/{app}": wall_now + (expires - now_mono)
                for (ns, app), expires in self._cooldowns.items()
                if expires > now_mono
            }
            with open(self._cooldown_file, "w") as f:
                json.dump(serialisable, f)
        except Exception as exc:
            logger.warning("Could not save cooldown state: %s", exc)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        if not self._settings.autonomous_monitor_enabled:
            logger.info("Autonomous monitor disabled (AIOPS_AUTONOMOUS_MONITOR_ENABLED=false)")
            return
        # Warn loudly if default credentials are in use — must be overridden in production
        if self._settings.autonomous_monitor_password in ("admin-change-me", "admin", ""):
            logger.warning(
                "SECURITY: autonomous_monitor_password is set to a default/weak value. "
                "Set AIOPS_AUTONOMOUS_MONITOR_PASSWORD env var to a strong secret before deploying."
            )
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="autonomous-monitor")
        logger.info(
            "Autonomous monitor started — interval=%ds cooldown=%dm streak=%d",
            self._settings.autonomous_monitor_interval_seconds,
            self._settings.autonomous_monitor_cooldown_minutes,
            self._settings.autonomous_monitor_anomaly_streak,
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

        # Prune streak entries for apps that are no longer visible in Prometheus.
        # Keeps memory bounded when deployments are deleted or scaled to zero mid-streak.
        current_keys = set(apps)
        stale = [k for k in self._anomaly_streak if k not in current_keys]
        for k in stale:
            self._anomaly_streak.pop(k, None)
        if stale:
            logger.debug("Pruned %d stale streak entries for gone apps", len(stale))

        # Orphan cleanup: close incidents for apps that no longer exist
        known_apps = {app for _, app in apps}
        await self._cleanup_orphan_incidents(known_apps)

        anomalies = 0
        streak_needed = self._settings.autonomous_monitor_anomaly_streak

        # --- Per-app analysis with debounce ---
        # An anomaly must persist for `streak_needed` consecutive cycles before
        # creating an incident. This prevents false positives from transient spikes.
        app_results: list[tuple[str, str, str]] = []  # (namespace, app, explanation)
        for namespace, app in apps:
            if self._in_cooldown(namespace, app):
                logger.debug("Cooldown active for %s/%s, skipping", namespace, app)
                # Reset streak during cooldown so it re-accumulates after cooldown expires
                self._anomaly_streak.pop((namespace, app), None)
                continue

            _timeout = self._settings.autonomous_monitor_interval_seconds * 0.8
            try:
                explanation = await asyncio.wait_for(
                    loop.run_in_executor(None, self._check_app, namespace, app),
                    timeout=_timeout,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "check_app timeout (%.0fs) for %s/%s — skipping cycle",
                    _timeout, namespace, app,
                )
                explanation = None

            if explanation:
                streak = self._anomaly_streak.get((namespace, app), 0) + 1
                self._anomaly_streak[(namespace, app)] = streak
                if streak >= streak_needed:
                    app_results.append((namespace, app, explanation))
                else:
                    logger.debug(
                        "Anomaly debounce for %s/%s (streak=%d/%d) — waiting for confirmation",
                        namespace, app, streak, streak_needed,
                    )
            else:
                # No anomaly this cycle: reset the streak counter
                self._anomaly_streak.pop((namespace, app), None)

        # --- Multi-signal correlation window ---
        # Group by namespace: if 2+ apps are anomalous in the same namespace
        # within the same cycle → correlated infra cause.
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
                            self._anomaly_streak.pop((ns, app), None)
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
                    self._anomaly_streak.pop((ns_val, app), None)
                    logger.info(
                        "Incident triggered for %s/%s — cooldown %dm",
                        ns_val, app, self._settings.autonomous_monitor_cooldown_minutes,
                    )

        self._last_anomalies_found = anomalies
        if anomalies > 0:
            self._save_cooldowns()
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
    # Prometheus watchdog (without Alertmanager)
    # ------------------------------------------------------------------

    async def _check_prometheus_health(self, loop) -> None:
        """
        Detect Prometheus (.160) outage purely from the AI side — no Alertmanager.
        After prometheus_failure_threshold consecutive query failures, create a
        MONITORING_DOWN incident. Clears the flag when Prometheus recovers.
        """
        _threshold = self._settings.prometheus_failure_threshold
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
                self._prometheus_consecutive_failures, _threshold, exc,
            )
            if (
                self._prometheus_consecutive_failures >= _threshold
                and not self._prometheus_down_incident_created
                and not self._in_cooldown("infra", "monitoring-vm")
            ):
                logger.error(
                    "Prometheus down for %d consecutive cycles — creating MONITORING_DOWN incident",
                    _threshold,
                )
                triggered = await self._trigger_backend(
                    "infra", "monitoring-vm",
                    extra_context={"incidentType": "MONITORING_DOWN"},
                )
                if triggered:
                    self._prometheus_down_incident_created = True
                    self._incidents_triggered += 1
                    secs = self._settings.autonomous_monitor_cooldown_minutes * 60 * 2
                    self._cooldowns[("infra", "monitoring-vm")] = time.monotonic() + secs
                    self._save_cooldowns()

    # ------------------------------------------------------------------
    # Orphan incident cleanup
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
                async with httpx.AsyncClient(timeout=8.0) as client:
                    resp = await client.get(f"{base}/api/incidents?page=0&size=500", headers=headers)
                    if resp.status_code == 401:
                        # JWT expired server-side before our estimate — clear cache
                        self._jwt_token = None
                        self._jwt_expires_at = 0.0
                        logger.debug("JWT rejected (401) in orphan cleanup — token cleared")
                        return
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
                            else:
                                logger.warning(
                                    "Failed to close orphan incident id=%s app=%s: backend returned %d",
                                    iid, app, r.status_code,
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
                self._save_cooldowns()
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
                async with httpx.AsyncClient(timeout=8.0) as client:
                    resp = await client.get(f"{base}/api/incidents?page=0&size=500", headers=headers)
                    if resp.status_code == 401:
                        self._jwt_token = None
                        self._jwt_expires_at = 0.0
                        logger.debug("JWT rejected (401) in node-resolve — token cleared")
                        return
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
                                self._save_cooldowns()  # persist removal so restart doesn't reload stale cooldown
                                logger.info("Auto-resolved NODE_NOT_READY incident id=%s — node %s recovered", iid, node)
                            else:
                                logger.warning(
                                    "Failed to close NODE_NOT_READY incident id=%s node=%s: %d",
                                    iid, node, r.status_code,
                                )
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
    # Backend notification — all IPs tried concurrently (FIRST_COMPLETED)
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

        All configured backend IPs are tried concurrently; we return True on the
        first successful response and cancel the remaining tasks to avoid duplicate
        incident creation.  A 401 clears the JWT cache so the next cycle re-authenticates.
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

        ips = [
            ip.strip()
            for ip in self._settings.autonomous_monitor_backend_ips.split(",")
            if ip.strip()
        ]

        async def _try(ip: str) -> bool:
            url = f"http://{ip}:30880/api/ai/predict"
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp = await client.post(url, json=payload, headers=headers)
                if resp.status_code == 401:
                    self._jwt_token = None
                    self._jwt_expires_at = 0.0
                    logger.debug("JWT rejected (401) by %s — token cache cleared", ip)
                    return False
                if resp.status_code in (200, 201):
                    data = resp.json()
                    logger.info(
                        "Backend ACK for %s/%s via %s: incidentCreated=%s id=%s",
                        namespace, app, ip,
                        data.get("incidentCreated"),
                        data.get("incidentId"),
                    )
                    return True
                logger.debug("Backend %s returned %d", ip, resp.status_code)
            except Exception as exc:
                logger.debug("Backend %s unreachable: %s", ip, exc)
            return False

        # Launch all IPs concurrently; return on first success and cancel the rest
        # to avoid duplicate incident creation from multiple backend replicas.
        tasks = [asyncio.create_task(_try(ip)) for ip in ips]
        pending = set(tasks)
        try:
            while pending:
                done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    if not task.exception() and task.result() is True:
                        for t in pending:
                            t.cancel()
                        return True
        except Exception as exc:
            logger.debug("Parallel backend trigger error: %s", exc)
        finally:
            for t in tasks:
                if not t.done():
                    t.cancel()

        logger.warning("All backend IPs unreachable for %s/%s", namespace, app)
        return False

    # ------------------------------------------------------------------
    # JWT management
    # ------------------------------------------------------------------

    async def _get_jwt(self) -> Optional[str]:
        # Proactively refresh 5 minutes before expiry to avoid mid-operation 401s
        if self._jwt_token and time.monotonic() < (self._jwt_expires_at - _JWT_REFRESH_EARLY_SECS):
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
                    self._jwt_expires_at = time.monotonic() + _JWT_CACHE_SECONDS
                    logger.debug("JWT acquired from %s (valid ~%dm)", ip, _JWT_CACHE_SECONDS // 60)
                    return self._jwt_token
                logger.debug("Auth at %s returned %d", ip, resp.status_code)
            except Exception as exc:
                logger.debug("Auth at %s failed: %s", ip, exc)

        logger.warning("JWT acquisition failed from all backend IPs")
        return None
