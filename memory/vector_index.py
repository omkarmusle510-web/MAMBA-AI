"""Local vector similarity search over SQLite-stored embeddings."""

from __future__ import annotations

import math
from typing import Any

from .types import MemoryStatus, MemoryType


class VectorIndex:
    """Local vector index computing cosine similarity over SQLite-stored embeddings.

    Uses NumPy for accelerated vector operations with pure-Python fallback.
    The index is purely an acceleration mechanism; embeddings are read directly
    from or synchronized with the canonical store.
    """

    def search(
        self,
        query_embedding: list[float],
        store: Any,
        *,
        project: str = "",
        memory_type: MemoryType | str | None = None,
        status: MemoryStatus | str | None = MemoryStatus.ACTIVE,
        limit: int = 10,
        min_similarity: float = 0.0,
    ) -> list[tuple[str, float]]:
        """Search stored embeddings for items most similar to query_embedding.

        Returns list of (memory_id, similarity_score) tuples ordered by descending score.
        """
        if not query_embedding or limit <= 0:
            return []

        status_val = status.value if isinstance(status, MemoryStatus) else status
        type_val = memory_type.value if isinstance(memory_type, MemoryType) else memory_type

        # Fetch candidate vectors from canonical store
        if not hasattr(store, "get_all_embeddings"):
            return []

        records = store.get_all_embeddings(
            project=project,
            memory_type=type_val,
            status=status_val,
        )
        if not records:
            return []

        ids: list[str] = [rec[0] for rec in records]
        vectors: list[list[float]] = [rec[1] for rec in records]

        # Vectorized calculation via NumPy when available
        try:
            import numpy as np

            q = np.array(query_embedding, dtype=np.float32)
            q_norm = float(np.linalg.norm(q))
            if q_norm < 1e-9:
                return []

            m = np.array(vectors, dtype=np.float32)
            # Ensure dimensions match
            if m.ndim != 2 or m.shape[1] != q.shape[0]:
                return []

            m_norms = np.linalg.norm(m, axis=1)
            # Avoid division by zero
            m_norms = np.where(m_norms < 1e-9, 1e-9, m_norms)
            sims = np.dot(m, q) / (m_norms * q_norm)
            scores = [float(s) for s in sims]
        except Exception:
            # Pure Python fallback
            scores = []
            q_norm = math.sqrt(sum(x * x for x in query_embedding))
            if q_norm < 1e-9:
                return []
            for vec in vectors:
                if len(vec) != len(query_embedding):
                    scores.append(0.0)
                    continue
                dot = sum(a * b for a, b in zip(query_embedding, vec))
                v_norm = math.sqrt(sum(b * b for b in vec))
                sim = dot / (q_norm * v_norm) if v_norm > 1e-9 else 0.0
                scores.append(sim)

        # Pair, filter, and sort
        scored_pairs: list[tuple[str, float]] = []
        for mid, score in zip(ids, scores):
            if score >= min_similarity:
                # Clamp score to [-1.0, 1.0]
                clamped = max(-1.0, min(1.0, score))
                scored_pairs.append((mid, clamped))

        # Sort descending by score; tie-break deterministically by id ascending
        scored_pairs.sort(key=lambda item: (-item[1], item[0]))
        return scored_pairs[:limit]

