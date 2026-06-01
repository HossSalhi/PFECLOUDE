from __future__ import annotations

import logging
import re

import numpy as np


LOGGER = logging.getLogger(__name__)
TOKEN_PATTERN = re.compile(r"[a-zA-Z0-9_./:-]+")


class LogEmbeddingEncoder:
    def __init__(
        self,
        model_name: str = "all-MiniLM-L6-v2",
        fallback_dim: int = 384,
        max_lines: int = 200,
    ) -> None:
        self.model_name = model_name
        self.fallback_dim = max(16, int(fallback_dim))
        self.max_lines = max(1, int(max_lines))
        self.embedding_dim = self.fallback_dim
        self._model = None
        self._model_attempted = False

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def encode(self, lines: list[str]) -> np.ndarray:
        selected = [line for line in lines if line][-self.max_lines :]
        if not selected:
            return np.zeros(self.embedding_dim, dtype=float)

        model = self._ensure_model()
        if model is not None:
            try:
                embeddings = model.encode(
                    selected,
                    convert_to_numpy=True,
                    normalize_embeddings=True,
                    show_progress_bar=False,
                )
                if embeddings.ndim == 1:
                    return embeddings.astype(float)
                return embeddings.mean(axis=0).astype(float)
            except Exception as exc:  # pragma: no cover - fallback path
                LOGGER.warning(
                    "MiniLM encoding failed; using hashed fallback embedding. error=%s",
                    exc,
                )

        return self._fallback_embed(selected)

    def as_feature_map(
        self,
        vector: np.ndarray,
        prefix: str = "log_embedding",
    ) -> dict[str, float]:
        return {
            f"{prefix}_{index:03d}": float(value)
            for index, value in enumerate(vector.tolist())
        }

    def _ensure_model(self):
        if self._model_attempted:
            return self._model
        self._model_attempted = True

        try:
            from sentence_transformers import SentenceTransformer
        except Exception as exc:  # pragma: no cover - optional dependency path
            LOGGER.info(
                "sentence-transformers unavailable, continuing with hashed embeddings. error=%s",
                exc,
            )
            return None

        try:
            model = SentenceTransformer(self.model_name)
            self._model = model
            self.embedding_dim = int(
                model.get_sentence_embedding_dimension() or self.fallback_dim
            )
            return model
        except Exception as exc:  # pragma: no cover - network/cache path
            LOGGER.warning(
                "Unable to load sentence-transformer '%s'; using hashed fallback. error=%s",
                self.model_name,
                exc,
            )
            self._model = None
            self.embedding_dim = self.fallback_dim
            return None

    def _fallback_embed(self, lines: list[str]) -> np.ndarray:
        vector = np.zeros(self.embedding_dim, dtype=float)
        for line in lines:
            for token in TOKEN_PATTERN.findall(line.lower()):
                vector[hash(token) % self.embedding_dim] += 1.0

        norm = float(np.linalg.norm(vector))
        if norm > 0:
            vector /= norm
        return vector
