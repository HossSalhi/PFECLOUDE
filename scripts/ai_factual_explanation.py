#!/usr/bin/env python3
# Make every incident's TYPE, ACTION and EXPLANATION reflect the real measured signals
# (deterministic, evidence-based) instead of LLM narrative. Durable for any incident.
import shutil, sys

P = "/opt/pfe-ai-service/app/models/inference.py"
s = open(P).read()
shutil.copy(P, P + ".bak.factual")

start = s.find("        # --- Feature-driven label corrections")
end = s.find("        summary = self._build_summary(")
if start < 0 or end < 0 or end < start:
    print("ERROR: anchors not found"); sys.exit(1)

new_block = '''        # === Deterministic, evidence-based classification + factual explanation =====
        # The incident TYPE, recommended ACTION and EXPLANATION are derived from the real
        # measured signals (priority-ordered), so every incident is labelled and explained
        # accurately and durably -- not from non-deterministic LLM prose.
        if incident:
            mem_trend = self._finite(safe_features.get("memory_trend_bytes_per_sec", 0.0))
            overall_risk = self._finite(safe_features.get("overall_risk_score", 0.0))
            evidence = []
            dominant = None
            rec_action = None
            if node_not_ready >= 1.0:
                dominant, rec_action = "NODE_NOT_READY", "ESCALATE_NODE_OUTAGE_AND_REBALANCE"
                evidence.append(f"{node_not_ready:.0f} node(s) NotReady")
            elif monitoring_vm_up < 0.5:
                dominant, rec_action = "MONITORING_DOWN", "ESCALATE_VM_OUTAGE_AND_FAILOVER"
                evidence.append("monitoring VM unreachable (up=0)")
            elif ai_vm_up < 0.5:
                dominant, rec_action = "AI_SERVICE_DOWN", "ESCALATE_VM_OUTAGE_AND_FAILOVER"
                evidence.append("AI VM unreachable (up=0)")
            elif unavailable_replicas >= 1.0 and pod_restarts < 3.0:
                dominant, rec_action = "DEPLOYMENT_UNAVAILABLE", "ROLLBACK_RECENT_CHANGE_AND_RESTART"
                evidence.append(f"{unavailable_replicas:.0f} replica(s) unavailable")
            elif pod_restarts >= 3.0:
                dominant, rec_action = "POD_CRASH_LOOP", "DELETE_CRASHLOOPING_PODS"
                evidence.append(f"{pod_restarts:.0f} restarts in window")
            elif db_pool_pending >= 1.0 or db_pool_usage_ratio >= 0.9:
                dominant, rec_action = "DATABASE_POOL_SATURATION", "CHECK_DATABASE_CONNECTION_POOL_AND_RESTART"
                evidence.append(f"DB pool pending={db_pool_pending:.0f}, usage={db_pool_usage_ratio:.2f}")
            elif http_5xx_ratio >= 0.10:
                dominant, rec_action = "HIGH_ERROR_RATE", "ROLLBACK_LAST_RELEASE_AND_SHED_TRAFFIC"
                evidence.append(f"HTTP 5xx ratio={http_5xx_ratio*100:.0f}%")
            elif latency_p95 >= 1.0:
                dominant, rec_action = "LATENCY_DEGRADATION", "SCALE_DEPLOYMENT_AND_REBALANCE"
                evidence.append(f"HTTP p95 latency={latency_p95:.2f}s")
            elif cpu_cores >= 0.20:
                dominant, rec_action = "CPU_STRESS", "RESTART_POD_AND_CAPTURE_CPU_PROFILE"
                evidence.append(f"CPU={cpu_cores:.2f} cores")
            elif memory_bytes >= 1.0e8 and mem_trend > 0.0:
                dominant, rec_action = "MEMORY_LEAK", "RESTART_POD_AND_TUNE_MEMORY_LIMITS"
                evidence.append(f"memory={memory_bytes/1e6:.0f}MB rising (+{mem_trend/1e3:.0f}KB/s)")

            if dominant is not None:
                incident_type = dominant
                action = rec_action
                if execution_mode == "AUTO_EXECUTE_CANDIDATE":
                    final_executable_action = self._map_executable_action(action)

            # Demote a flat-memory false positive (a JVM holding its heap is not a leak).
            if (incident_type in ("MEMORY_LEAK", "MEMORY_STRESS")
                    and mem_trend <= 0.0 and pod_restarts < 1.0 and unavailable_replicas < 1.0):
                incident, noise, incident_type = False, True, "NO_INCIDENT"

            if incident:
                ev = "; ".join(evidence) if evidence else f"composite risk score={overall_risk:.2f}"
                decision_explanation = (
                    f"{incident_type} (severity {severity}, confidence {confidence:.2f}). "
                    f"Measured evidence: {ev}. Recommended action: {action}."
                )

'''

s = s[:start] + new_block + s[end:]
open(P, "w").write(s)
print("inference.py: deterministic classification + factual explanation installed")
