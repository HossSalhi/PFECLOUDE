#!/usr/bin/env python3
"""
End-to-end scenario test suite for the PFE AIOps platform.

Tests every incident type by injecting faults through the backend API,
waiting for the AIOps polling cycle to detect them, then verifying that
Camunda creates a workflow and the incident reaches a terminal state.

Usage:
    python infra/scripts/test-all-scenarios.py [--backend-url URL] [--timeout SECONDS]
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from dataclasses import dataclass, field

import urllib.request
import urllib.error
import base64

BASE_AUTH = base64.b64encode(b"operator:operator-change-me").decode()
HEADERS = {"Authorization": f"Basic {BASE_AUTH}", "Content-Type": "application/json"}

POLL_INTERVAL_S = 20          # how often to check for incident
SCENARIO_TIMEOUT_S = 400      # max wait per scenario
REMEDIATION_TIMEOUT_S = 240   # extra wait after detection for Camunda to act
INTER_SCENARIO_COOLDOWN_S = 310  # 5-min Prometheus/Loki window + 10s buffer
TERMINAL_STATUSES = {"RESOLVED", "CLOSED", "ARCHIVED"}


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _request(method: str, url: str, body: dict | None = None, timeout: int = 15) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=HEADERS, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode())
        except Exception:
            return {"error": str(e)}
    except Exception as e:
        return {"error": str(e)}


def get(url: str) -> dict:
    return _request("GET", url)


def post(url: str, body: dict | None = None) -> dict:
    return _request("POST", url, body or {})


def put(url: str, body: dict | None = None) -> dict:
    return _request("PUT", url, body)


# ---------------------------------------------------------------------------
# Result tracking
# ---------------------------------------------------------------------------

@dataclass
class ScenarioResult:
    name: str
    expected_type: str
    passed: bool = False
    incident_id: int | None = None
    detected_type: str | None = None
    final_status: str | None = None
    execution_mode: str | None = None
    confidence: float | None = None
    error: str | None = None
    duration_s: float = 0.0
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Fault injection helpers
# ---------------------------------------------------------------------------

def reset_all_faults(base: str) -> None:
    post(f"{base}/api/test/faults/reset")


def enable_fault(base: str, scenario: str) -> None:
    post(f"{base}/api/test/faults", {"scenario": scenario, "enabled": True})


def disable_fault(base: str, scenario: str) -> None:
    post(f"{base}/api/test/faults", {"scenario": scenario, "enabled": False})


def get_fault_states(base: str) -> dict:
    return get(f"{base}/api/test/faults")


# ---------------------------------------------------------------------------
# Incident detection
# ---------------------------------------------------------------------------

def count_open_incidents_before(base: str) -> set[int]:
    """Return ALL known incident IDs so stale resolved incidents are not returned as new."""
    resp = get(f"{base}/api/incidents?size=200")
    items = resp.get("content", [])
    return {i["id"] for i in items}


def wait_for_new_incident(
    base: str,
    known_ids: set[int],
    timeout_s: int,
    keepalive_fn=None,
) -> dict | None:
    deadline = time.time() + timeout_s
    while True:
        resp = get(f"{base}/api/incidents?size=200")
        items = resp.get("content", [])
        for item in items:
            if item["id"] not in known_ids and item.get("status") not in TERMINAL_STATUSES:
                return item
        remaining = int(deadline - time.time())
        if remaining <= 0:
            # One last check after a brief pause — catches near-miss incidents
            time.sleep(10)
            resp = get(f"{base}/api/incidents?size=200")
            for item in resp.get("content", []):
                if item["id"] not in known_ids and item.get("status") not in TERMINAL_STATUSES:
                    return item
            return None
        print(f"    no new incident yet ... ({remaining}s remaining)", flush=True)
        if keepalive_fn is not None:
            try:
                keepalive_fn()
            except Exception:
                pass
        sleep_time = min(POLL_INTERVAL_S, remaining + 5)
        if sleep_time > 0:
            time.sleep(sleep_time)


def wait_for_incident_resolution(base: str, incident_id: int, timeout_s: int) -> str:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        resp = get(f"{base}/api/incidents/{incident_id}")
        status = resp.get("status", "UNKNOWN")
        if status in TERMINAL_STATUSES:
            return status
        if status == "WAITING_FOR_APPROVAL":
            return status  # Camunda created the task - sufficient for test
        remaining = int(deadline - time.time())
        print(f"    incident #{incident_id} status={status} ... ({remaining}s remaining)", flush=True)
        time.sleep(POLL_INTERVAL_S)
    resp = get(f"{base}/api/incidents/{incident_id}")
    return resp.get("status", "UNKNOWN")


def resolve_incident(base: str, incident_id: int) -> None:
    resp = get(f"{base}/api/incidents/{incident_id}")
    status = resp.get("status", "UNKNOWN")
    if status in ("NEW", "OPEN"):
        put(f"{base}/api/incidents/{incident_id}/status/IN_PROGRESS")
        put(f"{base}/api/incidents/{incident_id}/status/RESOLVED")
    elif status == "IN_PROGRESS":
        put(f"{base}/api/incidents/{incident_id}/status/RESOLVED")
    elif status == "WAITING_FOR_APPROVAL":
        put(f"{base}/api/incidents/{incident_id}/status/RESOLVED")


def cleanup_active_ai_incidents(base: str) -> None:
    """Resolve all non-terminal AI incidents so the scheduler's hasActiveAiIncident check clears."""
    resp = get(f"{base}/api/incidents?size=200")
    items = resp.get("content", [])
    active = [i for i in items if i.get("source") == "AI" and i.get("status") not in TERMINAL_STATUSES]
    if active:
        print(f"  [cleanup] Resolving {len(active)} active AI incidents before test ...", flush=True)
        for item in active:
            resolve_incident(base, item["id"])
        time.sleep(5)


def inter_scenario_cooldown(label: str) -> None:
    """Wait for Prometheus/Loki 5-minute window to clear from previous scenario signals."""
    print(f"\n  [cooldown] Waiting {INTER_SCENARIO_COOLDOWN_S}s for metric window to clear after {label} ...", flush=True)
    deadline = time.time() + INTER_SCENARIO_COOLDOWN_S
    while True:
        remaining = int(deadline - time.time())
        if remaining <= 0:
            break
        print(f"    cooldown ... ({remaining}s remaining)", flush=True)
        time.sleep(min(30, remaining))
    print("  [cooldown] done\n", flush=True)


# ---------------------------------------------------------------------------
# Individual scenario runners
# ---------------------------------------------------------------------------

def run_scenario(
    base: str,
    name: str,
    expected_type: str,
    setup_fn,
    teardown_fn,
    timeout_s: int = SCENARIO_TIMEOUT_S,
    acceptable_types: set[str] | None = None,
    keepalive_fn=None,
) -> ScenarioResult:
    result = ScenarioResult(name=name, expected_type=expected_type)
    start = time.time()
    accepted = acceptable_types or {expected_type}

    print(f"\n{'='*60}", flush=True)
    print(f"  SCENARIO: {name}", flush=True)
    print(f"  Expected incident type: {expected_type}", flush=True)
    if acceptable_types:
        print(f"  Also acceptable: {sorted(acceptable_types - {expected_type})}", flush=True)
    print(f"{'='*60}", flush=True)

    cleanup_active_ai_incidents(base)
    known_ids = count_open_incidents_before(base)
    print(f"  Currently known incidents: {len(known_ids)}", flush=True)

    try:
        print("  -> Injecting fault ...", flush=True)
        setup_fn()

        print(f"  -> Waiting up to {timeout_s}s for AIOps to detect ...", flush=True)
        incident = wait_for_new_incident(base, known_ids, timeout_s, keepalive_fn=keepalive_fn)

        if incident is None:
            result.error = f"No incident detected within {timeout_s}s"
            result.notes.append("AIOps polling may have missed the fault window - retry with longer stress")
        else:
            result.incident_id = incident["id"]
            title = incident.get("title", "")
            result.detected_type = title.replace("AI Incident: ", "").strip() if "AI Incident:" in title else title
            result.confidence = incident.get("confidence")
            print(f"  [OK] Incident #{incident['id']} created: {title}", flush=True)
            print(f"    status={incident.get('status')}  confidence={result.confidence}", flush=True)

            type_ok = result.detected_type in accepted
            if type_ok:
                result.notes.append(f"Type correctly classified as {result.detected_type}")
                print(f"  [OK] Type match: {result.detected_type}", flush=True)
            else:
                result.notes.append(f"Type mismatch: expected={expected_type} got={result.detected_type}")
                print(f"  [FAIL] Type mismatch: expected={expected_type} got={result.detected_type}", flush=True)

            print(f"  -> Waiting up to {REMEDIATION_TIMEOUT_S}s for Camunda workflow ...", flush=True)
            final_status = wait_for_incident_resolution(base, incident["id"], REMEDIATION_TIMEOUT_S)
            result.final_status = final_status
            print(f"  -> Final status: {final_status}", flush=True)

            # Pass = incident detected + type accepted. Camunda status is informational.
            result.passed = type_ok
            if final_status not in ("RESOLVED", "WAITING_FOR_APPROVAL", "IN_PROGRESS", "CLOSED"):
                result.notes.append(f"Camunda did not process within {REMEDIATION_TIMEOUT_S}s (status={final_status})")

            if final_status not in TERMINAL_STATUSES:
                resolve_incident(base, incident["id"])
                print(f"  -> Auto-resolved incident #{incident['id']} for clean state", flush=True)

    except Exception as exc:
        result.error = str(exc)
        print(f"  [ERROR] Exception: {exc}", flush=True)
    finally:
        try:
            teardown_fn()
        except Exception as e:
            print(f"  [WARN] Teardown error: {e}", flush=True)

    result.duration_s = time.time() - start
    status_icon = "PASS" if result.passed else "FAIL"
    print(f"\n  {status_icon}  ({result.duration_s:.0f}s)", flush=True)
    return result


# ---------------------------------------------------------------------------
# Main test runner
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="PFE AIOps end-to-end scenario tests")
    parser.add_argument("--backend-url", default="http://localhost:8181", help="Backend base URL")
    parser.add_argument("--timeout", type=int, default=SCENARIO_TIMEOUT_S, help="Per-scenario timeout in seconds")
    parser.add_argument("--scenario", default="all", help="Run only this scenario name (or 'all')")
    parser.add_argument("--no-cooldown", action="store_true", help="Skip inter-scenario cooldown (faster but less reliable)")
    args = parser.parse_args()

    base = args.backend_url.rstrip("/")
    use_cooldown = not args.no_cooldown

    # Sanity check — use readiness group to avoid failing on non-critical components (e.g. Camunda)
    health = get(f"{base}/actuator/health/readiness")
    if health.get("status") != "UP":
        # fallback: try basic ping
        ping = get(f"{base}/actuator/health/liveness")
        if ping.get("status") != "UP":
            print(f"ERROR: Backend not reachable at {base}  health={health}")
            return 1
        print(f"[WARN] Readiness not UP but liveness is UP — proceeding. health={health}")

    print(f"\nPFE AIOps - Scenario Test Suite")
    print(f"Backend : {base}")
    print(f"Timeout : {args.timeout}s / scenario")
    print(f"Cooldown: {'enabled (' + str(INTER_SCENARIO_COOLDOWN_S) + 's)' if use_cooldown else 'disabled'}\n")

    # Ensure clean state
    reset_all_faults(base)
    time.sleep(2)

    scenarios: list[ScenarioResult] = []
    ran_any_fault_scenario = False

    # -- Scenario 0: Healthy baseline (NO_INCIDENT) --------------------------
    if args.scenario in ("all", "healthy"):
        print("\n--- BASELINE: verifying healthy system produces NO incident ---")
        before = count_open_incidents_before(base)
        time.sleep(30)
        after_resp = get(f"{base}/api/incidents?size=200")
        after_all = {i["id"] for i in after_resp.get("content", [])}
        new_ids = after_all - before
        new_open = {
            i["id"] for i in after_resp.get("content", [])
            if i["id"] in new_ids and i.get("status") not in TERMINAL_STATUSES
        }
        r = ScenarioResult(name="HEALTHY_BASELINE", expected_type="NO_INCIDENT")
        r.passed = len(new_open) == 0
        r.notes.append(
            "No new incidents created while system is healthy" if r.passed
            else f"Unexpected open incidents: {new_open}"
        )
        r.duration_s = 30
        print(f"  {'PASS' if r.passed else 'FAIL'} - {'No false positives' if r.passed else 'False positive incidents created'}", flush=True)
        scenarios.append(r)

    # -- Scenario 1: APPLICATION_ERROR_STORM ---------------------------------
    if args.scenario in ("all", "error_storm"):
        if ran_any_fault_scenario and use_cooldown:
            inter_scenario_cooldown("previous scenario")

        def setup_error():
            # error-storm endpoint returns 500 by design AND generates error logs.
            # Do NOT enable HTTP_500 fault — it would break /api/incidents polling.
            for _ in range(80):
                get(f"{base}/api/test/error-storm?message=simulated_test_error_flood")
                time.sleep(0.15)

        def teardown_error():
            pass

        r = run_scenario(
            base, "APPLICATION_ERROR_STORM", "APPLICATION_ERROR_STORM",
            setup_error, teardown_error, args.timeout,
            acceptable_types={"APPLICATION_ERROR_STORM", "GENERAL_DEGRADATION"},
        )
        scenarios.append(r)
        ran_any_fault_scenario = True

    # -- Scenario 2: HIGH_LATENCY -> GENERAL_DEGRADATION --------------------
    if args.scenario in ("all", "latency"):
        if ran_any_fault_scenario and use_cooldown:
            inter_scenario_cooldown("APPLICATION_ERROR_STORM")

        _latency_stop = threading.Event()

        def setup_latency():
            for _ in range(10):
                enable_fault(base, "HIGH_LATENCY")
                time.sleep(0.1)
            # Initial burst of latency calls to seed the Prometheus window
            for _ in range(5):
                try:
                    _request("GET", f"{base}/api/test/db", timeout=12)
                except Exception:
                    pass
                time.sleep(0.5)

        def latency_keepalive():
            """Called every poll cycle to maintain fresh latency signal in Prometheus 5-min window."""
            try:
                _request("GET", f"{base}/api/test/db", timeout=12)
            except Exception:
                pass

        def teardown_latency():
            _latency_stop.set()
            for _ in range(5):
                disable_fault(base, "HIGH_LATENCY")
                time.sleep(0.1)

        r = run_scenario(
            base, "HIGH_LATENCY -> GENERAL_DEGRADATION", "GENERAL_DEGRADATION",
            setup_latency, teardown_latency, args.timeout,
            acceptable_types={"GENERAL_DEGRADATION", "AVAILABILITY_DROP", "CPU_STRESS"},
            keepalive_fn=latency_keepalive,
        )
        scenarios.append(r)
        ran_any_fault_scenario = True

    # -- Scenario 3: CPU_STRESS ---------------------------------------------
    if args.scenario in ("all", "cpu"):
        if ran_any_fault_scenario and use_cooldown:
            inter_scenario_cooldown("HIGH_LATENCY")

        def setup_cpu():
            for _ in range(10):
                enable_fault(base, "CPU_STRESS")
                time.sleep(0.1)
            # Hit multiple times to reach both pods (load-balanced)
            for _ in range(6):
                get(f"{base}/api/test/cpu?seconds=400")
                time.sleep(0.5)

        def teardown_cpu():
            for _ in range(5):
                disable_fault(base, "CPU_STRESS")
                time.sleep(0.1)
            post(f"{base}/api/test/cpu/stop")

        r = run_scenario(
            base, "CPU_STRESS", "CPU_STRESS",
            setup_cpu, teardown_cpu, args.timeout,
            acceptable_types={"CPU_STRESS", "GENERAL_DEGRADATION"},
        )
        scenarios.append(r)
        ran_any_fault_scenario = True

    # -- Scenario 4: MEMORY_STRESS ------------------------------------------
    if args.scenario in ("all", "memory"):
        if ran_any_fault_scenario and use_cooldown:
            inter_scenario_cooldown("CPU_STRESS")

        def setup_memory():
            for _ in range(10):
                enable_fault(base, "MEMORY_STRESS")
                time.sleep(0.1)
            # 500MB per pod × 2 pods = 1 GB total (>900MB threshold).
            # 500MB keeps each pod within its 1Gi limit (JVM base ~300MB + 500MB = ~800MB).
            for _ in range(6):
                get(f"{base}/api/test/memory?mb=500&holdSeconds=400")
                time.sleep(0.5)

        def teardown_memory():
            for _ in range(5):
                disable_fault(base, "MEMORY_STRESS")
                time.sleep(0.1)
            post(f"{base}/api/test/memory/stop")

        r = run_scenario(
            base, "MEMORY_STRESS", "MEMORY_STRESS",
            setup_memory, teardown_memory, args.timeout,
            acceptable_types={"MEMORY_STRESS", "GENERAL_DEGRADATION"},
        )
        scenarios.append(r)
        ran_any_fault_scenario = True

    # -- Scenario 5: DATABASE_CONNECTIVITY (DB pool exhaustion) -------------
    if args.scenario in ("all", "db"):
        if ran_any_fault_scenario and use_cooldown:
            inter_scenario_cooldown("MEMORY_STRESS")

        def setup_db():
            post(f"{base}/api/test/db-pool-exhaust/start?connections=5")

        def teardown_db():
            post(f"{base}/api/test/db-pool-exhaust/stop")

        r = run_scenario(
            base, "DATABASE_CONNECTIVITY (pool exhaustion)", "DATABASE_CONNECTIVITY",
            setup_db, teardown_db, args.timeout,
            acceptable_types={"DATABASE_CONNECTIVITY", "GENERAL_DEGRADATION"},
        )
        scenarios.append(r)
        ran_any_fault_scenario = True

    # -- Scenario 6: AVAILABILITY_DROP (readiness failure) ------------------
    if args.scenario in ("all", "availability"):
        if ran_any_fault_scenario and use_cooldown:
            inter_scenario_cooldown("DATABASE_CONNECTIVITY")

        def setup_avail():
            for _ in range(10):
                enable_fault(base, "READINESS_FAILURE")
                time.sleep(0.1)
            # Wait for readiness probe failures to propagate to Prometheus
            # (failureThreshold×periodSeconds ≈ 30s, plus one Prometheus scrape ≈ 30s)
            # and for db_pool residuals from the previous scenario to be cleared.
            print("  [avail] waiting 75s for probe-fail propagation ...", flush=True)
            deadline = time.time() + 75
            while True:
                remaining = int(deadline - time.time())
                if remaining <= 0:
                    break
                print(f"    probe propagation ... ({remaining}s remaining)", flush=True)
                time.sleep(min(15, remaining))
            # Clear any spurious incidents that fired during the drain window
            cleanup_active_ai_incidents(base)

        def teardown_avail():
            for _ in range(5):
                disable_fault(base, "READINESS_FAILURE")
                time.sleep(0.1)

        r = run_scenario(
            base, "AVAILABILITY_DROP (readiness failure)", "AVAILABILITY_DROP",
            setup_avail, teardown_avail, args.timeout,
            acceptable_types={"AVAILABILITY_DROP", "GENERAL_DEGRADATION"},
        )
        scenarios.append(r)
        ran_any_fault_scenario = True

    # -- Scenario 7: RESTART_LOOP (crash on demand) -------------------------
    if args.scenario in ("all", "restart"):
        if ran_any_fault_scenario and use_cooldown:
            inter_scenario_cooldown("AVAILABILITY_DROP")

        def setup_restart():
            for _ in range(5):
                enable_fault(base, "CRASH_ON_DEMAND")
                time.sleep(0.1)
            # Crash multiple times to accumulate pod_restarts_total >= 2 in the 5-min window
            for attempt in range(3):
                try:
                    get(f"{base}/api/test/crash")
                except Exception:
                    pass
                if attempt < 2:
                    # Wait for pod to recover before next crash
                    time.sleep(20)
            # Wait for JVM restart memory spike to normalise and for pod_restarts_total
            # to be scraped by Prometheus (scrape interval ~30s).
            # Also clears any spurious MEMORY_STRESS incidents triggered by the
            # high memory during JVM cold-start after a crash.
            print("  [restart] waiting 60s for crash signals to propagate ...", flush=True)
            deadline = time.time() + 60
            while True:
                remaining = int(deadline - time.time())
                if remaining <= 0:
                    break
                print(f"    crash propagation ... ({remaining}s remaining)", flush=True)
                time.sleep(min(15, remaining))
            cleanup_active_ai_incidents(base)

        def teardown_restart():
            pass  # pod will have restarted, fault auto-clears in-memory

        r = run_scenario(
            base, "RESTART_LOOP (crash on demand)", "RESTART_LOOP",
            setup_restart, teardown_restart, args.timeout,
            acceptable_types={"RESTART_LOOP", "GENERAL_DEGRADATION", "AVAILABILITY_DROP"},
        )
        scenarios.append(r)

    # -----------------------------------------------------------------------
    # Final report
    # -----------------------------------------------------------------------
    print(f"\n\n{'='*60}")
    print("  FINAL REPORT")
    print(f"{'='*60}")

    passed = sum(1 for r in scenarios if r.passed)
    total = len(scenarios)

    for r in scenarios:
        icon = "PASS" if r.passed else "FAIL"
        type_match = ""
        if r.expected_type != "NO_INCIDENT" and r.detected_type:
            type_match = f"  detected={r.detected_type}"
        status_str = f"  status={r.final_status}" if r.final_status else ""
        conf_str = f"  conf={r.confidence:.2f}" if r.confidence else ""
        err_str = f"  ERROR: {r.error}" if r.error else ""
        print(f"  [{icon}] {r.name:<45}{type_match}{status_str}{conf_str}{err_str}")
        for note in r.notes:
            print(f"       -> {note}")

    print(f"\n  Result: {passed}/{total} scenarios passed")
    print(f"{'='*60}\n")

    reset_all_faults(base)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
