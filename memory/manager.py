"""Central MemoryManager service coordinating memory lifecycle, classification, embedding, and hybrid retrieval."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any

from .embedding import EmbeddingProvider
from .protocols import ExtendedMemoryStore, MemoryStore
from .retrieval import HybridRetriever
from .types import MemoryEntry, MemoryQuery, MemoryResult, MemoryStatus, MemoryType

logger = logging.getLogger(__name__)

# Secret patterns that must never be committed to memory
_SECRET_PATTERNS = [
    re.compile(r"(?i)(?:api[_-]?key|apikey)\s*[:=]\s*['\"]?[a-zA-Z0-9_\-\.]{12,}"),
    re.compile(r"(?i)bearer\s+[a-zA-Z0-9_\-\.]{20,}"),
    re.compile(r"ghp_[a-zA-Z0-9]{20,}"),
    re.compile(r"sk-[a-zA-Z0-9]{20,}"),
    re.compile(r"(?i)(?:password|passwd|secret)\s*[:=]\s*['\"][^'\"]{4,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
]

# Ephemeral noise patterns
_NOISE_PATTERNS = [
    re.compile(r"(?i)^(?:ok|okay|yes|no|done|thanks|thank you|hello|hi|hey)\.?$"),
    re.compile(r"(?i)^listening(?:\.\.\.)?$"),
    re.compile(r"(?i)^processing(?:\.\.\.)?$"),
]


def _contains_secrets(text: str) -> bool:
    return any(p.search(text) for p in _SECRET_PATTERNS)


def _is_ephemeral_noise(text: str) -> bool:
    stripped = text.strip()
    return any(p.match(stripped) for p in _NOISE_PATTERNS)


class MemoryManager:
    """Coordinates full lifecycle of Mamba long-term memory:

    capture -> classify -> store -> embed -> retrieve -> rank -> update/supersede -> summarize -> forget
    """

    def __init__(
        self,
        store: Any,
        embedding_provider: EmbeddingProvider | None = None,
        retriever: HybridRetriever | None = None,
        model_router: Any = None,
    ) -> None:
        self.store = store
        self.embedding_provider = embedding_provider
        self.retriever = retriever or HybridRetriever()
        self.model_router = model_router

    # ── 1. Classification & Intake ──

    def classify_content(self, content: str) -> MemoryType:
        """Infer semantic category from memory content."""
        lower = content.casefold().strip()

        # User preferences
        if any(term in lower for term in ("i prefer", "my preference", "always use", "never use", "i like", "i dislike", "prefer to")):
            return MemoryType.USER_PREFERENCE

        # User facts
        if any(term in lower for term in ("my name is", "i live in", "i work at", "my email is", "my role is", "i am a")):
            return MemoryType.USER_FACT

        # Project decisions
        if any(term in lower for term in ("we decided", "decision:", "architectural decision", "design decision", "chose to")):
            return MemoryType.PROJECT_DECISION

        # Conversation summaries
        if any(term in lower for term in ("conversation summary", "summary of discussion", "session summary")):
            return MemoryType.CONVERSATION_SUMMARY

        # Task contexts
        if "goal:" in lower and "outcome:" in lower:
            return MemoryType.TASK_CONTEXT

        return MemoryType.KNOWLEDGE

    def should_capture(self, content: str, *, force: bool = False) -> bool:
        """Evaluate whether content is suitable for long-term memory storage.

        Rejects secrets, credentials, trivial chatter, and screen interpretations.
        """
        if not content or not content.strip():
            return False

        stripped = content.strip()

        # Never persist secrets regardless of force flag
        if _contains_secrets(stripped):
            logger.warning("Rejected memory storage: content contains sensitive credentials/secrets.")
            return False

        if force:
            return True

        # Reject trivial conversational noise
        if _is_ephemeral_noise(stripped):
            return False

        # Reject pure code dumps or raw tracebacks (> 2000 chars) unless explicit
        if len(stripped) > 2000 and "Traceback (most recent call last)" in stripped:
            return False

        return True

    # ── 2. Storage & Supersession ──

    def remember(
        self,
        content: str,
        *,
        memory_type: MemoryType | str | None = None,
        project: str = "",
        task: str = "",
        source: str = "",
        importance: float | None = None,
        metadata: dict[str, Any] | None = None,
        check_supersede: bool = True,
        force: bool = False,
    ) -> MemoryEntry | None:
        """Capture and store a new memory item with classification, supersession, and embedding."""
        if not self.should_capture(content, force=force):
            return None

        # Auto-classify type if not explicitly supplied
        resolved_type = memory_type or self.classify_content(content)
        if isinstance(resolved_type, str):
            try:
                resolved_type = MemoryType(resolved_type)
            except ValueError:
                resolved_type = MemoryType.KNOWLEDGE

        # Default importance based on category
        if importance is None:
            if resolved_type in (MemoryType.USER_PREFERENCE, MemoryType.PROJECT_DECISION):
                resolved_importance = 0.8
            elif resolved_type in (MemoryType.USER_FACT, MemoryType.PROJECT_CONTEXT):
                resolved_importance = 0.7
            elif resolved_type == MemoryType.CONVERSATION_SUMMARY:
                resolved_importance = 0.6
            else:
                resolved_importance = 0.5
        else:
            resolved_importance = max(0.0, min(1.0, float(importance)))

        entry_meta = dict(metadata or {})
        entry = MemoryEntry(
            content=content.strip(),
            memory_type=resolved_type,
            project=project.strip(),
            task=task.strip(),
            source=source.strip(),
            importance=resolved_importance,
            status=MemoryStatus.ACTIVE,
            metadata=entry_meta,
        )

        # Detect and handle supersession
        if check_supersede:
            self._handle_supersession(entry)

        # Generate embedding if provider is available
        embedding: list[float] | None = None
        if self.embedding_provider and self.embedding_provider.is_available:
            try:
                emb = self.embedding_provider.embed_query(entry.content)
                if emb:
                    embedding = emb
            except Exception as exc:
                logger.warning("Failed to generate embedding during remember: %s", exc)

        # Persist to canonical store
        stored = self.store.store(entry, embedding=embedding) if hasattr(self.store, "store") else entry
        return stored

    def _handle_supersession(self, new_entry: MemoryEntry) -> None:
        """Find active prior memories that the new entry invalidates and mark them superseded."""
        # Supersession applies primarily to user preferences, facts, and project decisions
        if new_entry.memory_type not in (
            MemoryType.USER_PREFERENCE,
            MemoryType.USER_FACT,
            MemoryType.PROJECT_DECISION,
        ):
            return

        if not hasattr(self.store, "find_related") or not hasattr(self.store, "update"):
            return

        try:
            candidates = self.store.find_related(new_entry.content, project=new_entry.project, limit=5)
            for old in candidates:
                if old.id == new_entry.id or old.status != MemoryStatus.ACTIVE:
                    continue

                # Same type check
                if old.memory_type != new_entry.memory_type:
                    continue

                # Topic overlap check: e.g. "favorite database is X" vs "favorite database is Y"
                old_words = set(re.findall(r"\w+", old.content.casefold()))
                new_words = set(re.findall(r"\w+", new_entry.content.casefold()))
                overlap = old_words & new_words - _STOPWORDS

                # High topic overlap indicating conflict or replacement
                if len(overlap) >= 2:
                    logger.info("Memory '%s' superseded by '%s'", old.id, new_entry.id)
                    self.store.update(
                        old.id,
                        status=MemoryStatus.SUPERSEDED,
                        superseded_by=new_entry.id,
                    )
        except Exception as exc:
            logger.warning("Error evaluating supersession: %s", exc)

    # ── 3. Retrieval & Ranking ──

    def retrieve(
        self,
        query: str = "",
        *,
        project: str = "",
        task: str = "",
        memory_type: MemoryType | str | None = None,
        importance_min: float = 0.0,
        status: MemoryStatus | str | None = MemoryStatus.ACTIVE,
        limit: int = 5,
        include_semantic: bool = True,
    ) -> MemoryResult:
        """Retrieve most relevant memories using hybrid multi-signal ranking."""
        q = MemoryQuery(
            query=query,
            project=project,
            task=task,
            memory_type=memory_type,
            importance_min=importance_min,
            status=status,
            limit=limit,
            include_semantic=include_semantic,
        )
        return self.retriever.retrieve(
            query=q,
            store=self.store,
            embedding_provider=self.embedding_provider,
        )

    # ── 4. Lifecycle Modifications ──

    def update(self, memory_id: str, **kwargs: Any) -> MemoryEntry | None:
        """Update an existing memory item."""
        if hasattr(self.store, "update"):
            # If content changed, regenerate embedding
            if "content" in kwargs and self.embedding_provider and self.embedding_provider.is_available:
                try:
                    emb = self.embedding_provider.embed_query(str(kwargs["content"]))
                    if emb:
                        kwargs["embedding"] = emb
                except Exception:
                    pass
            return self.store.update(memory_id, **kwargs)
        return None

    def supersede(self, old_memory_id: str, new_content: str, **kwargs: Any) -> MemoryEntry | None:
        """Explicitly supersede an existing memory with updated content."""
        # Create new memory
        new_entry = self.remember(new_content, check_supersede=False, **kwargs)
        if new_entry and hasattr(self.store, "update"):
            self.store.update(
                old_memory_id,
                status=MemoryStatus.SUPERSEDED,
                superseded_by=new_entry.id,
            )
        return new_entry

    def forget(self, memory_id: str, *, hard_delete: bool = False) -> bool:
        """Remove or soft-delete a memory item and invalidate its index entries."""
        if hasattr(self.store, "delete"):
            try:
                return self.store.delete(memory_id, hard_delete=hard_delete)
            except TypeError:
                # Fallback if delete signature does not accept keyword hard_delete
                return self.store.delete(memory_id)
        return False

    # ── 5. Summarization ──

    def summarize(
        self,
        entries: list[MemoryEntry],
        *,
        context_label: str = "Session",
        project: str = "",
        importance: float = 0.65,
    ) -> MemoryEntry | None:
        """Generate and persist a durable summary across multiple memory items."""
        if not entries:
            return None

        # Format items for summarization
        bullet_points = [f"- [{e.memory_type.value if isinstance(e.memory_type, MemoryType) else e.memory_type}] {e.content}" for e in entries]
        body = "\n".join(bullet_points)

        # Intelligent summary via ModelRouter if available
        summary_content = ""
        if self.model_router is not None:
            try:
                from models.types import ModelRequest

                req = ModelRequest(
                    prompt=(
                        f"Synthesize a concise durable memory summary of the following {context_label} items:\n\n"
                        f"{body}\n\n"
                        "Provide a clear, cohesive 2-4 sentence summary capturing all key decisions, facts, and outcomes."
                    )
                )
                resp = self.model_router.route(req).invoke(req)
                if resp.success and resp.content.strip():
                    summary_content = resp.content.strip()
            except Exception as exc:
                logger.warning("Model router summarization failed, falling back to structured summary: %s", exc)

        if not summary_content:
            # Deterministic fallback summary
            summary_content = f"{context_label} summary ({len(entries)} items):\n{body}"

        return self.remember(
            summary_content,
            memory_type=MemoryType.CONVERSATION_SUMMARY,
            project=project,
            importance=importance,
            source="summarizer",
            metadata={"summarized_ids": [e.id for e in entries]},
            check_supersede=False,
            force=True,
        )

    # ── 6. Index Maintenance ──

    def reindex(self) -> int:
        """Ensure all stored active memories have embeddings."""
        if not self.embedding_provider or not self.embedding_provider.is_available:
            return 0

        if not hasattr(self.store, "get_all_embeddings") or not hasattr(self.store, "retrieve"):
            return 0

        # Retrieve active memories missing embeddings
        all_active = self.store.retrieve(MemoryQuery(query="", status=MemoryStatus.ACTIVE, limit=1000)).entries
        existing_with_emb = {rec[0] for rec in self.store.get_all_embeddings(status="active")}

        to_embed = [e for e in all_active if e.id not in existing_with_emb]
        if not to_embed:
            return 0

        count = 0
        for entry in to_embed:
            try:
                emb = self.embedding_provider.embed_query(entry.content)
                if emb and hasattr(self.store, "store_embedding"):
                    self.store.store_embedding(entry.id, emb)
                    count += 1
            except Exception as exc:
                logger.warning("Failed to index entry '%s': %s", entry.id, exc)

        return count

