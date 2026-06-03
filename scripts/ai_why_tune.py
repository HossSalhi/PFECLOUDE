#!/usr/bin/env python3
# Use the better-behaving model for the "Why" + tighter prompt + post-process to keep
# it to 2 concise sentences (guaranteed, even if the model rambles).
import shutil, sys, re
P = "/opt/pfe-ai-service/app/models/inference.py"
s = open(P).read()
shutil.copy(P, P + ".bak.whytune")

start = s.find("    def _explain_why(self, incident_type")
end = s.find("    def _full_llm_predict(self")
if start < 0 or end < 0 or end < start:
    print("ERROR: _explain_why anchors not found"); sys.exit(1)

new_method = '''    def _explain_why(self, incident_type, severity, confidence, evidence, action):
        """LLM reasoning CONSTRAINED to the given facts. Returns 1-2 concise sentences or None."""
        try:
            import httpx as _httpx, re as _re
            system_msg = (
                "You are a senior SRE. Explain WHY the recommended action resolves this incident, "
                "in at most TWO short sentences (max 40 words). Use ONLY the facts given. "
                "Do NOT speculate about causes that are not in the evidence. No preamble, no lists."
            )
            user_msg = (
                f"Incident: {incident_type} (severity {severity}).\\n"
                f"Measured evidence: {evidence}.\\n"
                f"Recommended action: {action}.\\n"
                "Why is this action the right response?"
            )
            with _httpx.Client(timeout=90.0) as _http:
                resp = _http.post(
                    "http://localhost:11434/api/chat",
                    json={
                        "model": "aiops-sre-q4-backup",
                        "messages": [
                            {"role": "system", "content": system_msg},
                            {"role": "user", "content": user_msg},
                        ],
                        "stream": False,
                        "options": {"temperature": 0.1, "num_predict": 160},
                    },
                )
            if resp.status_code != 200:
                return None
            raw = (resp.json().get("message", {}).get("content", "") or "").strip()
            if not raw:
                return None
            parts = _re.split(r"(?<=[.!?])\\s+", raw)
            concise = " ".join(parts[:2]).strip()
            if len(concise) > 320:
                concise = concise[:317].rstrip() + "..."
            return concise
        except Exception:
            return None

'''
s = s[:start] + new_method + s[end:]
open(P, "w").write(s)
print("_explain_why tuned (backup model, 2-sentence cap)")
