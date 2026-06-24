"""
VM Watchdog — auto-restarts VMware Workstation VMs when K8s nodes go NotReady.

Architecture:
  - Polls kubectl every 30s for node status
  - When a node is NotReady > GRACE_PERIOD_SECONDS, starts its VM via vmrun
  - Exposes POST /restart/{node_name} so the backend/Camunda can trigger restarts
  - Reports incidents to the backend REST API

Usage:
    python infra/scripts/vm_watchdog.py

Config: infra/scripts/vm_watchdog_config.json  (auto-created on first run if missing)
"""
from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Optional
import urllib.request, urllib.parse, base64

# ── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("vm_watchdog.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("vm_watchdog")

# ── Config ────────────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).parent
CONFIG_PATH = SCRIPT_DIR / "vm_watchdog_config.json"

DEFAULT_CONFIG = {
    "vmrun_path": "C:\\Program Files (x86)\\VMware\\VMware Workstation\\vmrun.exe",
    "kubeconfig_path": os.path.join(os.environ.get("TEMP", "/tmp"), "pfe-kubeconfig-master"),
    "kubectl_path": "kubectl",
    "grace_period_seconds": 120,
    "poll_interval_seconds": 30,
    "watchdog_port": 9099,
    # The control-plane node is detected by DIRECT probe (its API/SSH), never via kubectl —
    # because if the master is down, the API server is down and kubectl itself cannot answer.
    "control_plane_node": "k8s-master",
    "control_plane_api_port": 6443,
    "backend_api": "http://192.168.148.153:30808",
    "backend_nodeport": 30808,
    "backend_user": "operator",
    "backend_password": "operator-change-me",
    "nodes": {
        "k8s-master": {
            "ip": "192.168.148.153",
            "vmx_path": "C:\\PATH\\TO\\k8s-master.vmx",
            "ssh_user": "kube",
            "ssh_password": "kube"
        },
        "k8s-worker1": {
            "ip": "192.168.148.158",
            "vmx_path": "C:\\PATH\\TO\\k8s-worker1.vmx",
            "ssh_user": "kube",
            "ssh_password": "kube"
        },
        "k8s-worker2": {
            "ip": "192.168.148.155",
            "vmx_path": "C:\\PATH\\TO\\k8s-worker2.vmx",
            "ssh_user": "kube",
            "ssh_password": "kube"
        }
    }
}


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, indent=2), encoding="utf-8")
        log.warning("Config created at %s — EDIT vmx_path values before using!", CONFIG_PATH)
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    # Auto-discover vmx paths if still set to defaults
    for node_name, node_cfg in cfg.get("nodes", {}).items():
        if "PATH\\TO" in node_cfg.get("vmx_path", ""):
            discovered = discover_vmx(node_name)
            if discovered:
                node_cfg["vmx_path"] = discovered
                log.info("Auto-discovered VMX for %s: %s", node_name, discovered)
    return cfg


def discover_vmx(node_name: str) -> Optional[str]:
    """Search common VMware directories for a .vmx file matching the node name."""
    search_roots = [
        Path.home() / "Documents" / "Virtual Machines",
        Path("C:/VMs"),
        Path("D:/VMs"),
        Path("C:/Users") / os.environ.get("USERNAME", "") / "Virtual Machines",
        Path("D:/Virtual Machines"),
    ]
    for root in search_roots:
        if not root.exists():
            continue
        for vmx in root.rglob("*.vmx"):
            if node_name.lower() in vmx.stem.lower():
                return str(vmx)
    return None


# ── State tracker ─────────────────────────────────────────────────────────────

class NodeState:
    def __init__(self):
        self._lock = threading.Lock()
        self._not_ready_since: dict[str, float] = {}
        self._restarting: set[str] = set()
        self._last_restart: dict[str, float] = {}
        self._restart_count: dict[str, int] = {}

    def mark_not_ready(self, node: str) -> float:
        with self._lock:
            if node not in self._not_ready_since:
                self._not_ready_since[node] = time.time()
                log.warning("Node %s went NotReady", node)
            return time.time() - self._not_ready_since[node]

    def mark_ready(self, node: str):
        with self._lock:
            if node in self._not_ready_since:
                log.info("Node %s is Ready again", node)
                self._not_ready_since.pop(node, None)
            self._restarting.discard(node)

    def should_restart(self, node: str, grace: float, cooldown: float = 300) -> bool:
        with self._lock:
            if node in self._restarting:
                return False
            last = self._last_restart.get(node, 0)
            if time.time() - last < cooldown:
                return False
            return True

    def begin_restart(self, node: str):
        with self._lock:
            self._restarting.add(node)
            self._last_restart[node] = time.time()
            self._restart_count[node] = self._restart_count.get(node, 0) + 1

    def end_restart(self, node: str):
        with self._lock:
            self._restarting.discard(node)

    def restart_count(self, node: str) -> int:
        with self._lock:
            return self._restart_count.get(node, 0)


STATE = NodeState()


# ── kubectl helpers ───────────────────────────────────────────────────────────

def get_node_statuses(cfg: dict) -> dict[str, str]:
    """Returns {node_name: 'Ready'|'NotReady'|'Unknown'}"""
    kubeconfig = cfg.get("kubeconfig_path", "")
    kubectl = cfg.get("kubectl_path", "kubectl")
    cmd = [kubectl, "--kubeconfig", kubeconfig, "get", "nodes",
           "-o", "jsonpath={range .items[*]}{.metadata.name}={.status.conditions[-1:].type}={.status.conditions[-1:].status} {end}"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        statuses = {}
        for token in result.stdout.strip().split():
            parts = token.split("=")
            if len(parts) == 3:
                name, condition, status = parts
                if condition == "Ready":
                    statuses[name] = "Ready" if status == "True" else "NotReady"
                else:
                    statuses[name] = "Unknown"
        return statuses
    except Exception as exc:
        log.warning("kubectl get nodes failed: %s", exc)
        return {}


def is_reachable(ip: str, port: int, timeout: float = 4.0) -> bool:
    """Direct TCP probe — does NOT depend on the K8s API (used to detect the master itself)."""
    if not ip:
        return False
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except Exception:
        return False


def control_plane_alive(cfg: dict) -> bool:
    """The master is alive if its API server (6443) OR SSH (22) answers a direct TCP probe.
    This is independent of kubectl: when the master VM is down, the API is down too, so kubectl
    cannot be the detector for the control-plane node itself."""
    cp = cfg.get("control_plane_node", "k8s-master")
    ip = cfg.get("nodes", {}).get(cp, {}).get("ip", "")
    api_port = cfg.get("control_plane_api_port", 6443)
    return is_reachable(ip, api_port) or is_reachable(ip, 22)


# ── vmrun helpers ─────────────────────────────────────────────────────────────

def start_vm(vmrun_path: str, vmx_path: str) -> bool:
    if not Path(vmrun_path).exists():
        # Try alternate path
        alt = "C:\\Program Files\\VMware\\VMware Workstation\\vmrun.exe"
        if Path(alt).exists():
            vmrun_path = alt
        else:
            log.error("vmrun not found at %s", vmrun_path)
            return False

    if not Path(vmx_path).exists():
        log.error("VMX file not found: %s", vmx_path)
        return False

    log.info("Starting VM: %s", vmx_path)
    try:
        result = subprocess.run(
            [vmrun_path, "start", vmx_path, "nogui"],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0:
            log.info("VM started successfully: %s", vmx_path)
            return True
        log.error("vmrun failed (rc=%d): %s", result.returncode, result.stderr.strip())
        return False
    except subprocess.TimeoutExpired:
        log.warning("vmrun start timed out for %s — VM may still be starting", vmx_path)
        return True
    except Exception as exc:
        log.error("vmrun exception: %s", exc)
        return False


def wait_for_ssh(ip: str, user: str, password: str, timeout: int = 180) -> bool:
    """Wait until SSH responds on the VM (node booted)."""
    try:
        import paramiko
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                client = paramiko.SSHClient()
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                client.connect(ip, username=user, password=password, timeout=5)
                client.close()
                return True
            except Exception:
                time.sleep(5)
        return False
    except ImportError:
        log.warning("paramiko not available — skipping SSH check, waiting 60s")
        time.sleep(60)
        return True


# ── Backend reporting ─────────────────────────────────────────────────────────

def _candidate_apis(cfg: dict) -> list[str]:
    """Backend NodePort base URLs to try, in order. The configured backend_api comes first, then
    EVERY node IP at the NodePort — so a VM_DOWN incident still reaches the backend when the master
    is down (its NodePort is unreachable, but a worker's NodePort serves the same Service)."""
    apis: list[str] = []
    primary = cfg.get("backend_api", "").rstrip("/")
    if primary:
        apis.append(primary)
    port = cfg.get("backend_nodeport", 30808)
    for ncfg in cfg.get("nodes", {}).values():
        ip = ncfg.get("ip")
        if ip:
            url = f"http://{ip}:{port}"
            if url not in apis:
                apis.append(url)
    return apis


def report_to_backend(cfg: dict, node_name: str, success: bool, message: str):
    user = cfg.get("backend_user", "operator")
    pwd = cfg.get("backend_password", "operator-change-me")
    apis = _candidate_apis(cfg)
    if not apis:
        return
    creds = base64.b64encode(f"{user}:{pwd}".encode()).decode()
    status = "RESOLVED" if success else "OPEN"
    body = json.dumps({
        "title": f"VM_DOWN: {node_name}",
        "description": f"type=VM_DOWN action=RESTART_VM executionMode=AUTO_EXECUTE_CANDIDATE "
                       f"node={node_name} result={message}",
        "severity": "CRITICAL",
        "source": "AI",
        "namespace": "pfe",
        "app": node_name,
        "incidentType": "VM_DOWN",
        "status": status,
    }).encode()
    for api in apis:
        try:
            req = urllib.request.Request(
                f"{api}/api/incidents",
                data=body,
                headers={"Authorization": f"Basic {creds}", "Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=10)
            log.info("Reported VM_DOWN incident for %s to backend via %s", node_name, api)
            return
        except Exception as exc:
            log.debug("Report via %s failed: %s", api, exc)
    log.debug("Could not report VM_DOWN for %s to any backend endpoint", node_name)


# ── Restart logic ─────────────────────────────────────────────────────────────

def handle_vm_restart(cfg: dict, node_name: str):
    STATE.begin_restart(node_name)
    node_cfg = cfg["nodes"].get(node_name, {})
    vmrun = cfg.get("vmrun_path", "")
    vmx = node_cfg.get("vmx_path", "")
    ip = node_cfg.get("ip", "")
    ssh_user = node_cfg.get("ssh_user", "kube")
    ssh_pwd = node_cfg.get("ssh_password", "kube")

    log.info("=== AUTO-RESTART triggered for node %s (restart #%d) ===",
             node_name, STATE.restart_count(node_name) + 1)

    success = start_vm(vmrun, vmx)
    if success and ip:
        log.info("Waiting for %s to boot (SSH check on %s)...", node_name, ip)
        booted = wait_for_ssh(ip, ssh_user, ssh_pwd, timeout=180)
        if booted:
            log.info("Node %s is back online!", node_name)
            report_to_backend(cfg, node_name, True, "VM restarted and SSH confirmed")
        else:
            log.warning("Node %s did not respond to SSH after restart", node_name)
            report_to_backend(cfg, node_name, False, "VM started but SSH timed out")
    else:
        report_to_backend(cfg, node_name, False, "vmrun start failed")

    STATE.end_restart(node_name)


# ── Main polling loop ─────────────────────────────────────────────────────────

def polling_loop(cfg: dict):
    grace = cfg.get("grace_period_seconds", 120)
    interval = cfg.get("poll_interval_seconds", 30)
    managed_nodes = set(cfg.get("nodes", {}).keys())
    cp_node = cfg.get("control_plane_node", "k8s-master")

    log.info("Watchdog started. Monitoring nodes: %s (control-plane: %s)", list(managed_nodes), cp_node)
    log.info("Grace period: %ds | Poll interval: %ds", grace, interval)

    def trigger_restart(node):
        threading.Thread(target=handle_vm_restart, args=(cfg, node), daemon=True).start()

    while True:
        try:
            # 1) CONTROL-PLANE (master): detected by DIRECT probe, never via kubectl — because if
            #    the master is down the API is down and kubectl cannot answer. This is also why we
            #    must NOT mark the workers NotReady from an empty kubectl result (would false-restart
            #    healthy workers whenever the master is merely unreachable).
            cp_alive = control_plane_alive(cfg)
            if cp_node in managed_nodes:
                if cp_alive:
                    STATE.mark_ready(cp_node)
                else:
                    secs = STATE.mark_not_ready(cp_node)
                    log.warning("Control-plane %s unreachable for %.0fs (grace=%ds)", cp_node, secs, grace)
                    if secs >= grace and STATE.should_restart(cp_node, grace):
                        trigger_restart(cp_node)

            # 2) WORKERS: assessed via kubectl, which is only trustworthy when the API is up.
            statuses = get_node_statuses(cfg) if cp_alive else {}
            for node in managed_nodes:
                if node == cp_node:
                    continue
                if not cp_alive:
                    # API down -> we cannot judge worker health; skip (fix the master first).
                    continue
                status = statuses.get(node, "Unknown")
                if status == "Ready":
                    STATE.mark_ready(node)
                elif status in ("NotReady", "Unknown"):
                    not_ready_secs = STATE.mark_not_ready(node)
                    log.debug("Node %s NotReady for %.0fs (grace=%ds)", node, not_ready_secs, grace)
                    if not_ready_secs >= grace and STATE.should_restart(node, grace):
                        trigger_restart(node)
        except Exception as exc:
            log.error("Polling error: %s", exc)
        time.sleep(interval)


# ── HTTP API server ───────────────────────────────────────────────────────────

class WatchdogHandler(BaseHTTPRequestHandler):
    cfg: dict = {}

    def log_message(self, fmt, *args):
        pass  # suppress default HTTP logging

    def do_GET(self):
        if self.path == "/health":
            self._json(200, {"status": "ok", "timestamp": datetime.utcnow().isoformat()})
        elif self.path == "/status":
            nodes = {}
            for node in self.cfg.get("nodes", {}):
                nodes[node] = {"restart_count": STATE.restart_count(node)}
            self._json(200, {"nodes": nodes})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        # POST /restart/{node_name}  — manual or Camunda-triggered restart
        if self.path.startswith("/restart/"):
            node_name = self.path.split("/restart/", 1)[1].rstrip("/")
            if node_name not in self.cfg.get("nodes", {}):
                self._json(404, {"error": f"unknown node: {node_name}"})
                return
            log.info("Manual restart requested for node %s via HTTP API", node_name)
            thread = threading.Thread(
                target=handle_vm_restart,
                args=(self.cfg, node_name),
                daemon=True,
            )
            thread.start()
            self._json(202, {"status": "restart_initiated", "node": node_name})
        else:
            self._json(404, {"error": "not found"})

    def _json(self, code: int, data: dict):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)


def start_http_server(cfg: dict):
    port = cfg.get("watchdog_port", 9099)

    class Handler(WatchdogHandler):
        pass
    Handler.cfg = cfg

    server = HTTPServer(("0.0.0.0", port), Handler)
    log.info("Watchdog HTTP API listening on port %d", port)
    log.info("  GET  http://localhost:%d/health", port)
    log.info("  GET  http://localhost:%d/status", port)
    log.info("  POST http://localhost:%d/restart/{{node_name}}", port)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    log.info("=== PFE-Orange VM Watchdog starting ===")
    cfg = load_config()

    # Validate vmx paths
    unconfigured = []
    for node, ncfg in cfg.get("nodes", {}).items():
        vmx = ncfg.get("vmx_path", "")
        if "PATH\\TO" in vmx:
            unconfigured.append(node)
    if unconfigured:
        log.warning(
            "VMX paths not configured for: %s\n"
            "Edit %s and set the correct vmx_path for each node.",
            unconfigured, CONFIG_PATH,
        )

    start_http_server(cfg)
    polling_loop(cfg)


if __name__ == "__main__":
    main()
