"""
ChromaDB-backed semantic memory for AIOps incident auto-learning.

Stores resolved incident outcomes as vectors and retrieves semantically
similar past experiences to inject into the LLM prompt (in-context learning).

Two storage modes:
  - "persistent"  : local PersistentClient backed by a mounted PVC (default for dev / single-node)
  - "http"        : HttpClient pointing at a dedicated ChromaDB server pod (recommended for production)

Falls back gracefully if ChromaDB is not installed.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# Key metric features used as the embedding vector for similarity search.
_VECTOR_FEATURES = [
    "cpu_usage_cores",
    "memory_working_set_bytes",
    "http_5xx_ratio",
    "http_latency_p95_seconds",
    "pod_restarts_total",
    "unavailable_replicas",
    "db_keyword_hits",
    "db_pool_pending",
    "log_risk_score",
    "metric_risk_score",
    "overall_risk_score",
    "error_ratio",
    "error_line_count",
    "restart_keyword_hits",
]
_VECTOR_DIM = len(_VECTOR_FEATURES)


def _build_vector(features: dict[str, float]) -> list[float]:
    """Normalise metrics into a fixed-length similarity vector."""
    SCALE = {
        "cpu_usage_cores": 2.0,
        "memory_working_set_bytes": 2_000_000_000.0,
        "http_5xx_ratio": 1.0,
        "http_latency_p95_seconds": 10.0,
        "pod_restarts_total": 10.0,
        "unavailable_replicas": 3.0,
        "db_keyword_hits": 15.0,
        "db_pool_pending": 5.0,
        "log_risk_score": 1.0,
        "metric_risk_score": 1.0,
        "overall_risk_score": 1.0,
        "error_ratio": 1.0,
        "error_line_count": 200.0,
        "restart_keyword_hits": 10.0,
    }
    vec = np.array(
        [min(features.get(f, 0.0) / SCALE.get(f, 1.0), 1.0) for f in _VECTOR_FEATURES],
        dtype=np.float32,
    )
    norm = float(np.linalg.norm(vec))
    if norm > 0:
        vec = vec / norm
    return vec.tolist()


@dataclass
class IncidentMemory:
    incident_type: str
    severity: str
    action: str
    executable: str
    success: bool
    minutes_to_resolve: int
    app: str
    namespace: str
    confidence: float
    timestamp: float


class ChromaMemoryStore:
    """
    Persistent vector store for incident outcomes.

    Supports two modes:
      - persistent : chromadb.PersistentClient backed by a local directory (PVC in K8s)
      - http        : chromadb.HttpClient pointing at a standalone ChromaDB pod

    Falls back to no-op if chromadb is not installed or the server is unreachable.
    """

    COLLECTION_NAME = "aiops_incident_memory"
    MAX_RESULTS = 8

    def __init__(
        self,
        persist_dir: str = "/data/chroma",
        mode: str = "persistent",
        http_host: str = "chromadb",
        http_port: int = 8000,
        http_ssl: bool = False,
    ) -> None:
        self._available = False
        self._collection = None
        # Local count cache — avoids a ChromaDB round-trip on every query_similar().
        self._count_cache = 0
        try:
            import chromadb
            from chromadb.config import Settings as ChromaSettings

            if mode == "http":
                client = chromadb.HttpClient(
                    host=http_host,
                    port=http_port,
                    ssl=http_ssl,
                    settings=ChromaSettings(anonymized_telemetry=False),
                )
                logger.info(
                    "ChromaDB HTTP client connecting to %s:%d (ssl=%s)",
                    http_host, http_port, http_ssl,
                )
            else:
                path = Path(persist_dir)
                path.mkdir(parents=True, exist_ok=True)
                client = chromadb.PersistentClient(
                    path=str(path),
                    settings=ChromaSettings(anonymized_telemetry=False),
                )
                logger.info("ChromaDB persistent client at %s", persist_dir)

            self._collection = client.get_or_create_collection(
                name=self.COLLECTION_NAME,
                metadata={"hnsw:space": "cosine"},
            )
            self._available = True
            self._count_cache = self._collection.count()
            logger.info(
                "ChromaDB memory store ready (mode=%s, count=%d)",
                mode, self._count_cache,
            )
        except ImportError:
            logger.warning(
                "chromadb not installed — semantic memory disabled; install with: pip install chromadb"
            )
        except Exception as exc:
            logger.warning("ChromaDB init failed (%s): %s — semantic memory disabled", mode, exc)

    @property
    def available(self) -> bool:
        return self._available

    def store(self, features: dict[str, float], memory: IncidentMemory) -> None:
        if not self._available:
            return
        try:
            # Use microsecond precision to avoid doc_id collisions between incidents
            # that share the same type/action but occur within the same second.
            doc_id = hashlib.sha256(
                f"{memory.incident_type}:{memory.action}:{memory.timestamp:.6f}".encode()
            ).hexdigest()[:16]
            vector = _build_vector(features)
            metadata = {
                "incident_type": memory.incident_type,
                "severity": memory.severity,
                "action": memory.action,
                "executable": memory.executable,
                "success": str(memory.success).lower(),
                "minutes": memory.minutes_to_resolve,
                "app": memory.app,
                "namespace": memory.namespace,
                "confidence": round(memory.confidence, 4),
                "timestamp": memory.timestamp,
            }
            existing = self._collection.get(ids=[doc_id]).get("ids") or []
            self._collection.upsert(
                ids=[doc_id],
                embeddings=[vector],
                metadatas=[metadata],
                documents=[json.dumps(metadata)],
            )
            # Keep the local count in sync — only a brand-new id grows the collection.
            if doc_id not in existing:
                self._count_cache += 1
        except Exception as exc:
            logger.warning("ChromaDB store failed: %s", exc)

    def query_similar(
        self,
        features: dict[str, float],
        n_results: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return up to n_results past incidents similar to the current feature vector."""
        if not self._available:
            return []
        try:
            count = self._count_cache
            if count == 0:
                return []
            k = min(n_results or self.MAX_RESULTS, count)
            vector = _build_vector(features)
            results = self._collection.query(
                query_embeddings=[vector],
                n_results=k,
                include=["metadatas", "distances"],
            )
            memories = []
            for meta, dist in zip(results["metadatas"][0], results["distances"][0]):
                # cosine distance: 0=identical, 2=opposite. Accept < 0.6.
                if dist > 0.6:
                    continue
                memories.append({
                    "incident_type": meta.get("incident_type"),
                    "action": meta.get("action"),
                    "executable": meta.get("executable"),
                    "success": meta.get("success") == "true",
                    "minutes": meta.get("minutes"),
                    "app": meta.get("app"),
                    "severity": meta.get("severity"),
                    "confidence": meta.get("confidence"),
                    "similarity": round(1.0 - dist / 2.0, 3),
                })
            return memories
        except Exception as exc:
            logger.warning(
                "ChromaDB query failed (semantic memory degraded — returning empty context): %s", exc
            )
            return []

    def count(self) -> int:
        if not self._available:
            return 0
        try:
            return self._collection.count()
        except Exception:
            return 0
