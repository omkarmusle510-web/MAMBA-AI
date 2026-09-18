"""Memory skill capability and task handler for Mamba."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.context import ExecutionContext
from memory.manager import MemoryManager
from memory.protocols import MemoryStore
from memory.types import MemoryEntry, MemoryQuery, MemoryType
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
_UPDATE_MEMORY_INTENTS = frozenset(
    {"update_memory", "modify_memory", "edit_memory"}
)
_SUMMARIZE_MEMORY_INTENTS = frozenset(
    {"summarize_memories", "summarize_memory"}
)

_MEMORY_SUPPORTED_INTENTS = frozenset(
    _REMEMBER_INTENTS
    | _RECALL_INTENTS
    | _DELETE_MEMORY_INTENTS
    | _UPDATE_MEMORY_INTENTS
    | _SUMMARIZE_MEMORY_INTENTS
)


class MemorySkill(BaseSkill):
    """Skill for storing, retrieving, and managing Mamba's structured persistent memory."""

    def __init__(
        self,
        store: MemoryStore,
        skill: Skill | None = None,
        manager: MemoryManager | None = None,
    ) -> None:
        skill_obj = skill or Skill(
            name="memory",
            description="Manage structured persistent memories: remember facts, recall past information, update, and forget entries.",
            metadata={"action": "memory", "type": "memory"},
        )
        super().__init__(skill_obj)
        if manager is not None:
            self._manager = manager
            self._store = manager.store
        elif isinstance(store, MemoryManager):
            self._manager = store
            self._store = store.store
        else:
            self._store = store
            self._manager = MemoryManager(store=store)

    @property
    def store(self) -> MemoryStore:
        return self._store

    @property
    def manager(self) -> MemoryManager:
        return self._manager

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

            mem_type = meta.get("memory_type") or meta.get("type")
            project = meta.get("project") or ""
            task = meta.get("task") or ""
            importance = meta.get("importance")

            try:
                stored = self._manager.remember(
                    str(content).strip(),
                    memory_type=mem_type,
                    project=str(project),
                    task=str(task),
                    importance=float(importance) if importance is not None else None,
                    metadata=entry_metadata,
                    force=meta.get("force", False),
                )
                if stored is None:
                    return SkillOutput(
                        content="Memory was rejected (contains credentials or ephemeral chatter).",
                        success=False,
                        metadata={"error": "rejected_memory"},
                    )
                return SkillOutput(
                    content=f"Remembered: '{stored.content}' (id: {stored.id})",
                    success=True,
                    metadata={"memory_id": stored.id, "content": stored.content, "memory_type": stored.memory_type},
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

            project = str(meta.get("project") or "")
            mem_type = meta.get("memory_type") or meta.get("type")

            try:
                result = self._manager.retrieve(
                    query=str(query_str).strip(),
                    project=project,
                    memory_type=mem_type,
                    limit=limit,
                )
                if not result.entries:
                    return SkillOutput(
                        content=f"No memories found matching '{query_str}'.",
                        success=True,
                        metadata={"matched": 0, "query": query_str},
                    )

                lines = [f"Found {len(result.entries)} matching memories:"]
                for i, entry in enumerate(result.entries, 1):
                    type_label = entry.memory_type.value if isinstance(entry.memory_type, MemoryType) else entry.memory_type
                    lines.append(f"{i}. [{entry.id}] ({type_label}) {entry.content}")

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

        # ── Update ──
        if intent in _UPDATE_MEMORY_INTENTS:
            memory_id = meta.get("id") or meta.get("memory_id")
            if not memory_id:
                return SkillOutput(
                    content="Missing required argument: 'id' or 'memory_id' to update",
                    success=False,
                    metadata={"error": "missing_id"},
                )
            update_fields = {}
            if "content" in meta:
                update_fields["content"] = meta["content"]
            if "memory_type" in meta:
                update_fields["memory_type"] = meta["memory_type"]
            if "importance" in meta:
                update_fields["importance"] = meta["importance"]
            if "project" in meta:
                update_fields["project"] = meta["project"]
            if "metadata" in meta:
                update_fields["metadata"] = meta["metadata"]

            try:
                updated = self._manager.update(str(memory_id), **update_fields)
                if updated:
                    return SkillOutput(
                        content=f"Successfully updated memory entry '{memory_id}'.",
                        success=True,
                        metadata={"updated": True, "memory_id": memory_id},
                    )
                else:
                    return SkillOutput(
                        content=f"Memory entry '{memory_id}' not found.",
                        success=False,
                        metadata={"updated": False, "memory_id": memory_id},
                    )
            except Exception as exc:
                return SkillOutput(
                    content=f"Failed to update memory: {exc}",
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

            hard_delete = bool(meta.get("hard_delete", False))
            try:
                deleted = self._manager.forget(memory_id.strip(), hard_delete=hard_delete)
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

        # ── Summarize ──
        if intent in _SUMMARIZE_MEMORY_INTENTS:
            project = str(meta.get("project") or "")
            limit = int(meta.get("limit", 10))
            try:
                result = self._manager.retrieve(query="", project=project, limit=limit)
                if not result.entries:
                    return SkillOutput(
                        content="No memories found to summarize.",
                        success=True,
                        metadata={"summarized": 0},
                    )
                summary_entry = self._manager.summarize(
                    list(result.entries),
                    context_label=project or "Project",
                    project=project,
                )
                if summary_entry:
                    return SkillOutput(
                        content=f"Summary created: '{summary_entry.content}' (id: {summary_entry.id})",
                        success=True,
                        metadata={"memory_id": summary_entry.id, "content": summary_entry.content},
                    )
                return SkillOutput(
                    content="Failed to create summary.",
                    success=False,
                    metadata={"error": "summary_failed"},
                )
            except Exception as exc:
                return SkillOutput(
                    content=f"Failed to summarize memories: {exc}",
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

        if intent in _UPDATE_MEMORY_INTENTS:
            return {
                "action": intent,
                "risk_level": "medium",
                "destructive": False,
                "user_sensitive": True,
                "irreversible": False,
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
    manager: MemoryManager | None = None,
) -> TaskExecutor:
    """Create a TaskExecutor wired to memory capability."""
    s = skill or MemorySkill(store=store, manager=manager)
    return TaskExecutor(handler=MemoryTaskHandler(memory_skill=s))
