"""
HELIOS — Wrapper LLM avec RAG + CRAG (Corrective RAG).

Encapsule TOUT le pipeline de décision du LLM en un objet propre :
    incident -> RAG (cartes catalogue) -> CRAG (évalue + corrige) -> prompt ancré
             -> Ollama (helios-120-3b, repli qwen2.5:3b) -> parse (DIAGNOSTIC|ACTION|EXPLICATION)

CRAG (Corrective RAG — Yan et al. 2024, adapté AIR-GAP) : un évaluateur note la
pertinence des cartes récupérées (via le score hybride du RAG) et agit :
  - CORRECT   (score >= high, ou vérité terrain dispo) -> RAFFINE (garde le pertinent, filtre le bruit)
  - AMBIGU    (low < score < high)                     -> COMBINE (cartes + vérité terrain event.reason)
  - INCORRECT (score <= low)                           -> CORRIGE (ignore le RAG trompeur ; s'ancre sur
                                                          la vérité terrain event.reason, sinon symptômes seuls)
Pas de recherche web (air-gap) : la correction s'appuie sur la VÉRITÉ TERRAIN `event.reason`
que HELIOS possède déjà — un signal correctif plus fiable qu'une recherche web pour ce domaine K8s.

Entièrement gardé : toute panne -> None (l'appelant retombe sur l'explication déterministe).
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)


class HeliosLLM:
    def __init__(self, rag, *, model="helios-120-3b", fallback="qwen2.5:3b-instruct",
                 timeout=25.0, num_predict=128, top_k=3,
                 crag_high=0.60, crag_low=0.30, ollama_url="http://localhost:11434"):
        self.rag = rag
        self.model = model
        self.fallback = fallback
        self.timeout = float(timeout)
        self.num_predict = int(num_predict)
        self.top_k = int(top_k)
        self.crag_high = crag_high   # seuil au-dessus duquel la récupération est jugée FIABLE
        self.crag_low = crag_low     # seuil en dessous duquel elle est jugée TROMPEUSE
        self.url = ollama_url

    # ── vérité terrain event.reason (signal correctif) ───────────────────────
    def _reason_type(self, rc):
        try:
            from app.services.scenario_120 import Scenario120
            return Scenario120.reason_type(rc)
        except Exception:
            return None

    # ── CRAG : évaluation de la récupération + correction ────────────────────
    def _crag(self, incident_type, evidence, rc):
        """Retourne (context_text, grade, meta). grade ∈ {correct, ambiguous, incorrect, none}."""
        if self.rag is None:
            return "", "none", {}
        try:
            pinned = self.rag.card_for(incident_type)
            neighbors = self.rag.retrieve(f"{incident_type} {evidence} {rc or ''}", k=self.top_k)
            er_type = self._reason_type(rc)
            er_card = self.rag.card_for(er_type) if er_type else None
            top_score = max([c.get("score", 0.0) for c in neighbors], default=0.0)

            # ── GRADE de la récupération ──
            if er_card is not None:
                grade = "correct"          # vérité terrain K8s présente -> ancrage fiable
            elif top_score >= self.crag_high:
                grade = "correct"
            elif top_score <= self.crag_low:
                grade = "incorrect"
            else:
                grade = "ambiguous"

            # ── CORRECTION selon le grade ──
            cards, seen = [], set()
            def add(c):
                if c and c["type"] not in seen:
                    seen.add(c["type"]); cards.append(c)

            if grade == "incorrect":
                add(er_card); add(pinned)                       # ignore les voisins trompeurs
                header = "HELIOS knowledge base (low-confidence retrieval — grounded on the classified type only):"
            elif grade == "ambiguous":
                add(er_card); add(pinned)
                for c in neighbors[:1]:
                    add(c)
                header = "HELIOS knowledge base (medium confidence — ground truth + closest neighbor):"
            else:  # correct -> raffine : filtre le bruit
                add(er_card); add(pinned)
                for c in neighbors:
                    if c.get("score", 0.0) >= self.crag_low:
                        add(c)
                header = "HELIOS knowledge base (known incidents, most relevant first):"

            cards = cards[: self.top_k + 1]
            if not cards:
                return "", grade, {"top_score": round(top_score, 3), "er_type": er_type}
            lines = [header]
            for c in cards:
                mode = "AUTO" if c["auto"] else "ESCALATE"
                lines.append(f"- {c['type']} [{c['category']}] -> action={c['action']} "
                             f"({mode}); fix: {c['resolution']}")
            return "\n".join(lines), grade, {
                "top_score": round(top_score, 3), "er_type": er_type,
                "cards": [c["type"] for c in cards]}
        except Exception as exc:
            logger.debug("CRAG failed: %s", exc)
            return "", "none", {}

    # ── appel Ollama (avec repli modèle) ─────────────────────────────────────
    def _chat(self, system, user, model=None):
        import httpx
        m = model or self.model
        try:
            with httpx.Client(timeout=self.timeout) as h:
                r = h.post(self.url + "/api/chat", json={
                    "model": m,
                    "messages": [{"role": "system", "content": system},
                                 {"role": "user", "content": user}],
                    "stream": False, "keep_alive": -1,
                    "options": {"temperature": 0.2, "num_predict": self.num_predict}})
            if r.status_code != 200:
                return None
            return (r.json().get("message", {}).get("content", "") or "").strip()
        except Exception:
            return None

    # ── appel Ollama en SORTIE STRUCTURÉE (décodage contraint) ───────────────
    # Ollama contraint la génération à un schéma JSON -> l'action est FORCÉMENT un
    # token catalogue valide (enum), la sortie toujours parsable. Corrige le maillon
    # faible mesuré (l'action nommée/le format). Repli prose si indisponible.
    def _chat_json(self, system, user, schema, model=None):
        import httpx, json as _json
        m = model or self.model
        try:
            with httpx.Client(timeout=self.timeout) as h:
                r = h.post(self.url + "/api/chat", json={
                    "model": m,
                    "messages": [{"role": "system", "content": system},
                                 {"role": "user", "content": user}],
                    "stream": False, "keep_alive": -1, "format": schema,
                    "options": {"temperature": 0.1, "num_predict": self.num_predict}})
            if r.status_code != 200:
                return None
            content = (r.json().get("message", {}).get("content", "") or "").strip()
            return _json.loads(content) if content else None
        except Exception:
            return None

    # ── pipeline complet ─────────────────────────────────────────────────────
    def decide(self, incident_type, severity, evidence, action, rc, candidates=None, history=None):
        try:
            ctx, grade, meta = self._crag(incident_type, evidence, rc)
            system = (
                "You are the automatic decision engine of a Kubernetes self-healing system (HELIOS). "
                "Reason ONLY from the provided evidence and knowledge base. Do NOT invent namespaces, "
                "cluster names, application names, percentages or numbers that are not in the evidence. "
                "Give the DIAGNOSTIC (root cause), the ACTION (prefer an ACTION_NAME from the knowledge "
                "base when it matches), and EXPLICATION of how that action fixes it. Be concise (2-3 sentences).")
            if ctx:
                system += "\n\n" + ctx
            if grade == "incorrect":
                system += ("\n\nNote: the retrieved knowledge is low-confidence; rely on the symptoms "
                           "and the classified type, do not force a listed action if it does not fit.")
            if candidates:
                ct = ", ".join(dict.fromkeys([c for c in candidates if c]))
                if ct:
                    system += f"\n\nML classifier candidate types (most likely first): {ct}."
            if history:                       # MÉMOIRE DE CAS : historique de remédiation de cette app
                system += "\n\n" + history
            user = (f"Incident: {incident_type} (severity {severity}). "
                    f"Measured evidence: {evidence}. Proposed action: {action}. Decide the remediation.")

            # ── DÉCODAGE CONTRAINT (prioritaire) : l'action DOIT être une action
            #    catalogue valide récupérée par le RAG -> 100% parsable, jamais de « investigate » flou.
            enum_actions = list(dict.fromkeys(re.findall(r"action=([A-Z][A-Z0-9_]+)", ctx or "")))
            if enum_actions:
                enum_actions = enum_actions + ["CAPTURE_DIAGNOSTICS_AND_INVESTIGATE"]
                schema = {"type": "object", "properties": {
                    "diagnostic": {"type": "string"},
                    "action": {"type": "string", "enum": enum_actions},
                    "explication": {"type": "string"}},
                    "required": ["diagnostic", "action", "explication"]}
                sys_json = system + ("\n\nRespond ONLY as JSON with keys diagnostic, action, explication. "
                                     "'action' MUST be the catalog ACTION_NAME (from the allowed list) that "
                                     "best fixes the incident; 'explication' says how it fixes it.")
                obj = self._chat_json(sys_json, user, schema)
                if not obj and self.fallback:
                    obj = self._chat_json(sys_json, user, schema, model=self.fallback)
                if obj and obj.get("action") in enum_actions:
                    _diag = (obj.get("diagnostic") or "").strip() or None
                    _expl = (obj.get("explication") or "").strip() or _diag
                    if _expl:
                        return {"model": self.model, "diagnostic": _diag,
                                "action": obj["action"], "explication": _expl,
                                "rag_grounded": True, "crag_grade": grade, "crag": meta,
                                "constrained": True}

            raw = self._chat(system, user)
            if not raw and self.fallback:
                raw = self._chat(system, user, model=self.fallback)   # repli modèle de base
            if not raw:
                return None

            diag = act = expl = None
            m = re.search(r"ACTION\s*=\s*([A-Z][A-Z0-9_]{2,60})", raw)
            if m:
                act = m.group(1)
            m = re.search(r"DIAGNOSTIC\s*:\s*(.+?)(?:\||\n|$)", raw, re.IGNORECASE)
            if m:
                diag = m.group(1).strip()
            m = re.search(r"EXPLICATION\s*:\s*(.+)$", raw, re.IGNORECASE | re.DOTALL)
            if m:
                expl = m.group(1).strip().split("\n")[0].strip()
            if not expl:  # le modèle répond en prose -> la prose EST l'explication
                prose = re.sub(r"(?i)\b(DIAGNOSTIC|ACTION|EXPLICATION)\s*[:=]\s*", "", raw).strip()
                sents = re.findall(r"[^.!?]*[.!?]", prose) or [prose]
                expl = " ".join(s.strip() for s in sents[:3]).strip()[:500]
                if not diag and sents:
                    diag = sents[0].strip()[:200]
            if not act:  # scanne l'action dans la réponse (proposée + celles des cartes)
                cand_acts = [action] + re.findall(r"action=([A-Z][A-Z0-9_]+)", ctx or "")
                for c in cand_acts:
                    if c and c in raw:
                        act = c
                        break
            if not expl:
                return None
            return {"model": self.model, "diagnostic": diag, "action": act, "explication": expl,
                    "rag_grounded": bool(ctx), "crag_grade": grade, "crag": meta}
        except Exception as exc:
            logger.debug("HeliosLLM.decide failed: %s", exc)
            return None
