#!/usr/bin/env python3
# Few-shot the LLM "Why" so a small (3B) model actually follows the style:
# ONE grounded sentence, no restating the type/severity/numbers, no lists.
# Small instruct models mimic examples far better than they obey rules, so we
# prepend two ideal Q->A turns and keep a tight token budget to prevent the
# model from drifting into a numbered list. Robust post-processing strips any
# leftover restatement / list-intro fragment. Deterministic fallback unchanged.
import shutil, sys
P = "/opt/pfe-ai-service/app/models/inference.py"
s = open(P).read()
shutil.copy(P, P + ".bak.fewshot")

start = s.find("    def _explain_why(self, incident_type")
end = s.find("    def _full_llm_predict(self")
if start < 0 or end < 0 or end < start:
    print("ERROR: _explain_why anchors not found"); sys.exit(1)

new_method = '''    def _explain_why(self, incident_type, severity, confidence, evidence, action):
        """LLM reasoning, few-shot constrained to the facts. One grounded sentence or None."""
        try:
            import httpx as _httpx, re as _re
            system_msg = (
                "You are a senior SRE. Given an incident and its recommended action, reply with "
                "ONE short sentence explaining WHY that action fixes it. Use only the facts given. "
                "Never restate the type, severity or numbers. Never write a list or a preamble."
            )
            # Few-shot: small models copy the STYLE of these examples far better than rules.
            ex1_u = ("Incident: POD_CRASH_LOOP (severity HIGH). Measured evidence: 7 restarts in window. "
                     "Recommended action: DELETE_CRASHLOOPING_PODS. Why is this action the right response?")
            ex1_a = ("Deleting the crash-looping pods forces Kubernetes to recreate them from a clean "
                     "state, clearing the stuck condition that the repeated restarts could not resolve.")
            ex2_u = ("Incident: DATABASE_POOL_SATURATION (severity HIGH). Measured evidence: pool pending=5, "
                     "usage=0.97. Recommended action: CHECK_DATABASE_CONNECTION_POOL_AND_RESTART. "
                     "Why is this action the right response?")
            ex2_a = ("Restarting releases the leaked connections saturating the pool so new requests stop "
                     "queueing, while the pool check confirms the leak is gone before traffic resumes.")
            user_msg = (
                f"Incident: {incident_type} (severity {severity}). "
                f"Measured evidence: {evidence}. "
                f"Recommended action: {action}. Why is this action the right response?"
            )
            with _httpx.Client(timeout=180.0) as _http:
                resp = _http.post(
                    "http://localhost:11434/api/chat",
                    json={
                        "model": "aiops-sre-q4-backup",
                        "messages": [
                            {"role": "system", "content": system_msg},
                            {"role": "user", "content": ex1_u},
                            {"role": "assistant", "content": ex1_a},
                            {"role": "user", "content": ex2_u},
                            {"role": "assistant", "content": ex2_a},
                            {"role": "user", "content": user_msg},
                        ],
                        "stream": False,
                        "options": {"temperature": 0.1, "num_predict": 90},
                    },
                )
            if resp.status_code != 200:
                return None
            raw = (resp.json().get("message", {}).get("content", "") or "").strip()
            if not raw:
                return None
            # Keep only the first answer line; small models ramble (markdown/meta) after it.
            low = raw.lower()
            cut = len(raw)
            for mk in ["\\n\\n", "\\n#", "\\n**", "\\nlet's", "\\nlet me", "\\nhere", "\\nbelow",
                       "\\nsure", "for the following", "as follows", " reasons:", "\\n1.", "\\n- ", "\\n* "]:
                j = low.find(mk)
                if j != -1:
                    cut = min(cut, j)
            raw = raw[:cut].strip()
            sents = _re.findall(r"[^.!?]*[.!?]", raw) or ([raw] if raw else [])
            clean = []
            for sx in sents:
                t = sx.strip()
                if not t:
                    continue
                lt = t.lower()
                if lt.startswith(("the incident", "this incident", "the recommended action",
                                  "let's", "let me", "here's", "here is", "sure", "certainly")):
                    continue
                if t.endswith(":") or "#" in t or "**" in t:
                    continue
                clean.append(t)
                if len(clean) >= 2:
                    break
            concise = " ".join(clean).strip()
            return concise if concise else None
        except Exception:
            return None

'''
s = s[:start] + new_method + s[end:]
open(P, "w").write(s)
print("_explain_why: few-shot installed (num_predict=90, restatement/list stripping)")
