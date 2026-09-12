"""Web search skill and task handler for Mamba."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.context import ExecutionContext
from tasks.types import TaskInput, TaskOutput
from tools.protocols import ToolExecutor
from tools.tool import BaseTool, StandardToolExecutor
from tools.types import ToolInput
from tools.web.search import WebSearchTool
from tools.web.types import WEB_OPERATIONS, WebSearchAction

from .skill import BaseSkill, Skill
from .types import SkillInput, SkillOutput

_WEB_SEARCH_INTENTS = frozenset({"web_search"})


class WebSearchSkill(BaseSkill):
    """Skill for executing web searches and retrieving web intelligence."""

    def __init__(
        self,
        tool: BaseTool | None = None,
        executor: ToolExecutor | None = None,
    ) -> None:
        defn = WEB_OPERATIONS[WebSearchAction.WEB_SEARCH]
        super().__init__(
            Skill(
                name=defn.name,
                description=defn.description,
                metadata=defn.to_metadata(),
            )
        )
        self._tool = tool or WebSearchTool()
        self._executor = executor or StandardToolExecutor()

    def execute(self, input: SkillInput) -> SkillOutput:
        meta = input.task_input.step_metadata
        query = meta.get("query") or meta.get("q") or input.task_input.description

        arguments: dict[str, Any] = {"query": query}
        if "max_results" in meta:
            arguments["max_results"] = meta["max_results"]

        tool_input = ToolInput(
            arguments=arguments,
            metadata=dict(meta),
        )
        tool_output = self._executor.execute(self._tool, tool_input)
        return SkillOutput(
            content=str(tool_output.result or tool_output.error or ""),
            success=tool_output.success,
            metadata=tool_output.metadata,
        )


@dataclass(slots=True)
class WebTaskHandler:
    """Dispatches web search task inputs to the WebSearchSkill."""

    web_search_skill: WebSearchSkill | None = None

    def __post_init__(self) -> None:
        if self.web_search_skill is None:
            self.web_search_skill = WebSearchSkill()

    def get_metadata(self, task_input: TaskInput) -> dict[str, Any]:
        """Return authoritative capability security metadata for web search."""
        return WEB_OPERATIONS[WebSearchAction.WEB_SEARCH].to_metadata()

    def run(self, task_input: TaskInput, context: ExecutionContext) -> TaskOutput:
        skill_input = SkillInput.from_task(task_input, context)
        assert self.web_search_skill is not None
        return self.web_search_skill.run(skill_input).to_task_output()

