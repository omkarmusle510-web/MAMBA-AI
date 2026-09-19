"""Project Understanding skills and task handler for Mamba."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.context import ExecutionContext
from core.project import (
    GitState,
    IssueSeverity,
    ProjectContext,
    ProjectIssue,
    detect_project_problems,
    discover_project,
    find_project_root,
    find_relevant_files,
    get_git_state,
)
from tasks.types import TaskInput, TaskOutput

from .skill import BaseSkill, Skill
from .types import SkillInput, SkillOutput

_PROJECT_INFO_INTENTS = frozenset(
    {
        "project_info",
        "what_is_this_project",
        "project_summary",
        "inspect_project",
        "project",
    }
)

_ARCHITECTURE_INTENTS = frozenset(
    {
        "explain_architecture",
        "project_architecture",
        "architecture",
        "describe_architecture",
    }
)

_PROBLEMS_INTENTS = frozenset(
    {
        "find_problems",
        "diagnose_project",
        "project_health",
        "check_problems",
        "detect_problems",
        "project_issues",
    }
)

_RELEVANT_FILES_INTENTS = frozenset(
    {
        "relevant_files",
        "find_relevant_files",
        "related_files",
        "locate_files",
    }
)

_GIT_CONTEXT_INTENTS = frozenset(
    {
        "git_context",
        "project_git_status",
        "git_status",
        "git_state",
    }
)


class ProjectInfoSkill(BaseSkill):
    """Summarizes project type, entry points, directories, and documentation."""

    def __init__(self, root_dir: str | Path | None = None) -> None:
        super().__init__(
            Skill(
                name="project_info",
                description="Discover and summarize the current software project structure.",
                metadata={
                    "action": "project_info",
                    "risk_level": "low",
                    "destructive": False,
                    "idempotent": True,
                },
            )
        )
        self._root_dir = Path(root_dir) if root_dir else None

    def execute(self, input: SkillInput) -> SkillOutput:
        path = input.task_input.step_metadata.get("path") or self._root_dir
        ctx = discover_project(path)
        summary = ctx.format_summary()
        return SkillOutput(
            content=summary,
            success=True,
            metadata={
                "project_name": ctx.name,
                "project_type": ctx.project_type,
                "root": str(ctx.root),
                "entry_points": list(ctx.entry_points),
                "source_dirs": list(ctx.source_dirs),
                "test_dirs": list(ctx.test_dirs),
            },
        )


class ProjectArchitectureSkill(BaseSkill):
    """Explains project architecture based on layout, manifests, and modules."""

    def __init__(self, root_dir: str | Path | None = None) -> None:
        super().__init__(
            Skill(
                name="explain_architecture",
                description="Analyze and explain high-level project architecture.",
                metadata={
                    "action": "explain_architecture",
                    "risk_level": "low",
                    "destructive": False,
                    "idempotent": True,
                },
            )
        )
        self._root_dir = Path(root_dir) if root_dir else None

    def execute(self, input: SkillInput) -> SkillOutput:
        path = input.task_input.step_metadata.get("path") or self._root_dir
        ctx = discover_project(path)

        lines = [
            f"# Architecture Overview: {ctx.name} ({ctx.project_type})",
            "",
            "## 1. Project Organization",
            f"- **Root**: `{ctx.root.name}/`",
            f"- **Ecosystem**: {ctx.project_type}",
            f"- **Entry Points**: {', '.join(f'`{ep}`' for ep in ctx.entry_points) or 'None explicitly identified'}",
            f"- **Core Source Directories**: {', '.join(f'`{sd}`' for sd in ctx.source_dirs) or 'Root layout'}",
            f"- **Test Suite**: {', '.join(f'`{td}`' for td in ctx.test_dirs) or 'No test directories found'}",
            f"- **Manifests / Config**: {', '.join(f'`{m}`' for m in ctx.manifests) or 'None'}",
            "",
            "## 2. Key Documentation & Specs",
            f"{', '.join(f'`{d}`' for d in ctx.docs) or 'No top-level documentation files discovered'}",
            "",
            "## 3. Working State",
            ctx.git_state.format_line(),
        ]
        return SkillOutput(
            content="\n".join(lines),
            success=True,
            metadata={"project_name": ctx.name, "project_type": ctx.project_type},
        )


class ProjectProblemsSkill(BaseSkill):
    """Detects syntax errors, merge conflicts, and project hygiene issues."""

    def __init__(self, root_dir: str | Path | None = None) -> None:
        super().__init__(
            Skill(
                name="find_problems",
                description="Detect syntax errors, merge conflicts, and project hygiene issues.",
                metadata={
                    "action": "find_problems",
                    "risk_level": "low",
                    "destructive": False,
                    "idempotent": True,
                },
            )
        )
        self._root_dir = Path(root_dir) if root_dir else None

    def execute(self, input: SkillInput) -> SkillOutput:
        path = input.task_input.step_metadata.get("path") or self._root_dir
        root = find_project_root(path)
        issues = detect_project_problems(root)

        if not issues:
            return SkillOutput(
                content=f"No problems detected in project '{root.name}'. (Status: {IssueSeverity.NO_EVIDENCE_FOUND.value})",
                success=True,
                metadata={"issues_count": 0, "status": IssueSeverity.NO_EVIDENCE_FOUND.value},
            )

        lines = [f"Found {len(issues)} issue(s) in project '{root.name}':"]
        for issue in issues:
            lines.append(f"- {issue.format_line()}")

        return SkillOutput(
            content="\n".join(lines),
            success=True,
            metadata={
                "issues_count": len(issues),
                "issues": [i.to_dict() for i in issues],
            },
        )


class ProjectRelevantFilesSkill(BaseSkill):
    """Identifies files relevant to a query or bug description."""

    def __init__(self, root_dir: str | Path | None = None) -> None:
        super().__init__(
            Skill(
                name="relevant_files",
                description="Find the most relevant source files for a task or bug query.",
                metadata={
                    "action": "relevant_files",
                    "risk_level": "low",
                    "destructive": False,
                    "idempotent": True,
                },
            )
        )
        self._root_dir = Path(root_dir) if root_dir else None

    def execute(self, input: SkillInput) -> SkillOutput:
        path = input.task_input.step_metadata.get("path") or self._root_dir
        root = find_project_root(path)
        query = (
            input.task_input.step_metadata.get("query")
            or input.task_input.step_metadata.get("text")
            or input.task_input.context.request.goal
        )
        max_files = int(input.task_input.step_metadata.get("max_files", 5))

        matches = find_relevant_files(root, query, max_files=max_files)
        if not matches:
            return SkillOutput(
                content=f"No specific files matched the query '{query}' in project '{root.name}'.",
                success=True,
                metadata={"matches": []},
            )

        lines = [f"Relevant files for query '{query}':"]
        for m in matches:
            lines.append(f"- `{m['path']}` (score: {m['score']}) — {m['reason']}")

        return SkillOutput(
            content="\n".join(lines),
            success=True,
            metadata={"matches": matches},
        )


class ProjectGitContextSkill(BaseSkill):
    """Reports Git branch, clean/dirty working tree, and recent commit history."""

    def __init__(self, root_dir: str | Path | None = None) -> None:
        super().__init__(
            Skill(
                name="git_context",
                description="Inspect Git status, active branch, and recent commit history.",
                metadata={
                    "action": "git_context",
                    "risk_level": "low",
                    "destructive": False,
                    "idempotent": True,
                },
            )
        )
        self._root_dir = Path(root_dir) if root_dir else None

    def execute(self, input: SkillInput) -> SkillOutput:
        path = input.task_input.step_metadata.get("path") or self._root_dir
        root = find_project_root(path)
        git_state = get_git_state(root)
        return SkillOutput(
            content=git_state.format_line(),
            success=True,
            metadata=git_state.to_dict(),
        )


@dataclass(slots=True)
class ProjectTaskHandler:
    """Dispatches project-understanding intents to concrete skills."""

    project_info_skill: ProjectInfoSkill | None = None
    architecture_skill: ProjectArchitectureSkill | None = None
    problems_skill: ProjectProblemsSkill | None = None
    relevant_files_skill: ProjectRelevantFilesSkill | None = None
    git_context_skill: ProjectGitContextSkill | None = None
    root_dir: Path | None = None

    def __post_init__(self) -> None:
        if self.project_info_skill is None:
            self.project_info_skill = ProjectInfoSkill(root_dir=self.root_dir)
        if self.architecture_skill is None:
            self.architecture_skill = ProjectArchitectureSkill(root_dir=self.root_dir)
        if self.problems_skill is None:
            self.problems_skill = ProjectProblemsSkill(root_dir=self.root_dir)
        if self.relevant_files_skill is None:
            self.relevant_files_skill = ProjectRelevantFilesSkill(root_dir=self.root_dir)
        if self.git_context_skill is None:
            self.git_context_skill = ProjectGitContextSkill(root_dir=self.root_dir)

    def get_metadata(self, task_input: TaskInput) -> dict[str, Any]:
        """Return authoritative capability security metadata for this intent."""
        intent = (
            task_input.step_metadata.get("action")
            or task_input.intent
            or ""
        ).strip().lower()

        return {
            "action": intent,
            "risk_level": "low",
            "destructive": False,
            "reversible": True,
            "requires_confirmation": False,
        }

    def run(self, task_input: TaskInput, context: ExecutionContext) -> TaskOutput:
        intent = (
            task_input.step_metadata.get("action")
            or task_input.intent
            or ""
        ).strip().lower()

        skill_input = SkillInput.from_task(task_input, context)

        if intent in _PROJECT_INFO_INTENTS:
            assert self.project_info_skill is not None
            return self.project_info_skill.run(skill_input).to_task_output()

        if intent in _ARCHITECTURE_INTENTS:
            assert self.architecture_skill is not None
            return self.architecture_skill.run(skill_input).to_task_output()

        if intent in _PROBLEMS_INTENTS:
            assert self.problems_skill is not None
            return self.problems_skill.run(skill_input).to_task_output()

        if intent in _RELEVANT_FILES_INTENTS:
            assert self.relevant_files_skill is not None
            return self.relevant_files_skill.run(skill_input).to_task_output()

        if intent in _GIT_CONTEXT_INTENTS:
            assert self.git_context_skill is not None
            return self.git_context_skill.run(skill_input).to_task_output()

        return TaskOutput(
            content=f"unsupported project capability intent: '{task_input.intent}'",
            success=False,
            metadata={"error": "unsupported_capability"},
        )

