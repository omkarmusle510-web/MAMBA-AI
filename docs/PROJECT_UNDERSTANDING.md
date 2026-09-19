# Mamba Project Understanding Guide

Mamba features built-in **Project Understanding** capabilities that enable it to reason about codebases, analyze architectural structures, identify bugs, and inspect local Git repositories.

---

## 1. What Project Understanding Does

Project Understanding is **not** an external indexing database or code-crawling agent. It is a native, deterministic capability wired into Mamba Core that:

1. **Discovers Local Projects**: Locates project root markers (`pyproject.toml`, `package.json`, `Cargo.toml`, `go.mod`, `.git`, etc.).
2. **Analyzes Architecture**: Identifies project frameworks (FastAPI, React, Django, Next.js, etc.), primary entry points, test suites, and core modules.
3. **Detects Problems**: Inspects workspace diagnostics, missing dependencies, syntax issues, and failing tests.
4. **Locates Relevant Files**: Maps user tasks to the most relevant files using token heuristics and project layout context.
5. **Inspects Git Context**: Evaluates active branch, uncommitted diffs, recent commit logs, and repository status.

---

## 2. Supported Actions & Skills

Project Understanding exposes five core intents via `skills/project.py`:

| Action | Intent | Description |
| :--- | :--- | :--- |
| `project_info` | `project_info` | Returns project name, root path, detected language/framework, and directory layout. |
| `explain_architecture` | `explain_architecture` | Outlines high-level modules, data flow, entry points, and dependencies. |
| `find_problems` | `find_problems` | Summarizes compiler errors, lint issues, test failures, or broken imports. |
| `relevant_files` | `relevant_files` | Identifies files relevant to a specific feature, bug, or query. |
| `git_context` | `git_context` | Summarizes current git branch, staged/unstaged changes, and recent commit history. |

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

Active entities such as current file, repository, directory, and prior turn outcomes are carried forward in the `ExecutionContext` so you can speak naturally using pronouns (*"it"*, *"that"*, *"the file"*).

