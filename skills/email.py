"""Email skill and task handler for Mamba."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.context import ExecutionContext
from tasks.types import TaskInput, TaskOutput
from tools.email.errors import EmailError
from tools.email.providers import EmailProvider
from tools.email.tool import EmailTool
from tools.email.types import EMAIL_OPERATIONS, EmailAction
from tools.protocols import ToolExecutor
from tools.tool import BaseTool, StandardToolExecutor
from tools.types import ToolInput

from .skill import BaseSkill, Skill
from .types import SkillInput, SkillOutput

_EMAIL_INTENTS = frozenset(
    {
        "search_emails",
        "search_email",
        "list_emails",
        "list_email",
        "read_email",
        "get_email",
        "summarize_email",
        "draft_email",
        "create_draft",
        "send_email",
        "reply_email",
        "reply",
        "email",
    }
)


def _resolve_email_action(intent_or_action: str) -> EmailAction:
    cleaned = intent_or_action.strip().lower()
    if cleaned in ("search_emails", "search_email", "search"):
        return EmailAction.SEARCH_EMAILS
    if cleaned in ("list_emails", "list_email", "list", "recent_emails", "inbox"):
        return EmailAction.LIST_EMAILS
    if cleaned in ("read_email", "get_email", "read", "inspect_email"):
        return EmailAction.READ_EMAIL
    if cleaned in ("summarize_email", "summarize", "summary"):
        return EmailAction.SUMMARIZE_EMAIL
    if cleaned in ("draft_email", "create_draft", "draft"):
        return EmailAction.DRAFT_EMAIL
    if cleaned in ("send_email", "send"):
        return EmailAction.SEND_EMAIL
    if cleaned in ("reply_email", "reply"):
        return EmailAction.REPLY_EMAIL
    return EmailAction.SEARCH_EMAILS


class EmailSkill(BaseSkill):
    """Unified skill for searching, reading, summarizing, drafting, and sending emails."""

    def __init__(
        self,
        tool: BaseTool | None = None,
        provider: EmailProvider | None = None,
        executor: ToolExecutor | None = None,
    ) -> None:
        skill = Skill(
            name="email",
            description="Manage, read, search, draft, and send emails.",
            metadata={"domain": "email"},
        )
        super().__init__(skill)
        self._tool = tool or EmailTool(provider=provider)
        self._executor = executor or StandardToolExecutor()

    def execute(self, input: SkillInput) -> SkillOutput:
        meta = input.task_input.step_metadata
        raw_intent = str(meta.get("action") or input.task_input.intent or "").strip().lower()
        action = _resolve_email_action(raw_intent)

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
        except EmailError as exc:
            return SkillOutput(
                content=f"Email execution failed: {exc}",
                success=False,
                metadata={"action": action.value, "error": str(exc)},
            )
        except Exception as exc:
            return SkillOutput(
                content=f"Email execution failed: {exc}",
                success=False,
                metadata={"action": action.value, "error": str(exc)},
            )


@dataclass(slots=True)
class EmailTaskHandler:
    """Dispatches email task inputs to EmailSkill with authoritative security metadata."""

    email_skill: EmailSkill | None = None

    def __post_init__(self) -> None:
        if self.email_skill is None:
            self.email_skill = EmailSkill()

    def get_metadata(self, task_input: TaskInput) -> dict[str, Any]:
        """Return authoritative capability security metadata for the email action."""
        raw_intent = str(
            task_input.step_metadata.get("action") or task_input.intent or ""
        ).strip().lower()
        action = _resolve_email_action(raw_intent)
        defn = EMAIL_OPERATIONS.get(action)
        if defn is not None:
            return defn.to_metadata()
        return EMAIL_OPERATIONS[EmailAction.SEARCH_EMAILS].to_metadata()

    def run(self, task_input: TaskInput, context: ExecutionContext) -> TaskOutput:
        skill_input = SkillInput.from_task(task_input, context)
        assert self.email_skill is not None
        return self.email_skill.run(skill_input).to_task_output()

