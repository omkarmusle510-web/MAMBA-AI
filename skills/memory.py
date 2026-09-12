"""Memory skill capability and task handler for Mamba."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.context import ExecutionContext
from memory.protocols import MemoryStore
from memory.types import MemoryEntry, MemoryQuery
from tasks.executor import TaskExecutor
from tasks.types import TaskInput, TaskOutput

from .skill import BaseSkill, Skill
from .types import SkillInput, SkillOutput

_REMEMBER_INTENTS = frozenset(
    {"remember", "store_memory", "save_memory", "record_memory"}
)
_RECALL_INTENTS = frozenset(
    {"recall", "search_memory", "retrieve_memory", "get_memory", "query_memory"}
)
_DELETE_MEMORY_INTENTS = frozenset(
    {"delete_memory", "forget", "remove_memory", "clear_memory"}
)

_MEMORY_SUPPORTED_INTENTS = frozenset(
    _REMEMBER_INTENTS | _RECALL_INTENTS | _DELETE_MEMORY_INTENTS
)


class MemorySkill(BaseSkill):
    """Skill for storing, retrieving, and managing Mamba's structured persistent memory."""

    def __init__(
        self,
        store: MemoryStore,
        skill: Skill | None = None,
    ) -> None:
        skill_obj = skill or Skill(
            name="memory",
            description="Manage structured persistent memories: remember facts, recall past information, and forget entries.",
            metadata={"action": "memory", "type": "memory"},
        )
        super().__init__(skill_obj)
        self._store = store

    @property
    def store(self) -> MemoryStore:
        return self._store

    def execute(self, input: SkillInput) -> SkillOutput:
        action = str(input.task_input.step_metadata.get("action") or "").strip().lower()
        raw_intent = (input.task_input.intent or "").strip().lower()
        intent = action if action in _MEMORY_SUPPORTED_INTENTS else raw_intent

        if intent not in _MEMORY_SUPPORTED_INTENTS:
            return SkillOutput(
                content=f"unsupported memory capability intent: '{input.task_input.intent}'",
                success=False,
                metadata={"error": "unsupported_capability"},
            )

        meta = input.task_input.step_metadata

        # ── Remember / Store ──
        if intent in _REMEMBER_INTENTS:
            content = (
                meta.get("content")
                or meta.get("text")
                or meta.get("note")
                or meta.get("fact")
            )
            if not content:
                # If description is meaningful, use it
                desc = input.task_input.description.strip()
                if desc and not desc.lower().startswith("remember"):
                    content = desc
                elif desc.lower().startswith("remember "):
                    content = desc[9:].strip()
                else:
                    return SkillOutput(
                        content="Missing required argument: 'content' to remember",
                        success=False,
                        metadata={"error": "missing_content"},
                    )

            entry_metadata = dict(meta.get("metadata") or {})
            if "goal" not in entry_metadata and input.task_input.goal:
                entry_metadata["goal"] = input.task_input.goal
            if input.task_input.execution_id:
                entry_metadata["execution_id"] = input.task_input.execution_id

            entry = MemoryEntry(content=str(content).strip(), metadata=entry_metadata)
            try:
                stored = self._store.store(entry)
                return SkillOutput(
                    content=f"Remembered: '{stored.content}' (id: {stored.id})",
                    success=True,
                    metadata={"memory_id": stored.id, "content": stored.content},
                )
            except Exception as exc:
                return SkillOutput(
                    content=f"Failed to store memory: {exc}",
                    success=False,
                    metadata={"error": str(exc)},
                )

        # ── Recall / Retrieve ──
        if intent in _RECALL_INTENTS:
            query_str = (
                meta.get("query")
                or meta.get("q")
                or meta.get("search")
                or meta.get("text")
                or ""
            )
            if not query_str:
                query_str = input.task_input.goal or ""

            limit = meta.get("limit", 5)
            if not isinstance(limit, int) or limit <= 0:
                limit = 5

            try:
                query = MemoryQuery(query=str(query_str).strip(), limit=limit)
                result = self._store.retrieve(query)
                if not result.entries:
                    return SkillOutput(
                        content=f"No memories found matching '{query_str}'.",
                        success=True,
                        metadata={"matched": 0, "query": query_str},
                    )

                lines = [f"Found {len(result.entries)} matching memories:"]
                for i, entry in enumerate(result.entries, 1):
                    lines.append(f"{i}. [{entry.id}] {entry.content}")

                return SkillOutput(
                    content="\n".join(lines),
                    success=True,
                    metadata={
                        "matched": len(result.entries),
                        "entries": [e.content for e in result.entries],
                        "memory_ids": [e.id for e in result.entries],
                    },
                )
            except Exception as exc:
                return SkillOutput(
                    content=f"Failed to recall memory: {exc}",
                    success=False,
                    metadata={"error": str(exc)},
                )

        # ── Delete / Forget ──
        if intent in _DELETE_MEMORY_INTENTS:
            memory_id = meta.get("id") or meta.get("memory_id")
            if not memory_id or not isinstance(memory_id, str):
                return SkillOutput(
                    content="Missing required argument: 'id' or 'memory_id' to delete",
                    success=False,
                    metadata={"error": "missing_id"},
                )

            try:
                deleted = self._store.delete(memory_id.strip())
                if deleted:
                    return SkillOutput(
                        content=f"Successfully deleted memory entry '{memory_id}'.",
                        success=True,
                        metadata={"deleted": True, "memory_id": memory_id},
                    )
                else:
                    return SkillOutput(
                        content=f"Memory entry '{memory_id}' not found.",
                        success=False,
                        metadata={"deleted": False, "memory_id": memory_id},
                    )
            except Exception as exc:
                return SkillOutput(
                    content=f"Failed to delete memory: {exc}",
                    success=False,
                    metadata={"error": str(exc)},
                )

        return SkillOutput(
            content=f"unsupported memory capability intent: '{intent}'",
            success=False,
            metadata={"error": "unsupported_capability"},
        )


@dataclass(slots=True)
class MemoryTaskHandler:
    """Adapts MemorySkill to the TaskHandler interface."""

    memory_skill: MemorySkill

    def get_metadata(self, task_input: TaskInput) -> dict[str, Any]:
        """Return authoritative capability security metadata for memory intents."""
        intent = (
            task_input.step_metadata.get("action")
            or task_input.intent
            or ""
        ).strip().lower()

        if intent in _DELETE_MEMORY_INTENTS:
            return {
                "action": intent,
                "risk_level": "medium",
                "destructive": True,
                "user_sensitive": True,
                "irreversible": True,
            }

        return {
            "action": intent or "memory",
            "risk_level": "low",
            "destructive": False,
            "user_sensitive": False,
            "irreversible": False,
        }

    def run(self, task_input: TaskInput, context: ExecutionContext) -> TaskOutput:
        skill_input = SkillInput.from_task(task_input, context)
        return self.memory_skill.run(skill_input).to_task_output()


def create_memory_task_executor(
    store: MemoryStore,
    *,
    skill: MemorySkill | None = None,
) -> TaskExecutor:
    """Create a TaskExecutor wired to memory capability."""
    s = skill or MemorySkill(store=store)
    return TaskExecutor(handler=MemoryTaskHandler(memory_skill=s))
