#!/usr/bin/env python3
# Add an LLM-generated "Why:" reasoning to the factual explanation.
# - Facts stay deterministic/exact (type, evidence, action).
# - The LLM explains WHY the action fits, CONSTRAINED to the given facts (no invention).
# - Only runs on the full-analysis path (not the fast skip_llm path), with a
#   deterministic per-type fallback if the LLM is slow/unavailable.
import shutil, sys

P = "/opt/pfe-ai-service/app/models/inference.py"
s = open(P).read()
shutil.copy(P, P + ".bak.why")

if "_explain_why" in s:
    print("already patched"); sys.exit(0)

old = (
    '            if incident:\n'
    '                ev = "; ".join(evidence) if evidence else f"composite risk score={overall_risk:.2f}"\n'
    '                decision_explanation = (\n'
    '                    f"{incident_type} (severity {severity}, confidence {confidence:.2f}). "\n'
    '                    f"Measured evidence: {ev}. Recommended action: {action}."\n'
    '                )\n'
)
new = (
    '            if incident:\n'
    '                ev = "; ".join(evidence) if evidence else f"composite risk score={overall_risk:.2f}"\n'
    '                why = self._why_fallback(incident_type, action)\n'
    '                if not skip_llm:\n'
    '                    _llm_why = self._explain_why(incident_type, severity, confidence, ev, action)\n'
    '                    if _llm_why:\n'
    '                        why = _llm_why\n'
    '                decision_explanation = (\n'
    '                    f"{incident_type} (severity {severity}, confidence {confidence:.2f}). "\n'
    '                    f"Measured evidence: {ev}. Recommended action: {action}. "\n'
    '                    f"Why: {why}"\n'
    '                )\n'
)
if old not in s:
    print("ERROR: factual block not found"); sys.exit(1)
s = s.replace(old, new, 1)

methods = '''    @staticmethod
    def _why_fallback(incident_type: str, action: str) -> str:
        m = {
            "NODE_NOT_READY": "the node is unreachable, so its workloads must be rescheduled elsewhere; escalation triggers node failover/rebalancing.",
            "MONITORING_DOWN": "the monitoring VM is unreachable; escalation initiates failover to restore observability.",
            "AI_SERVICE_DOWN": "the AI VM is unreachable; escalation initiates failover to restore automated remediation.",
            "DEPLOYMENT_UNAVAILABLE": "replicas cannot become ready (typically a bad release), so rolling back to the last good revision restores availability fastest.",
            "POD_CRASH_LOOP": "the container keeps crashing, so deleting the crash-looping pods forces a clean recreation.",
            "DATABASE_POOL_SATURATION": "the database connection pool is exhausted, so restarting frees leaked connections after a pool check.",
            "HIGH_ERROR_RATE": "the error rate spiked (typically after a release), so rolling back and shedding traffic stops user-facing failures.",
            "LATENCY_DEGRADATION": "latency degraded under load, so scaling out adds capacity to absorb the pressure.",
            "CPU_STRESS": "CPU saturation slows every request, so restarting the hot pod clears the runaway work while a CPU profile is captured.",
            "MEMORY_LEAK": "memory grows toward OOM, so restarting reclaims it while memory limits are tuned.",
        }
        return m.get(incident_type, f"{action} directly addresses the detected condition with the lowest blast radius.")

    def _explain_why(self, incident_type, severity, confidence, evidence, action):
        """LLM reasoning CONSTRAINED to the given facts (no invention). None on failure."""
        try:
            import httpx as _httpx
            system_msg = (
                "You are a senior SRE. In ONE short technical sentence, explain WHY the "
                "recommended action is the correct response to the incident, based ONLY on "
                "the facts provided. Do not invent numbers, metrics, or causes. No preamble, no list."
            )
            user_msg = (
                f"Incident type: {incident_type}\\n"
                f"Severity: {severity}, confidence: {confidence:.2f}\\n"
                f"Measured evidence: {evidence}\\n"
                f"Recommended action: {action}\\n"
                "Why is this action the right response?"
            )
            with _httpx.Client(timeout=60.0) as _http:
                resp = _http.post(
                    "http://localhost:11434/api/chat",
                    json={
                        "model": "aiops-sre-q4",
                        "messages": [
                            {"role": "system", "content": system_msg},
                            {"role": "user", "content": user_msg},
                        ],
                        "stream": False,
                        "options": {"temperature": 0.1, "num_predict": 90},
                    },
                )
            if resp.status_code != 200:
                return None
            raw = (resp.json().get("message", {}).get("content", "") or "").strip()
            return raw if raw else None
        except Exception:
            return None

    def _full_llm_predict(self, features: dict[str, float]) -> dict | None:'''

anchor = "    def _full_llm_predict(self, features: dict[str, float]) -> dict | None:"
if anchor not in s:
    print("ERROR: _full_llm_predict anchor not found"); sys.exit(1)
s = s.replace(anchor, methods, 1)

open(P, "w").write(s)
print("LLM 'why' reasoning installed (grounded, gated to full-analysis path, with fallback)")
