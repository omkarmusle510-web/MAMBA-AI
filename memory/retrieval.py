"""Hybrid retrieval and relevance ranking combining semantic, keyword, metadata, importance, and recency signals."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any

from .embedding import EmbeddingProvider
from .types import MemoryEntry, MemoryQuery, MemoryResult, MemoryStatus, MemoryType
from .vector_index import VectorIndex

logger = logging.getLogger(__name__)

_STOPWORDS = frozenset(
    {
        "a", "an", "the", "and", "or", "but", "if", "then", "else", "when",
        "at", "by", "for", "with", "about", "against", "between", "into",
        "through", "during", "before", "after", "above", "below", "to", "from",
        "up", "down", "in", "out", "on", "off", "over", "under", "again",
        "further", "then", "once", "here", "there", "all", "any", "both",
        "each", "few", "more", "most", "other", "some", "such", "no", "nor",
        "not", "only", "own", "same", "so", "than", "too", "very", "can",
        "will", "just", "don", "should", "now", "i", "me", "my", "we", "our",
        "you", "your", "he", "him", "his", "she", "her", "it", "its", "they",
        "them", "their", "what", "which", "who", "whom", "this", "that",
        "these", "those", "am", "is", "are", "was", "were", "be", "been",
        "being", "have", "has", "had", "having", "do", "does", "did", "doing",
        "tell", "show", "give", "get", "find", "please", "recall", "remember",
    }
)


def _compute_keyword_score(content: str, query: str) -> float:
    """Compute lexical token overlap score in range [0.0, 1.0]."""
    content_lower = content.casefold()
    query_lower = query.casefold().strip()

    if not query_lower:
        return 0.5  # Neutral if no query string

    # Direct phrase match bonus
    if query_lower in content_lower:
        return 1.0

    query_tokens = [
        t for t in re.findall(r"\w+", query_lower)
        if len(t) > 1 and t not in _STOPWORDS
    ]
    if not query_tokens:
        # Fall back to all tokens if all were stopwords
        query_tokens = [t for t in re.findall(r"\w+", query_lower) if len(t) > 1]
    if not query_tokens:
        return 0.0

    content_tokens = set(re.findall(r"\w+", content_lower))
    matches = sum(1 for t in query_tokens if t in content_tokens)

    # Partial match ratio
    ratio = matches / len(query_tokens)
    return max(0.0, min(1.0, ratio))


def _compute_recency_score(created_at: datetime, now: datetime) -> float:
    """Compute recency decay score in range [0.0, 1.0]."""
    age_seconds = max(0.0, (now - created_at).total_seconds())
    age_days = age_seconds / 86400.0
    # Half-life around 20 days
    return 1.0 / (1.0 + 0.05 * age_days)


def _compute_metadata_score(entry: MemoryEntry, query: MemoryQuery) -> float:
    """Compute structured metadata match bonus."""
    checks = 0
    matches = 0

    if query.project.strip():
        checks += 1
        if entry.project.casefold() == query.project.strip().casefold():
            matches += 1

    if query.task.strip():
        checks += 1
        if entry.task.casefold() == query.task.strip().casefold():
            matches += 1

    if query.memory_type is not None:
        checks += 1
        entry_type = entry.memory_type.value if isinstance(entry.memory_type, MemoryType) else str(entry.memory_type)
        q_type = query.memory_type.value if isinstance(query.memory_type, MemoryType) else str(query.memory_type)
        if entry_type == q_type:
            matches += 1

    if query.metadata:
        for k, v in query.metadata.items():
            checks += 1
            if entry.metadata.get(k) == v:
                matches += 1

    if checks == 0:
        return 0.5  # Neutral when no filters specified
    return matches / checks


class HybridRetriever:
    """Combines semantic similarity, keyword matching, metadata filtering, importance, and recency."""

    def __init__(
        self,
        vector_index: VectorIndex | None = None,
        *,
        semantic_weight: float = 0.40,
        keyword_weight: float = 0.25,
        metadata_weight: float = 0.15,
        importance_weight: float = 0.10,
        recency_weight: float = 0.10,
        min_relevance_threshold: float = 0.15,
    ) -> None:
        self.vector_index = vector_index or VectorIndex()
        self.semantic_weight = semantic_weight
        self.keyword_weight = keyword_weight
        self.metadata_weight = metadata_weight
        self.importance_weight = importance_weight
        self.recency_weight = recency_weight
        self.min_relevance_threshold = min_relevance_threshold

    def retrieve(
        self,
        query: MemoryQuery,
        store: Any,
        embedding_provider: EmbeddingProvider | None = None,
    ) -> MemoryResult:
        """Perform hybrid retrieval and return top ranked entries."""
        now = datetime.now(UTC)

        # 1. Fetch candidate pool using structured store criteria
        # Pass a high candidate limit to allow hybrid re-ranking
        candidate_query = MemoryQuery(
            query="",  # Do not filter out candidates purely on keyword yet
            project=query.project,
            task=query.task,
            memory_type=query.memory_type,
            status=query.status,
            importance_min=query.importance_min,
            limit=max(query.limit * 10, 100),
        )
        base_result = store.retrieve(candidate_query)
        candidates = list(base_result.entries)

        if not candidates:
            return MemoryResult(entries=(), scores=(), metadata={"store": getattr(store, "store_name", "unknown"), "matched": 0})

        # 2. Semantic search hits (if provider is available and semantic retrieval is enabled)
        semantic_scores: dict[str, float] = {}
        use_semantic = (
            query.include_semantic
            and embedding_provider is not None
            and embedding_provider.is_available
            and bool(query.query.strip())
        )

        if use_semantic and embedding_provider is not None:
            try:
                query_emb = embedding_provider.embed_query(query.query)
                if query_emb:
                    hits = self.vector_index.search(
                        query_embedding=query_emb,
                        store=store,
                        project=query.project,
                        memory_type=query.memory_type,
                        status=query.status,
                        limit=max(query.limit * 5, 50),
                    )
                    for mid, sim in hits:
                        # Map cosine similarity to [0.0, 1.0]
                        semantic_scores[mid] = max(0.0, min(1.0, (sim + 1.0) / 2.0 if sim < 0 else sim))
            except Exception as exc:
                logger.warning("Semantic embedding search failed, falling back to keyword: %s", exc)
                use_semantic = False

        # 3. Dynamic weights: redistribute semantic weight if semantic not available
        if use_semantic and semantic_scores:
            w_sem = self.semantic_weight
            w_key = self.keyword_weight
            w_meta = self.metadata_weight
            w_imp = self.importance_weight
            w_rec = self.recency_weight
        else:
            # Rebalance weights when semantic retrieval is inactive
            w_sem = 0.0
            w_key = self.keyword_weight + self.semantic_weight * 0.60
            w_meta = self.metadata_weight + self.semantic_weight * 0.20
            w_imp = self.importance_weight + self.semantic_weight * 0.10
            w_rec = self.recency_weight + self.semantic_weight * 0.10

        # 4. Score each candidate
        scored_entries: list[tuple[MemoryEntry, float]] = []
        for entry in candidates:
            sem_s = semantic_scores.get(entry.id, 0.0)
            key_s = _compute_keyword_score(entry.content, query.query)
            meta_s = _compute_metadata_score(entry, query)
            imp_s = max(0.0, min(1.0, float(entry.importance)))
            rec_s = _compute_recency_score(entry.created_at, now)

            # If user entered a query string, verify it has either keyword overlap or semantic relevance
            if query.query.strip():
                if sem_s < 0.10 and key_s < 0.15:
                    continue  # Filter out items with no topical match

            total_score = (
                w_sem * sem_s
                + w_key * key_s
                + w_meta * meta_s
                + w_imp * imp_s
                + w_rec * rec_s
            )

            if total_score >= self.min_relevance_threshold:
                scored_entries.append((entry, round(total_score, 4)))

        # 5. Deterministic sorting: score desc -> importance desc -> created_at desc -> id asc
        scored_entries.sort(
            key=lambda item: (
                -item[1],
                -item[0].importance,
                -item[0].created_at.timestamp(),
                item[0].id,
            )
        )

        limited = scored_entries[: query.limit]
        return MemoryResult(
            entries=tuple(e for e, _ in limited),
            scores=tuple(s for _, s in limited),
            metadata={
                "store": getattr(store, "store_name", "unknown"),
                "matched": len(scored_entries),
                "returned": len(limited),
                "semantic_enabled": use_semantic,
                "strategy": "hybrid" if use_semantic else "structured_keyword",
            },
        )

