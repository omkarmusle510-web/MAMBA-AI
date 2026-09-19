"""Tests for Mamba Project Understanding capability."""

from __future__ import annotations

from pathlib import Path
import pytest

from core.context import ExecutionContext
from core.project import (
    IssueSeverity,
    ProjectContext,
    ProjectIssue,
    detect_project_problems,
    discover_project,
    find_project_root,
    find_relevant_files,
    get_git_state,
    is_ignored_path,
)
from skills.project import (
    ProjectArchitectureSkill,
    ProjectGitContextSkill,
    ProjectInfoSkill,
    ProjectProblemsSkill,
    ProjectRelevantFilesSkill,
    ProjectTaskHandler,
)
from tasks.types import TaskInput, TaskOutput


def test_is_ignored_path():
    """Verify standard virtualenvs, node_modules, caches, and secrets are ignored."""
    assert is_ignored_path(".venv/lib/site-packages/pkg.py") is True
    assert is_ignored_path("node_modules/express/index.js") is True
    assert is_ignored_path("__pycache__/main.cpython-311.pyc") is True
    assert is_ignored_path(".env") is True
    assert is_ignored_path("config/.env.local") is True
    assert is_ignored_path("server.key") is True
    assert is_ignored_path("cert.pem") is True
    assert is_ignored_path("image.png") is True

    # Valid project files must NOT be ignored
    assert is_ignored_path("app.py") is False
    assert is_ignored_path("src/core/brain.py") is False
    assert is_ignored_path("tests/test_brain.py") is False
    assert is_ignored_path("README.md") is False
    assert is_ignored_path("pyproject.toml") is False


def test_discover_project_python_structure(tmp_path: Path):
    """Verify discovery of a Python project with entrypoints, source, tests, and docs."""
    proj = tmp_path / "my_app"
    proj.mkdir()
    (proj / "pyproject.toml").write_text("[project]\nname = 'my_app'\nversion = '0.1.0'\n")
    (proj / "app.py").write_text("def main():\n    print('hello')\n")
    (proj / "README.md").write_text("# My App\nDocumentation here.\n")

    src = proj / "src"
    src.mkdir()
    (src / "utils.py").write_text("def add(a, b):\n    return a + b\n")

    tests = proj / "tests"
    tests.mkdir()
    (tests / "test_utils.py").write_text("def test_add():\n    assert True\n")

    ctx = discover_project(proj)
    assert ctx.name == "my_app"
    assert "python" in ctx.project_type
    assert "app.py" in ctx.entry_points
    assert "pyproject.toml" in ctx.manifests
    assert "src" in ctx.source_dirs
    assert "tests" in ctx.test_dirs
    assert "README.md" in ctx.docs


def test_discover_project_respects_ignores(tmp_path: Path):
    """Verify that ignored directories and secrets are not included in discovery."""
    proj = tmp_path / "ignored_test"
    proj.mkdir()
    (proj / "main.py").write_text("print(1)")

    # Ignored directories
    venv = proj / ".venv"
    venv.mkdir()
    (venv / "app.py").write_text("print('fake app')")

    node_modules = proj / "node_modules"
    node_modules.mkdir()
    (node_modules / "index.js").write_text("console.log('fake')")

    # Secret files
    (proj / ".env").write_text("SECRET_KEY=12345")
    (proj / "secret.pem").write_text("KEY")

    ctx = discover_project(proj)
    assert "main.py" in ctx.entry_points
    assert ".venv" not in ctx.source_dirs
    assert "node_modules" not in ctx.source_dirs
    assert ".env" not in ctx.manifests
    assert ".env" not in ctx.docs


def test_git_state_non_repo(tmp_path: Path):
    """Verify non-git directory returns clean fallback GitState."""
    state = get_git_state(tmp_path)
    assert state.is_repo is False
    assert "Not a git repository" in state.format_line()


def test_detect_problems_syntax_error(tmp_path: Path):
    """Verify that Python syntax errors are detected and classified as CONFIRMED."""
    proj = tmp_path / "broken_proj"
    proj.mkdir()
    (proj / "app.py").write_text("def invalid_syntax(\n    return 42\n")

    issues = detect_project_problems(proj)
    syntax_issues = [i for i in issues if i.category == "syntax_error"]
    assert len(syntax_issues) == 1
    assert syntax_issues[0].severity == IssueSeverity.CONFIRMED
    assert syntax_issues[0].file == "app.py"


def test_detect_problems_merge_conflict(tmp_path: Path):
    """Verify that git merge conflict markers are detected and classified as CONFIRMED."""
    proj = tmp_path / "conflict_proj"
    proj.mkdir()
    (proj / "module.py").write_text(
        "def hello():\n"
        "<<<<<<< HEAD\n"
        "    return 'a'\n"
        "=======\n"
        "    return 'b'\n"
        ">>>>>>> feature\n"
    )

    issues = detect_project_problems(proj)
    conflict_issues = [i for i in issues if i.category == "merge_conflict"]
    assert len(conflict_issues) == 1
    assert conflict_issues[0].severity == IssueSeverity.CONFIRMED
    assert conflict_issues[0].file == "module.py"


def test_detect_problems_clean_project(tmp_path: Path):
    """Verify that a clean project with tests produces no confirmed issues."""
    proj = tmp_path / "clean_proj"
    proj.mkdir()
    (proj / "main.py").write_text("def main():\n    pass\n")
    tests = proj / "tests"
    tests.mkdir()
    (tests / "test_main.py").write_text("def test_ok():\n    assert True\n")

    issues = detect_project_problems(proj)
    confirmed_issues = [i for i in issues if i.severity == IssueSeverity.CONFIRMED]
    assert len(confirmed_issues) == 0


def test_find_relevant_files_pinpointing(tmp_path: Path):
    """Verify find_relevant_files ranks the most relevant file first based on filename and content."""
    proj = tmp_path / "relevant_proj"
    proj.mkdir()

    (proj / "auth.py").write_text("def authenticate_user(token):\n    pass\n")
    (proj / "database.py").write_text(
        "import psycopg2\n"
        "# PostgreSQL database connection handler\n"
        "def connect_db():\n"
        "    pass\n"
    )
    (proj / "server.py").write_text("def run_server():\n    pass\n")

    matches = find_relevant_files(proj, "database connection error postgres")
    assert len(matches) >= 1
    assert matches[0]["path"] == "database.py"
    assert matches[0]["score"] > 0
    assert "database" in matches[0]["reason"].lower() or "postgres" in matches[0]["reason"].lower()


def test_project_task_handler_and_skills(tmp_path: Path):
    """Verify ProjectTaskHandler executes all standard project understanding intents."""
    proj = tmp_path / "sample_proj"
    proj.mkdir()
    (proj / "app.py").write_text("print('hello')\n")
    (proj / "README.md").write_text("# Sample Project\n")

    handler = ProjectTaskHandler(root_dir=proj)
    from core.types import UserRequest
    context = ExecutionContext.from_request(UserRequest(goal="understand project"))

    # 1. project_info
    t_info = TaskInput(
        step_id="s1",
        description="get project info",
        intent="project_info",
        execution_id="e1",
        goal="What is this project?",
        step_metadata={"path": str(proj)},
    )
    meta = handler.get_metadata(t_info)
    assert meta.get("risk_level") == "low"
    assert meta.get("destructive") is False

    out_info = handler.run(t_info, context)
    assert out_info.success is True
    assert "PROJECT: sample_proj" in out_info.content
    assert "app.py" in out_info.content

    # 2. explain_architecture
    t_arch = TaskInput(
        step_id="s2",
        description="explain architecture",
        intent="explain_architecture",
        execution_id="e1",
        goal="Explain architecture",
        step_metadata={"path": str(proj)},
    )
    out_arch = handler.run(t_arch, context)
    assert out_arch.success is True
    assert "Architecture Overview" in out_arch.content

    # 3. find_problems
    t_prob = TaskInput(
        step_id="s3",
        description="find problems",
        intent="find_problems",
        execution_id="e1",
        goal="Check problems",
        step_metadata={"path": str(proj)},
    )
    out_prob = handler.run(t_prob, context)
    assert out_prob.success is True

    # 4. relevant_files
    t_rel = TaskInput(
        step_id="s4",
        description="find relevant files",
        intent="relevant_files",
        execution_id="e1",
        goal="Find app entrypoint",
        step_metadata={"path": str(proj), "query": "app entrypoint"},
    )
    out_rel = handler.run(t_rel, context)
    assert out_rel.success is True
    assert "app.py" in out_rel.content

