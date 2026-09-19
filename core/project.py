"""Mamba Project Understanding - Discovery, Inspection, and Context Modeling."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from enum import Enum
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any

# Standard directories and files to ignore during inspection
IGNORED_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "env",
        ".env",
        "node_modules",
        "dist",
        "build",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        ".idea",
        ".vscode",
        ".eggs",
        "target",
        "bin",
        "obj",
        ".next",
        ".nuxt",
        "out",
    }
)

IGNORED_FILE_PATTERNS: tuple[str, ...] = (
    ".env",
    "*.pem",
    "*.key",
    "*.pfx",
    "id_rsa*",
    "id_ed25519*",
    "*.pyc",
    "*.pyo",
    "*.pyd",
    "*.so",
    "*.dylib",
    "*.dll",
    "*.exe",
    "*.sqlite",
    "*.sqlite3",
    "*.db",
    "*.lock",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "Cargo.lock",
    "poetry.lock",
)

BINARY_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".ico",
        ".svg",
        ".pdf",
        ".zip",
        ".tar",
        ".gz",
        ".tgz",
        ".rar",
        ".7z",
        ".exe",
        ".bin",
        ".dll",
        ".so",
        ".dylib",
        ".whl",
        ".pyc",
    }
)


class IssueSeverity(str, Enum):
    """Categorized severity for project problems and health checks."""

    CONFIRMED = "CONFIRMED"
    LIKELY = "LIKELY"
    POSSIBLE_CONCERN = "POSSIBLE_CONCERN"
    NO_EVIDENCE_FOUND = "NO_EVIDENCE_FOUND"


@dataclass(frozen=True, slots=True)
class ProjectIssue:
    """A detected project issue, anomaly, or concern."""

    severity: IssueSeverity
    category: str
    description: str
    file: str | None = None
    line: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity.value,
            "category": self.category,
            "description": self.description,
            "file": self.file,
            "line": self.line,
        }

    def format_line(self) -> str:
        loc = f" in {self.file}" if self.file else ""
        if self.line:
            loc += f":{self.line}"
        return f"[{self.severity.value}] {self.category}{loc}: {self.description}"


@dataclass(frozen=True, slots=True)
class GitState:
    """Read-only summary of the repository's Git working state."""

    is_repo: bool = False
    branch: str = ""
    is_dirty: bool = False
    modified_files: tuple[str, ...] = ()
    untracked_files: tuple[str, ...] = ()
    recent_commits: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_repo": self.is_repo,
            "branch": self.branch,
            "is_dirty": self.is_dirty,
            "modified_files": list(self.modified_files),
            "untracked_files": list(self.untracked_files),
            "recent_commits": list(self.recent_commits),
        }

    def format_line(self) -> str:
        if not self.is_repo:
            return "Git: Not a git repository"
        status = "dirty" if self.is_dirty else "clean"
        parts = [f"Git: branch '{self.branch}' ({status})"]
        if self.modified_files:
            parts.append(f"{len(self.modified_files)} modified")
        if self.untracked_files:
            parts.append(f"{len(self.untracked_files)} untracked")
        if self.recent_commits:
            parts.append(f"latest: '{self.recent_commits[0]}'")
        return ", ".join(parts)


@dataclass(frozen=True, slots=True)
class ProjectContext:
    """Targeted snapshot of a local software project."""

    root: Path
    name: str
    project_type: str
    entry_points: tuple[str, ...] = ()
    source_dirs: tuple[str, ...] = ()
    test_dirs: tuple[str, ...] = ()
    manifests: tuple[str, ...] = ()
    docs: tuple[str, ...] = ()
    git_state: GitState = field(default_factory=GitState)
    recent_todos: tuple[str, ...] = ()
    detected_issues: tuple[ProjectIssue, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "name": self.name,
            "project_type": self.project_type,
            "entry_points": list(self.entry_points),
            "source_dirs": list(self.source_dirs),
            "test_dirs": list(self.test_dirs),
            "manifests": list(self.manifests),
            "docs": list(self.docs),
            "git_state": self.git_state.to_dict(),
            "recent_todos": list(self.recent_todos),
            "detected_issues": [i.to_dict() for i in self.detected_issues],
        }

    def format_summary(self, max_chars: int = 2000) -> str:
        """Produce a dense, structured representation for planner and context."""
        lines = [
            f"PROJECT: {self.name} ({self.project_type}) at {self.root.name}/",
        ]
        if self.entry_points:
            lines.append(f"Entry points: {', '.join(self.entry_points)}")
        if self.source_dirs:
            lines.append(f"Source dirs: {', '.join(self.source_dirs)}")
        if self.test_dirs:
            lines.append(f"Test dirs: {', '.join(self.test_dirs)}")
        if self.manifests:
            lines.append(f"Manifests: {', '.join(self.manifests)}")
        if self.docs:
            lines.append(f"Docs: {', '.join(self.docs)}")
        if self.git_state.is_repo:
            lines.append(self.git_state.format_line())

        if self.detected_issues:
            lines.append("Detected issues:")
            for issue in self.detected_issues[:5]:
                lines.append(f"  - {issue.format_line()}")
        else:
            lines.append("Detected issues: None (NO_EVIDENCE_FOUND)")

        if self.recent_todos:
            lines.append(f"Targeted TODOs ({len(self.recent_todos)}):")
            for todo in self.recent_todos[:4]:
                lines.append(f"  - {todo}")

        content = "\n".join(lines)
        if len(content) > max_chars:
            content = content[: max_chars - 20] + "\n... [truncated]"
        return content


def is_ignored_path(path: Path | str, root: Path | None = None) -> bool:
    """Check if a path matches ignored directory or file patterns."""
    p = Path(path)
    # Check parts for ignored directories
    for part in p.parts:
        if part in IGNORED_DIRS:
            return True
        if part.endswith(".egg-info") or part.startswith(".git"):
            return True

    name = p.name.lower()
    if name == ".env" or name.startswith(".env."):
        return True
    if any(name.endswith(ext) for ext in BINARY_EXTENSIONS):
        return True
    if name.endswith(".pem") or name.endswith(".key") or name.endswith(".pfx"):
        return True
    if name.startswith("id_rsa") or name.startswith("id_ed25519"):
        return True

    return False


def find_project_root(start_path: Path | str | None = None) -> Path:
    """Find the root directory of a project by searching upward for indicators."""
    current = Path(start_path or os.getcwd()).resolve()
    if not current.is_dir() and current.parent.is_dir():
        current = current.parent

    indicators = (
        ".git",
        "pyproject.toml",
        "package.json",
        "Cargo.toml",
        "go.mod",
        "pom.xml",
        "build.gradle",
        "requirements.txt",
        "setup.py",
        "Makefile",
        "CMakeLists.txt",
    )

    candidate = current
    for _ in range(10):  # Search up to 10 levels up
        for ind in indicators:
            if (candidate / ind).exists():
                return candidate
        if candidate.parent == candidate:
            break
        candidate = candidate.parent

    return current


def get_git_state(root: Path) -> GitState:
    """Inspect Git repository state safely in a read-only manner."""
    git_dir = root / ".git"
    if not git_dir.exists():
        return GitState(is_repo=False)

    try:
        # Branch
        branch_proc = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=3,
        )
        branch = branch_proc.stdout.strip() if branch_proc.returncode == 0 else "unknown"

        # Status porcelain
        status_proc = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=3,
        )
        modified: list[str] = []
        untracked: list[str] = []
        if status_proc.returncode == 0:
            for line in status_proc.stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                code = line[:2]
                file_path = line[3:].strip()
                if is_ignored_path(file_path, root):
                    continue
                if code.startswith("??"):
                    untracked.append(file_path)
                else:
                    modified.append(file_path)

        # Recent commits (last 5)
        log_proc = subprocess.run(
            ["git", "log", "-n", "5", "--oneline"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=3,
        )
        recent_commits: list[str] = []
        if log_proc.returncode == 0:
            recent_commits = [c.strip() for c in log_proc.stdout.splitlines() if c.strip()]

        return GitState(
            is_repo=True,
            branch=branch,
            is_dirty=bool(modified or untracked),
            modified_files=tuple(modified[:20]),
            untracked_files=tuple(untracked[:20]),
            recent_commits=tuple(recent_commits[:5]),
        )
    except Exception:
        return GitState(is_repo=True, branch="error_reading_git")


def _detect_project_type(root: Path) -> tuple[str, list[str]]:
    """Identify project ecosystem/language and manifests."""
    manifests: list[str] = []
    types_found: list[str] = []

    checks = [
        ("pyproject.toml", "python"),
        ("requirements.txt", "python"),
        ("setup.py", "python"),
        ("Pipfile", "python"),
        ("package.json", "typescript/javascript"),
        ("tsconfig.json", "typescript"),
        ("Cargo.toml", "rust"),
        ("go.mod", "go"),
        ("pom.xml", "java"),
        ("build.gradle", "java/kotlin"),
        ("CMakeLists.txt", "cpp"),
        ("Makefile", "c/cpp/make"),
    ]

    for fname, ptype in checks:
        if (root / fname).is_file():
            manifests.append(fname)
            if ptype not in types_found:
                types_found.append(ptype)

    if not types_found:
        # Check if Python files exist at root or top level
        try:
            for item in root.iterdir():
                if item.is_file() and item.suffix == ".py":
                    types_found.append("python")
                    break
        except Exception:
            pass

    resolved_type = "/".join(types_found) if types_found else "generic"
    return resolved_type, manifests


def _find_entry_points(root: Path, project_type: str) -> list[str]:
    """Find common application entry points."""
    candidates = [
        "app.py",
        "main.py",
        "cli.py",
        "run.py",
        "server.py",
        "index.ts",
        "index.js",
        "src/main.py",
        "src/app.py",
        "src/index.ts",
        "src/index.js",
        "src/main.rs",
        "main.go",
    ]
    found: list[str] = []
    for c in candidates:
        if (root / c).is_file():
            found.append(c)
    return found


def _find_key_dirs(root: Path) -> tuple[list[str], list[str]]:
    """Find source and test directories."""
    src_candidates = ("src", "core", "app", "lib", "packages", "services", "skills", "tools")
    test_candidates = ("tests", "test", "spec", "specs")

    source_dirs: list[str] = []
    test_dirs: list[str] = []

    try:
        for item in root.iterdir():
            if item.is_dir() and not is_ignored_path(item, root):
                name_lower = item.name.lower()
                if name_lower in test_candidates or "test" in name_lower:
                    test_dirs.append(item.name)
                elif name_lower in src_candidates:
                    source_dirs.append(item.name)
    except Exception:
        pass

    return source_dirs, test_dirs


def _find_docs(root: Path) -> list[str]:
    """Find key documentation files or directories."""
    docs: list[str] = []
    doc_files = ("README.md", "README", "architecture.md", "CONTRIBUTING.md", "CHANGELOG.md")
    for f in doc_files:
        if (root / f).is_file():
            docs.append(f)
    if (root / "docs").is_dir():
        docs.append("docs/")
    return docs


def _collect_targeted_todos(root: Path, entry_points: list[str], source_dirs: list[str]) -> list[str]:
    """Collect targeted TODO and FIXME comments from entry points and key source files."""
    todos: list[str] = []
    files_to_check: list[Path] = [root / ep for ep in entry_points if (root / ep).is_file()]

    for sdir in source_dirs:
        pdir = root / sdir
        if pdir.is_dir():
            try:
                for f in pdir.iterdir():
                    if f.is_file() and f.suffix in (".py", ".ts", ".js", ".rs", ".go") and not is_ignored_path(f, root):
                        files_to_check.append(f)
                        if len(files_to_check) >= 15:
                            break
            except Exception:
                pass
        if len(files_to_check) >= 15:
            break

    todo_pattern = re.compile(r"(?:#|//|/\*)\s*(TODO|FIXME|XXX|BUG)(?:\(([^)]+)\))?:\s*(.+)", re.IGNORECASE)

    for fpath in files_to_check[:15]:
        try:
            content = fpath.read_text(encoding="utf-8", errors="replace")
            for idx, line in enumerate(content.splitlines(), start=1):
                match = todo_pattern.search(line)
                if match:
                    tag = match.group(1).upper()
                    text = match.group(3).strip()
                    rel_path = str(fpath.relative_to(root))
                    todos.append(f"{rel_path}:{idx} [{tag}] {text[:80]}")
                    if len(todos) >= 10:
                        return todos
        except Exception:
            continue

    return todos


def detect_project_problems(root: Path) -> tuple[ProjectIssue, ...]:
    """Detect syntax errors, broken files, merge conflicts, and potential concerns."""
    issues: list[ProjectIssue] = []

    # 1. Check for merge conflict markers in key files
    conflict_marker = re.compile(r"^(<{7}|={7}|>{7})(?:\s+.*)?$")

    # 2. Check Python syntax and conflict markers across top-level and source files (max 30 files)
    files_inspected = 0
    candidate_paths: list[Path] = []

    try:
        for item in root.iterdir():
            if item.is_file() and not is_ignored_path(item, root):
                candidate_paths.append(item)
            elif item.is_dir() and not is_ignored_path(item, root) and item.name in ("src", "core", "app", "tests"):
                for sub in item.iterdir():
                    if sub.is_file() and not is_ignored_path(sub, root):
                        candidate_paths.append(sub)
    except Exception:
        pass

    for fpath in candidate_paths:
        if files_inspected >= 40:
            break
        if fpath.suffix not in (".py", ".json", ".toml", ".yaml", ".yml", ".md", ".txt", ".ts", ".js"):
            continue

        files_inspected += 1
        rel_path = str(fpath.relative_to(root))

        try:
            text = fpath.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            issues.append(
                ProjectIssue(
                    severity=IssueSeverity.CONFIRMED,
                    category="unreadable_file",
                    description=f"Cannot read file: {exc}",
                    file=rel_path,
                )
            )
            continue

        # Check merge conflicts
        for line_num, line in enumerate(text.splitlines(), start=1):
            if conflict_marker.match(line):
                issues.append(
                    ProjectIssue(
                        severity=IssueSeverity.CONFIRMED,
                        category="merge_conflict",
                        description=f"Git merge conflict marker detected ('{line.strip()[:10]}')",
                        file=rel_path,
                        line=line_num,
                    )
                )
                break

        # Check Python syntax
        if fpath.suffix == ".py":
            try:
                ast.parse(text, filename=rel_path)
            except SyntaxError as syn_err:
                issues.append(
                    ProjectIssue(
                        severity=IssueSeverity.CONFIRMED,
                        category="syntax_error",
                        description=f"SyntaxError: {syn_err.msg}",
                        file=rel_path,
                        line=syn_err.lineno,
                    )
                )

        # Check JSON validity
        elif fpath.suffix == ".json" and fpath.name not in ("package-lock.json",):
            try:
                json.loads(text)
            except json.JSONDecodeError as json_err:
                issues.append(
                    ProjectIssue(
                        severity=IssueSeverity.CONFIRMED,
                        category="invalid_json",
                        description=f"JSONDecodeError: {json_err.msg}",
                        file=rel_path,
                        line=json_err.lineno,
                    )
                )

    # 3. Check for missing tests (POSSIBLE_CONCERN)
    has_tests = (root / "tests").is_dir() or (root / "test").is_dir()
    has_sources = (root / "src").is_dir() or (root / "core").is_dir() or any(
        f.suffix == ".py" for f in candidate_paths
    )
    if has_sources and not has_tests:
        issues.append(
            ProjectIssue(
                severity=IssueSeverity.POSSIBLE_CONCERN,
                category="test_coverage",
                description="No 'tests/' or 'test/' directory found in project",
            )
        )

    # 4. Check for dirty git state with many uncommitted files (POSSIBLE_CONCERN)
    git_dir = root / ".git"
    if git_dir.exists():
        try:
            status_proc = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=3,
            )
            if status_proc.returncode == 0:
                dirty_lines = [l for l in status_proc.stdout.splitlines() if l.strip()]
                if len(dirty_lines) > 25:
                    issues.append(
                        ProjectIssue(
                            severity=IssueSeverity.POSSIBLE_CONCERN,
                            category="git_cleanliness",
                            description=f"Large number of uncommitted/untracked changes ({len(dirty_lines)} files)",
                        )
                    )
        except Exception:
            pass

    return tuple(issues)


def find_relevant_files(
    root: Path,
    query: str,
    max_files: int = 5,
) -> list[dict[str, Any]]:
    """Pinpoint files in the project most relevant to a query/problem."""
    from memory.stopwords import STOPWORDS

    clean_query = query.lower()
    raw_tokens = re.findall(r"\b[a-zA-Z0-9_-]{2,}\b", clean_query)
    query_tokens = [t for t in raw_tokens if t not in STOPWORDS]
    if not query_tokens:
        query_tokens = raw_tokens or ["project"]

    scored_files: list[tuple[float, Path, str]] = []
    inspected_count = 0

    try:
        # Traverse up to 3 levels deep, skipping ignored directories
        for dirpath, dirnames, filenames in os.walk(root):
            # Prune ignored directories in-place
            dirnames[:] = [d for d in dirnames if not is_ignored_path(d, root) and d not in IGNORED_DIRS]

            rel_dir = os.path.relpath(dirpath, root)
            if rel_dir != "." and len(Path(rel_dir).parts) > 4:
                continue

            for fname in filenames:
                if inspected_count >= 150:
                    break
                fpath = Path(dirpath) / fname
                if is_ignored_path(fpath, root):
                    continue

                inspected_count += 1
                rel_fpath = fpath.relative_to(root)
                rel_str = str(rel_fpath).replace("\\", "/").lower()

                score = 0.0
                reasons: list[str] = []

                # Check filename / path match
                for token in query_tokens:
                    if token in fname.lower():
                        score += 3.0
                        reasons.append(f"filename contains '{token}'")
                    elif token in rel_str:
                        score += 1.5
                        reasons.append(f"path contains '{token}'")

                # Check content preview if text file
                if fpath.suffix in (".py", ".md", ".txt", ".ts", ".js", ".toml", ".json", ".yaml", ".yml"):
                    try:
                        content_sample = fpath.read_text(encoding="utf-8", errors="ignore")[:3000].lower()
                        for token in query_tokens:
                            count = content_sample.count(token)
                            if count > 0:
                                score += min(count * 0.5, 2.5)
                                reasons.append(f"mentions '{token}' ({count}x)")
                    except Exception:
                        pass

                if score > 0:
                    explanation = "; ".join(reasons[:3])
                    scored_files.append((score, rel_fpath, explanation))

            if inspected_count >= 150:
                break
    except Exception:
        pass

    scored_files.sort(key=lambda x: x[0], reverse=True)
    return [
        {"path": str(item[1]).replace("\\", "/"), "score": round(item[0], 2), "reason": item[2]}
        for item in scored_files[:max_files]
    ]


def discover_project(root_path: Path | str | None = None) -> ProjectContext:
    """Perform targeted, bounded discovery of a local project."""
    root = find_project_root(root_path)
    name = root.name or "project"
    project_type, manifests = _detect_project_type(root)
    entry_points = _find_entry_points(root, project_type)
    source_dirs, test_dirs = _find_key_dirs(root)
    docs = _find_docs(root)
    git_state = get_git_state(root)
    todos = _collect_targeted_todos(root, entry_points, source_dirs)
    issues = detect_project_problems(root)

    return ProjectContext(
        root=root,
        name=name,
        project_type=project_type,
        entry_points=tuple(entry_points),
        source_dirs=tuple(source_dirs),
        test_dirs=tuple(test_dirs),
        manifests=tuple(manifests),
        docs=tuple(docs),
        git_state=git_state,
        recent_todos=tuple(todos),
        detected_issues=issues,
    )

