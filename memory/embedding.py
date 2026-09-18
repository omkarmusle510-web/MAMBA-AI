"""Embedding provider abstraction and implementations for semantic memory."""

from __future__ import annotations

import hashlib
import logging
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)

_DEFAULT_MODEL_NAME = "all-MiniLM-L6-v2"
_DEFAULT_DIMENSION = 384


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Abstract contract for text embedding generation."""

    @property
    def is_available(self) -> bool:
        """Return True if this provider is ready to generate embeddings."""
        ...

    @property
    def dimension(self) -> int:
        """Return dimensionality of embedding vectors."""
        ...

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Compute embedding vectors for a batch of texts."""
        ...

    def embed_query(self, text: str) -> list[float]:
        """Compute embedding vector for a single query text."""
        ...


class NullEmbeddingProvider:
    """Null/no-op provider used when embeddings are disabled or unavailable."""

    @property
    def is_available(self) -> bool:
        return False

    @property
    def dimension(self) -> int:
        return 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        return []

    def embed_query(self, text: str) -> list[float]:
        return []


class SentenceTransformerEmbeddingProvider:
    """Local SentenceTransformer embedding provider.

    Loads model once, caches instance, and gracefully reports unavailable
    if sentence-transformers or the model weights cannot be loaded.
    """

    def __init__(self, model_name: str = _DEFAULT_MODEL_NAME) -> None:
        self.model_name = model_name
        self._model = None
        self._dimension = _DEFAULT_DIMENSION
        self._load_attempted = False
        self._available = False
        self._error: str | None = None

    def _ensure_loaded(self) -> bool:
        if self._load_attempted:
            return self._available

        self._load_attempted = True
        try:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.model_name)
            # Infer dimension
            test_emb = self._model.encode(["test"])
            self._dimension = len(test_emb[0])
            self._available = True
            self._error = None
            logger.info("Loaded SentenceTransformer model '%s' (dim: %d)", self.model_name, self._dimension)
        except Exception as exc:
            self._available = False
            self._error = str(exc)
            logger.warning(
                "SentenceTransformer model '%s' unavailable, semantic retrieval will fall back to keyword: %s",
                self.model_name,
                exc,
            )

        return self._available

    @property
    def is_available(self) -> bool:
        return self._ensure_loaded()

    @property
    def dimension(self) -> int:
        self._ensure_loaded()
        return self._dimension

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if not self._ensure_loaded() or self._model is None:
            return []
        try:
            embeddings = self._model.encode(texts, convert_to_numpy=True)
            return [vec.tolist() for vec in embeddings]
        except Exception as exc:
            logger.warning("Error generating embeddings with SentenceTransformer: %s", exc)
            return []

    def embed_query(self, text: str) -> list[float]:
        if not text.strip():
            return []
        batch = self.embed([text])
        return batch[0] if batch else []


class DeterministicEmbeddingProvider:
    """Deterministic hash-based embedding provider for testing and offline development.

    Produces normalized vectors based on term n-grams and character hashes,
    allowing semantic overlap testing without downloading deep neural network weights.
    """

    def __init__(self, dimension: int = _DEFAULT_DIMENSION) -> None:
        self._dimension = dimension

    @property
    def is_available(self) -> bool:
        return True

    @property
    def dimension(self) -> int:
        return self._dimension

    def _hash_vector(self, text: str) -> list[float]:
        import numpy as np

        if not text.strip():
            return [0.0] * self._dimension

        vec = np.zeros(self._dimension, dtype=np.float32)
        words = text.casefold().split()

        for word in words:
            # Deterministic bucket via sha256
            digest = hashlib.sha256(word.encode("utf-8")).digest()
            idx = int.from_bytes(digest[:4], "big") % self._dimension
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vec[idx] += sign

            # Bigrams
            if len(word) >= 3:
                for i in range(len(word) - 2):
                    sub = word[i : i + 3]
                    s_digest = hashlib.sha256(sub.encode("utf-8")).digest()
                    s_idx = int.from_bytes(s_digest[:4], "big") % self._dimension
                    vec[s_idx] += 0.5

        norm = float(np.linalg.norm(vec))
        if norm > 1e-9:
            vec = vec / norm
        return vec.tolist()

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._hash_vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._hash_vector(text)

