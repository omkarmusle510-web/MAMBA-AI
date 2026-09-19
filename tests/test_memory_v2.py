"""Tests for Mamba Long-Term Memory V2."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from core.brain import Brain
from core.context import ExecutionContext
from core.types import ExecutionPlan, ExecutionResult, Observation, PlanStep, ResultStatus, UserRequest
from memory.embedding import (
    DeterministicEmbeddingProvider,
    NullEmbeddingProvider,
)
from memory.manager import MemoryManager
from memory.persistent import PersistentStore
from memory.protocols import ExtendedMemoryStore, MemoryStore
from memory.retrieval import HybridRetriever
from memory.store import InMemoryStore
from memory.types import (
    MemoryEntry,
    MemoryQuery,
    MemoryResult,
    MemoryStatus,
    MemoryType,
)
from memory.vector_index import VectorIndex
from permissions.policy import DefaultPermissionPolicy
from tasks.executor import TaskExecutor
from tasks.types import TaskInput, TaskOutput
from verification.verifier import DefaultVerifier


class StaticPlanner:
    def __init__(self, plans: list[ExecutionPlan]) -> None:
        self._plans = list(plans)

    def plan(self, context: ExecutionContext) -> ExecutionPlan:
        if self._plans:
            return self._plans.pop(0)
        return ExecutionPlan(steps=())


class DummyExecutor:
    def execute(self, step: PlanStep, context: ExecutionContext) -> Observation:
        return Observation(step_id=step.id, content="Step finished successfully", success=True)


# 1. Persistent structured memory
def test_persistent_structured_memory(tmp_path: Path):
    db_file = tmp_path / "structured.db"
    store = PersistentStore(db_path=db_file)

    entry = MemoryEntry(
        content="Use Python 3.12 for Mamba backend",
        memory_type=MemoryType.PROJECT_DECISION,
        project="mamba",
        task="setup",
        source="cli",
        importance=0.85,
        status=MemoryStatus.ACTIVE,
        metadata={"author": "lead_dev"},
    )
    saved = store.store(entry)
    assert saved.id == entry.id
    assert saved.memory_type == MemoryType.PROJECT_DECISION
    assert saved.project == "mamba"
    assert saved.importance == 0.85

    # Retrieve and verify all fields preserved
    res = store.retrieve(MemoryQuery(project="mamba", memory_type=MemoryType.PROJECT_DECISION))
    assert len(res.entries) == 1
    loaded = res.entries[0]
    assert loaded.content == "Use Python 3.12 for Mamba backend"
    assert loaded.project == "mamba"
    assert loaded.task == "setup"
    assert loaded.source == "cli"
    assert loaded.importance == 0.85
    assert loaded.status == MemoryStatus.ACTIVE
    assert loaded.metadata == {"author": "lead_dev"}


# 2. Semantic retrieval
def test_semantic_retrieval(tmp_path: Path):
    db_file = tmp_path / "semantic.db"
    store = PersistentStore(db_path=db_file)
    provider = DeterministicEmbeddingProvider()
    index = VectorIndex()

    e1 = MemoryEntry(content="FastAPI web application framework for Python")
    e2 = MemoryEntry(content="PostgreSQL relational database schema design")
    e3 = MemoryEntry(content="Quantum physics wave particle duality")

    store.store(e1, embedding=provider.embed_query(e1.content))
    store.store(e2, embedding=provider.embed_query(e2.content))
    store.store(e3, embedding=provider.embed_query(e3.content))

    query_vec = provider.embed_query("Python web server REST API")
    hits = index.search(query_vec, store, limit=3)

    assert len(hits) >= 1
    # Most similar should be e1 (FastAPI web application framework)
    top_id, top_score = hits[0]
    assert top_id == e1.id
    assert top_score > 0.0


# 3. Hybrid retrieval
def test_hybrid_retrieval(tmp_path: Path):
    db_file = tmp_path / "hybrid.db"
    store = PersistentStore(db_path=db_file)
    provider = DeterministicEmbeddingProvider()
    retriever = HybridRetriever()

    manager = MemoryManager(store=store, embedding_provider=provider, retriever=retriever)

    manager.remember(
        "Always format code with Black and ruff",
        memory_type=MemoryType.USER_PREFERENCE,
        project="mamba",
        importance=0.9,
    )
    manager.remember(
        "Deploy Docker container to AWS ECS",
        memory_type=MemoryType.PROJECT_CONTEXT,
        project="cloud",
        importance=0.6,
    )

    # Hybrid query for formatting preference in project mamba
    result = manager.retrieve("How should I format code?", project="mamba")
    assert len(result.entries) == 1
    assert "format code with Black" in result.entries[0].content
    assert len(result.scores) == 1
    assert result.scores[0] > 0.3


# 4. Project-scoped retrieval
def test_project_scoped_retrieval(tmp_path: Path):
    db_file = tmp_path / "scoped.db"
    store = PersistentStore(db_path=db_file)
    manager = MemoryManager(store=store)

    manager.remember("Config is in config.json", project="project_alpha")
    manager.remember("Config is in settings.yaml", project="project_beta")

    res_alpha = manager.retrieve("config", project="project_alpha")
    assert len(res_alpha.entries) == 1
    assert "config.json" in res_alpha.entries[0].content

    res_beta = manager.retrieve("config", project="project_beta")
    assert len(res_beta.entries) == 1
    assert "settings.yaml" in res_beta.entries[0].content


# 5. Relevance filtering and ranking
def test_relevance_filtering_ranking(tmp_path: Path):
    db_file = tmp_path / "relevance.db"
    store = PersistentStore(db_path=db_file)
    manager = MemoryManager(store=store)

    manager.remember("The database host is db.example.com on port 5432", importance=0.8)
    manager.remember("The weather in Honolulu is sunny and 82 degrees", importance=0.3)

    # Query specifically about database
    result = manager.retrieve("What is the database host?")
    assert len(result.entries) == 1
    assert "db.example.com" in result.entries[0].content

    # Query with no match at all
    empty_result = manager.retrieve("quantum entanglement theory")
    assert len(empty_result.entries) == 0


# 6. Importance and recency behavior
def test_importance_recency_behavior(tmp_path: Path):
    db_file = tmp_path / "importance_recency.db"
    store = PersistentStore(db_path=db_file)
    retriever = HybridRetriever()

    now = datetime.now(UTC)
    old_time = now - timedelta(days=60)

    # High importance, slightly older
    e1 = MemoryEntry(
        id="high_imp",
        content="System architecture blueprint",
        importance=0.95,
        created_at=old_time,
        updated_at=old_time,
    )
    # Low importance, recent
    e2 = MemoryEntry(
        id="low_imp",
        content="System architecture sketch notes",
        importance=0.2,
        created_at=now,
        updated_at=now,
    )

    store.store(e1)
    store.store(e2)

    res = retriever.retrieve(MemoryQuery(query="System architecture"), store=store)
    assert len(res.entries) == 2
    # High importance blueprint should rank before low importance sketch notes
    assert res.entries[0].id == "high_imp"


# 7. Memory update
def test_memory_update(tmp_path: Path):
    db_file = tmp_path / "update.db"
    store = PersistentStore(db_path=db_file)
    manager = MemoryManager(store=store)

    orig = manager.remember("My primary email is old@example.com", memory_type=MemoryType.USER_FACT)
    assert orig is not None
    orig_created_at = orig.created_at

    time.sleep(0.01)
    updated = manager.update(orig.id, content="My primary email is new@example.com", importance=0.9)
    assert updated is not None
    assert updated.content == "My primary email is new@example.com"
    assert updated.created_at == orig_created_at
    assert updated.updated_at >= orig_created_at
    assert updated.importance == 0.9

    # Check retrieval returns updated content
    retrieved = manager.retrieve("primary email")
    assert len(retrieved.entries) == 1
    assert "new@example.com" in retrieved.entries[0].content


# 8. Memory supersession
def test_memory_supersession(tmp_path: Path):
    db_file = tmp_path / "supersession.db"
    store = PersistentStore(db_path=db_file)
    manager = MemoryManager(store=store)

    # First preference
    first = manager.remember(
        "I prefer dark mode in all editors",
        memory_type=MemoryType.USER_PREFERENCE,
        project="ui",
    )
    assert first is not None

    # Conflicting / updated preference in same project
    second = manager.remember(
        "I prefer light mode in all editors",
        memory_type=MemoryType.USER_PREFERENCE,
        project="ui",
    )
    assert second is not None
    assert second.id != first.id

    # First should now be superseded
    first_reloaded = store.retrieve(MemoryQuery(status=MemoryStatus.SUPERSEDED))
    assert len(first_reloaded.entries) == 1
    assert first_reloaded.entries[0].id == first.id
    assert first_reloaded.entries[0].superseded_by == second.id

    # Normal active retrieval should only return the new preference
    active_res = manager.retrieve("mode editor preference", project="ui")
    assert len(active_res.entries) == 1
    assert "light mode" in active_res.entries[0].content


# 9. Explicit forget / delete
def test_explicit_forget_delete(tmp_path: Path):
    db_file = tmp_path / "forget.db"
    store = PersistentStore(db_path=db_file)
    manager = MemoryManager(store=store)

    m1 = manager.remember("Meeting at 3pm with product team")
    m2 = manager.remember("Lunch at 12pm with design team")
    assert m1 is not None and m2 is not None

    # Soft delete m1
    deleted = manager.forget(m1.id, hard_delete=False)
    assert deleted is True

    # Check m1 is marked deleted in store
    res_deleted = store.retrieve(MemoryQuery(status=MemoryStatus.DELETED))
    assert any(e.id == m1.id for e in res_deleted.entries)

    # Hard delete m2
    hard_deleted = manager.forget(m2.id, hard_delete=True)
    assert hard_deleted is True

    # Check m2 does not exist in any status
    all_res = store.retrieve(MemoryQuery(status=None))
    assert not any(e.id == m2.id for e in all_res.entries)


# 10. Deleted memories cannot be retrieved in normal queries
def test_deleted_memories_cannot_be_retrieved(tmp_path: Path):
    db_file = tmp_path / "no_deleted.db"
    store = PersistentStore(db_path=db_file)
    manager = MemoryManager(store=store)

    m = manager.remember("Confidential internal project codename Pegasus")
    assert m is not None

    # Before deletion
    assert len(manager.retrieve("codename Pegasus").entries) == 1

    # Soft delete
    manager.forget(m.id)

    # Normal retrieval must return empty
    res = manager.retrieve("codename Pegasus")
    assert len(res.entries) == 0


# 11. Embedding index failure fallback
def test_embedding_index_failure_fallback(tmp_path: Path):
    class BrokenEmbeddingProvider:
        @property
        def is_available(self) -> bool:
            return True

        @property
        def dimension(self) -> int:
            return 128

        def embed(self, texts: list[str]) -> list[list[float]]:
            raise RuntimeError("Model download failed or connection dropped")

        def embed_query(self, text: str) -> list[float]:
            raise RuntimeError("Inference engine crashed")

    db_file = tmp_path / "fallback.db"
    store = PersistentStore(db_path=db_file)
    manager = MemoryManager(store=store, embedding_provider=BrokenEmbeddingProvider())

    # Remember should succeed despite embedding failure
    entry = manager.remember("Python asyncio concurrency model")
    assert entry is not None

    # Retrieve should fall back to keyword without raising
    res = manager.retrieve("asyncio concurrency")
    assert len(res.entries) == 1
    assert "Python asyncio" in res.entries[0].content


# 12. No provider fallback (NullEmbeddingProvider)
def test_no_provider_fallback(tmp_path: Path):
    db_file = tmp_path / "null_provider.db"
    store = PersistentStore(db_path=db_file)
    manager = MemoryManager(store=store, embedding_provider=NullEmbeddingProvider())

    manager.remember("The redis port is 6379")
    res = manager.retrieve("redis port")

    assert len(res.entries) == 1
    assert "6379" in res.entries[0].content
    assert res.metadata.get("semantic_enabled") is False


# 13. Controlled memory capture (rejects secrets and noise)
def test_controlled_memory_capture(tmp_path: Path):
    db_file = tmp_path / "capture.db"
    store = PersistentStore(db_path=db_file)
    manager = MemoryManager(store=store)

    # Secrets should be rejected
    sec1 = manager.remember("Here is the API key: api_key=sk-1234567890abcdef12345678")
    assert sec1 is None

    sec2 = manager.remember("Authorization: Bearer my_secret_token_1234567890abcdef")
    assert sec2 is None

    sec3 = manager.remember("ghp_1234567890abcdef1234567890abcdef123456")
    assert sec3 is None

    # Ephemeral chatter rejected
    chat1 = manager.remember("ok")
    assert chat1 is None

    chat2 = manager.remember("thanks!")
    assert chat2 is None

    # Valid user preference accepted
    valid = manager.remember("I prefer pytest over unittest for testing")
    assert valid is not None
    assert valid.memory_type == MemoryType.USER_PREFERENCE


# 14. Durable summarization
def test_durable_summarization(tmp_path: Path):
    db_file = tmp_path / "summary.db"
    store = PersistentStore(db_path=db_file)
    manager = MemoryManager(store=store)

    e1 = manager.remember("Discussed user authentication via OAuth2", project="auth")
    e2 = manager.remember("Decided to use JWT tokens with 1-hour expiration", project="auth")
    assert e1 is not None and e2 is not None

    summary = manager.summarize([e1, e2], context_label="OAuth Discussion", project="auth")
    assert summary is not None
    assert summary.memory_type == MemoryType.CONVERSATION_SUMMARY
    assert summary.project == "auth"
    assert "summarized_ids" in summary.metadata
    assert set(summary.metadata["summarized_ids"]) == {e1.id, e2.id}


# 15. Cross-session persistence
def test_cross_session_persistence(tmp_path: Path):
    db_file = tmp_path / "session.db"
    provider = DeterministicEmbeddingProvider()

    # Session 1: Write and close
    store1 = PersistentStore(db_path=db_file)
    manager1 = MemoryManager(store=store1, embedding_provider=provider)
    manager1.remember("User timezone is UTC+2", memory_type=MemoryType.USER_FACT, importance=0.8)
    store1.close()

    # Session 2: Reopen same file
    store2 = PersistentStore(db_path=db_file)
    manager2 = MemoryManager(store=store2, embedding_provider=provider)
    res = manager2.retrieve("user timezone")
    assert len(res.entries) == 1
    assert "UTC+2" in res.entries[0].content
    assert res.entries[0].importance == 0.8
    assert res.entries[0].memory_type == MemoryType.USER_FACT
    store2.close()


# 16. Brain core lifecycle integration
def test_brain_core_integration(tmp_path: Path):
    db_file = tmp_path / "brain_memory.db"
    store = PersistentStore(db_path=db_file)

    # Pre-populate memory with project context
    pre_entry = MemoryEntry(
        content="Target output directory is build_artifacts/",
        project="compiler",
        importance=0.9,
    )
    store.store(pre_entry)

    plan = ExecutionPlan(
        steps=(
            PlanStep(
                description="Compile source files",
                intent="compile",
                metadata={"status": "ok"},
            ),
        )
    )
    planner = StaticPlanner([plan])

    class MockTaskExecutor:
        def execute(self, step: PlanStep, context: ExecutionContext) -> Observation:
            return Observation(step_id=step.id, content="Compiled 42 files successfully", success=True)

    brain = Brain(
        planner=planner,
        executor=MockTaskExecutor(),
        memory=store,
        permissions=DefaultPermissionPolicy(),
        verifier=DefaultVerifier(),
    )

    req = UserRequest(goal="Compile project to target output directory", metadata={"project": "compiler"})
    res = brain.run(req)
    assert res.status == ResultStatus.COMPLETED

    # Memory update check: execution outcome was recorded in memory
    recalled = store.retrieve(MemoryQuery(query="Compile project"))
    assert len(recalled.entries) >= 1
    assert any("Compiled 42 files successfully" in e.content for e in recalled.entries)


# 17. Existing memory behavior compatibility
def test_existing_memory_behavior_compatible(tmp_path: Path):
    # Test that existing MemoryStore interface behaves identically to V1
    store = PersistentStore(db_path=tmp_path / "compat.db")
    assert isinstance(store, MemoryStore)
    assert isinstance(store, ExtendedMemoryStore)

    e = MemoryEntry(content="Legacy memory format without V2 kwargs")
    stored = store.store(e)
    assert stored.id == e.id
    assert stored.content == e.content
    assert stored.memory_type == MemoryType.KNOWLEDGE
    assert stored.status == MemoryStatus.ACTIVE

    query = MemoryQuery(query="Legacy memory format")
    result = store.retrieve(query)
    assert len(result.entries) == 1
    assert result.entries[0].id == e.id

    deleted = store.delete(e.id)
    assert deleted is True
    assert len(store.retrieve(query).entries) == 0


# 18. Short-fact supersession edge cases
def test_short_fact_supersession(tmp_path: Path):
    """Verify that short factual statements correctly supersede older conflicting facts."""
    db_file = tmp_path / "short_facts.db"
    store = PersistentStore(db_path=db_file)
    manager = MemoryManager(store=store)

    # Name replacement: "My name is Alice" -> "My name is Bob"
    n1 = manager.remember("My name is Alice", memory_type=MemoryType.USER_FACT)
    assert n1 is not None
    n2 = manager.remember("My name is Bob", memory_type=MemoryType.USER_FACT)
    assert n2 is not None

    res_name = store.retrieve(MemoryQuery(status=MemoryStatus.SUPERSEDED))
    assert any(e.id == n1.id and e.superseded_by == n2.id for e in res_name.entries)

    # Location replacement: "I live in Paris" -> "I live in London"
    l1 = manager.remember("I live in Paris", memory_type=MemoryType.USER_FACT)
    assert l1 is not None
    l2 = manager.remember("I live in London", memory_type=MemoryType.USER_FACT)
    assert l2 is not None

    res_loc = store.retrieve(MemoryQuery(status=MemoryStatus.SUPERSEDED))
    assert any(e.id == l1.id and e.superseded_by == l2.id for e in res_loc.entries)

    # Preference replacement: "My favorite editor is Vim" -> "My favorite editor is Emacs"
    p1 = manager.remember("My favorite editor is Vim", memory_type=MemoryType.USER_PREFERENCE)
    assert p1 is not None
    p2 = manager.remember("My favorite editor is Emacs", memory_type=MemoryType.USER_PREFERENCE)
    assert p2 is not None

    res_pref = store.retrieve(MemoryQuery(status=MemoryStatus.SUPERSEDED))
    assert any(e.id == p1.id and e.superseded_by == p2.id for e in res_pref.entries)

    # Active queries must return only current values
    assert "Bob" in manager.retrieve("my name").entries[0].content
    assert "London" in manager.retrieve("where I live").entries[0].content
    assert "Emacs" in manager.retrieve("favorite editor").entries[0].content


# 19. Unrelated short facts are NOT incorrectly superseded
def test_unrelated_short_facts_not_superseded(tmp_path: Path):
    """Verify that distinct facts sharing common structural words are NOT falsely superseded."""
    db_file = tmp_path / "unrelated.db"
    store = PersistentStore(db_path=db_file)
    manager = MemoryManager(store=store)

    f1 = manager.remember("My name is Alice", memory_type=MemoryType.USER_FACT)
    f2 = manager.remember("My car is blue", memory_type=MemoryType.USER_FACT)
    assert f1 is not None and f2 is not None

    # Both must remain active
    active = store.retrieve(MemoryQuery(status=MemoryStatus.ACTIVE))
    active_ids = {e.id for e in active.entries}
    assert f1.id in active_ids
    assert f2.id in active_ids

    # "I live in Paris" vs "I work in tech"
    f3 = manager.remember("I live in Paris", memory_type=MemoryType.USER_FACT)
    f4 = manager.remember("I work in tech", memory_type=MemoryType.USER_FACT)
    assert f3 is not None and f4 is not None

    active_after = store.retrieve(MemoryQuery(status=MemoryStatus.ACTIVE))
    active_ids_after = {e.id for e in active_after.entries}
    assert f3.id in active_ids_after
    assert f4.id in active_ids_after


# 20. Keyword retrieval fallback for all-stopword / stopword-heavy queries
def test_all_stopword_retrieval_fallback(tmp_path: Path):
    """Verify queries containing only or mostly stopwords don't return zero matches."""
    db_file = tmp_path / "stopwords_query.db"
    store = PersistentStore(db_path=db_file)

    entry1 = MemoryEntry(content="Who am I and what is my purpose")
    entry2 = MemoryEntry(content="Tell me about this project and its details")
    store.store(entry1)
    store.store(entry2)

    # All tokens in "Who am I?" are in STOPWORDS -> fallback must match entry1
    res1 = store.retrieve(MemoryQuery(query="Who am I?"))
    assert len(res1.entries) >= 1
    assert "Who am I" in res1.entries[0].content

    # "Tell me about this" is all stopwords -> fallback must match entry2
    res2 = store.retrieve(MemoryQuery(query="Tell me about this"))
    assert len(res2.entries) >= 1
    assert "Tell me about this project" in res2.entries[0].content

    # Test InMemoryStore has identical fallback behavior
    mem_store = InMemoryStore()
    mem_store.store(entry1)
    mem_store.store(entry2)

    mem_res1 = mem_store.retrieve(MemoryQuery(query="Who am I?"))
    assert len(mem_res1.entries) >= 1

    mem_res2 = mem_store.retrieve(MemoryQuery(query="Tell me about this"))
    assert len(mem_res2.entries) >= 1


# 21. Centralized STOPWORDS definition verification
def test_centralized_stopwords():
    """Verify STOPWORDS is canonically defined and identically imported across modules."""
    from memory import STOPWORDS as exported_sw
    from memory.persistent import STOPWORDS as persistent_sw
    from memory.retrieval import STOPWORDS as retrieval_sw
    from memory.stopwords import STOPWORDS as canonical_sw
    from memory.store import STOPWORDS as store_sw
    from memory.manager import STOPWORDS as manager_sw

    assert canonical_sw is exported_sw
    assert canonical_sw is persistent_sw
    assert canonical_sw is retrieval_sw
    assert canonical_sw is store_sw
    assert canonical_sw is manager_sw
    assert "the" in canonical_sw
    assert "and" in canonical_sw
    assert "who" in canonical_sw
    assert "about" in canonical_sw


