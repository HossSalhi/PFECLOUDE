"""
HELIOS — Classifieur ML 120 types (CANDIDAT, mode SHADOW).

NON autoritaire. Tourne en parallèle du modèle prouvé et journalise sa prédiction
pour comparaison en conditions réelles (Phase 2 -> promotion ou ré-entraînement).

Réplique EXACTEMENT l'assemblage d'entraînement (scripts/train-120/gen_120_realistic.py) :
    X = [ scaler(NUM_11) | tf_log(log_sig sans '_') | tf_evt(event_reason, char n-grams) ]
Bundle attendu : {model, scaler, tf_log, tf_evt, label_encoder, num_features}.

Entièrement gardé : toute panne -> None (ne casse jamais la décision autoritaire).
"""
from __future__ import annotations

import logging
import re

import numpy as np

logger = logging.getLogger(__name__)

# nom attendu par le ML 120 -> clés candidates dans les features du service (+ multiplicateur éventuel)
_NUM_MAP = {
    "cpu_cores":            ["cpu_usage_cores", "cpu_cores"],
    "memory_mb":           [("memory_working_set_bytes", 1e-6), "memory_mb"],
    "pod_restarts":         ["pod_restarts_total", "pod_restart_count", "pod_restarts"],
    "unavailable_replicas": ["unavailable_replicas"],
    "latency_p95_s":        ["http_latency_p95_seconds", "latency_p95_s"],
    "http_5xx_ratio":       ["http_5xx_ratio"],
    "node_not_ready":       ["node_not_ready_count", "node_not_ready"],
    "db_hits":              ["db_keyword_hits", "db_hits"],
    "log_lines":            ["log_line_count", "log_lines"],
    "error_lines":          ["error_line_count", "error_lines"],
    "warning_lines":        ["warning_line_count", "warning_lines"],
}

# event.reason K8s -> type des 120 (VÉRITÉ TERRAIN : un fait K8s, pas une prédiction).
# Ordre = priorité de détection dans le texte de root-cause. Couvre les types "event-driven".
_ER_TYPE = [
    ("OOMKilled", "POD_OOM_KILLED"),
    ("ImagePullBackOff", "POD_IMAGE_PULL_BACKOFF"),
    ("ErrImagePull", "POD_ERR_IMAGE_PULL"),
    ("InvalidImageName", "POD_IMAGE_PULL_BACKOFF"),
    ("CrashLoopBackOff", "POD_CRASH_LOOP"),
    ("CreateContainerConfigError", "POD_CONFIG_ERROR"),
    ("RunContainerError", "POD_CONTAINER_RUNTIME_ERROR"),
    ("ContainerCannotRun", "POD_CONTAINER_RUNTIME_ERROR"),
    ("FailedCreatePodSandBox", "POD_CONTAINER_RUNTIME_ERROR"),
    ("Evicted", "POD_EVICTED"),
    ("FailedScheduling", "POD_PENDING_INSUFFICIENT_RESOURCES"),
    ("FailedMount", "VOLUME_MOUNT_FAILURE"),
    ("NodeNotReady", "NODE_NOT_READY"),
    ("Unhealthy", "POD_FAILED_PROBE"),
    ("BackOff", "POD_CRASH_LOOP"),
]
_EVENT_REASONS = [r for r, _ in _ER_TYPE] + ["DeadlineExceeded", "Failed", "Killing"]


class Scenario120:
    def __init__(self, path: str) -> None:
        import joblib
        b = joblib.load(path)
        self.model = b["model"]
        self.scaler = b["scaler"]
        self.tf_log = b["tf_log"]
        self.tf_evt = b["tf_evt"]
        self.le = b["label_encoder"]
        self.num = list(b["num_features"])
        # Compat sklearn : le bundle a été picklé avec une version plus récente (>=1.6) qui a
        # retiré l'attribut `multi_class` ; sur une version antérieure predict_proba le lit.
        # Le modèle est multinomial (solver lbfgs, 120 classes) -> softmax.
        if not hasattr(self.model, "multi_class"):
            try:
                self.model.multi_class = "multinomial"
            except Exception:
                pass

    @staticmethod
    def event_reason(rc: str | None) -> str:
        rc = rc or ""
        low = rc.lower()
        for r in _EVENT_REASONS:
            if r.lower() in low:
                return r
        return "None"

    @staticmethod
    def reason_type(rc: str | None) -> str | None:
        """Type des 120 déduit DÉTERMINISTIQUEMENT de l'event.reason K8s (vérité terrain)."""
        low = (rc or "").lower()
        for reason, itype in _ER_TYPE:
            if reason.lower() in low:
                return itype
        return None

    @staticmethod
    def log_sig(logs) -> str:
        """Signature de logs approximative (minuscule, chiffres masqués, tokens alpha)."""
        if isinstance(logs, str):
            logs = [logs]
        txt = " ".join(logs or [])[:2000].lower()
        txt = re.sub(r"[0-9]+", " ", txt)
        return " ".join(re.findall(r"[a-z]{3,}", txt)[:40])

    def _num_vec(self, feats: dict) -> np.ndarray:
        row = []
        for name in self.num:
            val = 0.0
            for cand in _NUM_MAP.get(name, [name]):
                if isinstance(cand, tuple):
                    key, mult = cand
                    if key in feats:
                        val = float(feats[key]) * mult
                        break
                elif cand in feats:
                    val = float(feats[cand])
                    break
            row.append(val)
        return np.array([row], dtype=float)

    def predict(self, feats: dict, rc: str | None, logs, k: int = 3) -> dict | None:
        try:
            from scipy.sparse import hstack, csr_matrix
            er = self.event_reason(rc)
            sig = self.log_sig(logs)
            X = hstack([
                csr_matrix(self.scaler.transform(self._num_vec(feats))),
                self.tf_log.transform([sig.replace("_", " ")]),
                self.tf_evt.transform([er]),
            ]).tocsr()
            if hasattr(self.model, "predict_proba"):
                proba = self.model.predict_proba(X)[0]
                order = np.argsort(-proba)[:k]
                labels = self.le.inverse_transform(order)
                top = [{"type": str(labels[i]), "confidence": round(float(proba[order[i]]), 4)}
                       for i in range(len(order))]
            else:
                lbl = self.le.inverse_transform(self.model.predict(X))[0]
                top = [{"type": str(lbl), "confidence": None}]
            return {"event_reason": er, "reason_type": self.reason_type(rc),
                    "type": top[0]["type"], "confidence": top[0]["confidence"],
                    "top": top, "authoritative": False}
        except Exception as exc:
            logger.debug("Scenario120 shadow predict failed: %s", exc)
            return None
