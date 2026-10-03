# Mamba Project Understanding Guide

Mamba features built-in **Project Understanding** capabilities that enable it to reason about codebases, analyze architectural structures, identify problems, and inspect local Git repositories.

> **Status: IMPLEMENTED** — capability `project_understanding` in the registry, five read-only
> intents, all local and deterministic. It inspects; it never runs a build, a test suite, or a
> linter, and it never modifies anything.

---

## 1. What Project Understanding Does

Project Understanding is **not** an external indexing database or code-crawling agent. It is a native, deterministic capability wired into Mamba Core that:

1. **Discovers Local Projects**: Locates project root markers (`pyproject.toml`, `package.json`, `Cargo.toml`, `go.mod`, `.git`, etc.) by walking up from the working directory — `core/project.py`.
2. **Analyzes Architecture**: Identifies language/framework (FastAPI, React, Django, Next.js, …), primary entry points, test layout, and core modules from the discovered structure and manifests.
3. **Detects Problems**: Static, evidence-based checks only — merge-conflict markers (`<<<<<<<`, `=======`, `>>>>>>>`), unreadable/broken files, **Python syntax errors** via compile-time parsing, and missing-test hygiene concerns, across a bounded scan (up to 30 candidate files). Findings carry a severity: `CONFIRMED`, `POSSIBLE_CONCERN`, or `NO_EVIDENCE_FOUND`.
4. **Collects Targeted TODO/FIXME/XXX/BUG comments** from entry points and key source files, so "what does this project still owe us?" has a real answer.
5. **Locates Relevant Files**: Maps a task to the most relevant files using token heuristics plus project layout and content sampling.
6. **Inspects Git Context**: Active branch, clean/dirty state from `git status --porcelain`, and the last 5 commits from `git log --oneline` — **read-only**; no commit, push, merge, checkout, or reset path exists in this capability.

Project discovery runs **automatically on every request**: the resulting `ProjectContext` (name, root, language/framework, entry points, `GitState`) is attached to the request as `project_context` metadata, so the planner always reasons with codebase awareness even when the user never mentions the project.

---

## 2. Supported Actions & Skills

Project Understanding exposes five core intent families via `skills/project.py` (each with several natural-language intent aliases mapped in `skills/mixed.py`):

| Action | Representative intents | What it returns | Risk |
| :--- | :--- | :--- | :--- |
| `project_info` | `project_info`, `inspect_project`, `what_is_this_project` | Project name, root path, detected language/framework, layout summary. | LOW |
| `explain_architecture` | `explain_architecture`, `project_architecture`, `describe_architecture` | High-level modules, entry points, and how the discovered pieces relate. | LOW |
| `find_problems` | `find_problems`, `diagnose_project`, `project_health` | Count and list of issues with severity (`CONFIRMED` / `POSSIBLE_CONCERN`), or `NO_EVIDENCE_FOUND`. | LOW |
| `relevant_files` | `relevant_files`, `locate_files`, `find_relevant_files` | Files most relevant to a query, with reasons. | LOW |
| `git_context` | `git_context`, `git_status`, `project_git_status` | Branch, clean/dirty state, changed paths, last 5 commits. | LOW |

All five are registered under the `project_understanding` capability with a read-only
contract: analysis and inspection only, no code mutation, and no git writes. Because every
action is LOW risk, none of them pauses for approval — a request like *"what is wrong with
this project?"* never needs a confirmation to be answered.

---

## 3. Example Natural Language Queries

You can ask Mamba directly in the interactive prompt or voice interface:

- *"What is this project?"*
- *"Explain the architecture of this codebase."*
- *"What files are relevant to authentication?"*
- *"Are there any problems or failing tests right now?"*
- *"What is the current git status and recent commit history?"*

---

## 4. Multi-Turn Referent Resolution

When working with projects, Mamba automatically tracks active entities across conversation turns:

```text
mamba> List the files in core/
[Status: completed]
- brain.py
- runtime.py
- types.py
...

mamba> What does the second file do?
[Status: completed]
core/runtime.py serves as the canonical application runtime boundary...
```

Active entities such as current file, repository, directory, and prior turn outcomes are carried forward in the `ExecutionContext` so you can speak naturally using pronouns (*"it"*, *"that"*, *"the file"*). Repository references in goals (e.g. `github.com/<owner>/<repo>`) are also captured as the active repository for follow-up questions.

---

## 5. Deliberate boundaries

| It does | It does not |
| :--- | :--- |
| Read manifests, layout, source files, and `git status` / `git log` | Run tests, builds, linters, or type checkers |
| Compile-check Python files it scans for syntax errors | Report IDE-level diagnostics or dependency-resolution failures |
| Scan a bounded set (≤30 candidate files) for problems | Crawl or index the whole filesystem |
| Describe what is present | Edit, create, delete, or refactor anything |
| Summarize local git state | Commit, push, merge, branch, checkout, or reset |

Remote repository inspection is a **different** capability (`github`, §3.8 of
[SKILLS.md](SKILLS.md)), which reads the GitHub REST API rather than a working tree.

---

## Related documents

- [Architecture Specification](ARCHITECTURE.md) — §11 project understanding, §3 lifecycle
- [Skills & Capabilities Reference](SKILLS.md) — the full capability surface
- [Long-Term Memory](MEMORY.md) — `project_context` / `project_decision` memory types
