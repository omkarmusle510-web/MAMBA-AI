"""Messaging skill and task handler for Mamba."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.context import ExecutionContext
from tasks.types import TaskInput, TaskOutput
from tools.messaging.errors import MessagingError
from tools.messaging.providers import MessagingProvider
from tools.messaging.tool import MessagingTool
from tools.messaging.types import MESSAGING_OPERATIONS, MessagingAction
from tools.protocols import ToolExecutor
from tools.tool import BaseTool, StandardToolExecutor
from tools.types import ToolInput

from .skill import BaseSkill, Skill
from .types import SkillInput, SkillOutput

_MESSAGING_INTENTS = frozenset(
    {
        "list_conversations",
        "list_messages",
        "search_conversations",
        "search_messages",
        "read_messages",
        "read_message",
        "draft_message",
        "send_message",
        "reply_message",
        "message",
        "chat",
    }
)


def _resolve_messaging_action(intent_or_action: str) -> MessagingAction:
    cleaned = intent_or_action.strip().lower()
    if cleaned in ("list_conversations", "list_chats", "list_threads", "list"):
        return MessagingAction.LIST_CONVERSATIONS
    if cleaned in ("search_conversations", "search_messages", "search_chats", "search", "find_contact"):
        return MessagingAction.SEARCH_CONVERSATIONS
    if cleaned in ("read_messages", "read_message", "read_chat", "read"):
        return MessagingAction.READ_MESSAGES
    if cleaned in ("draft_message", "draft"):
        return MessagingAction.DRAFT_MESSAGE
    if cleaned in ("send_message", "send", "message"):
        return MessagingAction.SEND_MESSAGE
    if cleaned in ("reply_message", "reply"):
        return MessagingAction.REPLY_MESSAGE
    return MessagingAction.LIST_CONVERSATIONS


class MessagingSkill(BaseSkill):
    """Unified skill for conversation inspection, message reading, drafting, and sending."""

    def __init__(
        self,
        tool: BaseTool | None = None,
        provider: MessagingProvider | None = None,
        executor: ToolExecutor | None = None,
    ) -> None:
        skill = Skill(
            name="messaging",
            description="Manage, read, search, draft, and send messages.",
            metadata={"domain": "messaging"},
        )
        super().__init__(skill)
        self._tool = tool or MessagingTool(provider=provider)
        self._executor = executor or StandardToolExecutor()

    def execute(self, input: SkillInput) -> SkillOutput:
        meta = input.task_input.step_metadata
        raw_intent = str(meta.get("action") or input.task_input.intent or "").strip().lower()
        action = _resolve_messaging_action(raw_intent)

        arguments = dict(meta)
        arguments["action"] = action.value

        tool_input = ToolInput(
            arguments=arguments,
            metadata=dict(meta),
        )

        try:
            tool_output = self._executor.execute(self._tool, tool_input)
            return SkillOutput(
                content=str(tool_output.content or tool_output.result or tool_output.error or ""),
                success=tool_output.success,
                metadata={
                    **tool_output.metadata,
                    "action": action.value,
                    "result": tool_output.result,
                },
            )
        except MessagingError as exc:
            return SkillOutput(
                content=f"Messaging execution failed: {exc}",
                success=False,
                metadata={"action": action.value, "error": str(exc)},
            )
        except Exception as exc:
            return SkillOutput(
                content=f"Messaging execution failed: {exc}",
                success=False,
                metadata={"action": action.value, "error": str(exc)},
            )


@dataclass(slots=True)
class MessagingTaskHandler:
    """Dispatches messaging task inputs to MessagingSkill with authoritative security metadata."""

    messaging_skill: MessagingSkill | None = None

    def __post_init__(self) -> None:
        if self.messaging_skill is None:
            self.messaging_skill = MessagingSkill()

    def get_metadata(self, task_input: TaskInput) -> dict[str, Any]:
        """Return authoritative capability security metadata for the messaging action."""
        raw_intent = str(
            task_input.step_metadata.get("action") or task_input.intent or ""
        ).strip().lower()
        action = _resolve_messaging_action(raw_intent)
        defn = MESSAGING_OPERATIONS.get(action)
        if defn is not None:
            return defn.to_metadata()
        return MESSAGING_OPERATIONS[MessagingAction.LIST_CONVERSATIONS].to_metadata()

    def run(self, task_input: TaskInput, context: ExecutionContext) -> TaskOutput:
        skill_input = SkillInput.from_task(task_input, context)
        assert self.messaging_skill is not None
        return self.messaging_skill.run(skill_input).to_task_output()

