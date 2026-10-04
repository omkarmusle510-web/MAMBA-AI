# Mamba AI Architecture Specification

> **Mamba** is a personal AI operating layer between the user and digital tools.

This document describes the system **as implemented**. Every claim here is grounded in the
repository source. Where a capability is incomplete, simulated, or only planned, it is
labeled as such — see §22.

**Companion documents:** [SKILLS.md](SKILLS.md) is the per-capability reference (actions,
risk gates, known gaps); [SECURITY.md](SECURITY.md) is the trust-boundary specification;
[MEMORY.md](MEMORY.md), [VOICE_INTERFACE.md](VOICE_INTERFACE.md), and
[PROJECT_UNDERSTANDING.md](PROJECT_UNDERSTANDING.md) cover their subsystems in depth.

---

## 1. Product Purpose & Core Vision

Mamba exists to be the single trusted layer between a user and everything their computer can do. Instead of juggling separate apps, scripts, and chatbots, the user states a goal in natural language (typed or spoken) and Mamba:

1. understands the goal and resolves references to prior conversation ("it", "that file", "the error"),
2. recalls relevant long-term memory and project context,
3. plans a short sequence of concrete, tool-grounded steps,
4. checks every step against a permissions policy *before* executing,
5. executes, observes real output, and verifies the outcome actually happened,
6. replans on failure instead of looping blindly,
7. persists only durable facts to memory, and
8. responds in text, speech, or the desktop UI.

Design commitments:

- **One brain.** All intelligence and execution authority lives in Mamba Core (`core/`). Interfaces (CLI, voice, React, Electron) are presentation; the transport adapter is plumbing.
- **Permissions before action.** The model proposes; the policy disposes. High-risk actions pause for explicit user approval and resume cleanly.
- **Verify, don't assume.** A step is complete only when its outcome is observed and — where applicable — checked against a predicate.
- **Deterministic lifecycle.** A bounded, observation-driven loop (max 10 reasoning cycles) with identical-plan detection, not an open-ended agent loop.
- **Provider independence.** Models are swappable intelligence providers behind a router; memory embeddings run locally.

---

## 2. System Overview: User → Interface → Transport → Runtime → Core

```
USER
 │  text · speech · click · hotkey
 ▼
INTERFACE ── presentation only. No planning, no model routing, no tool execution.
 ├─► app.py CLI .................... interactive shell, one-shot commands
 ├─► voice/ VoiceInterface ......... microphone loop (STT → Core → TTS)
 └─► Electron desktop app .......... React UI (MambaApp) + Floating Orb window
 │   communicates with the backend over HTTP/WebSocket only
 ▼
TRANSPORT ── api/server.py .......... FastAPI adapter. ZERO planning, ZERO
 │   routing, ZERO tool logic.       endpoints: /health, /api/chat,
 │   /api/voice, /live (WebSocket),  /api/settings, /api/reminders
 ▼
RUNTIME ── core/runtime.py .......... MambaRuntime: the single
 │   application-facing boundary.    run(request, on_progress) → Brain.run()
 ▼
CORE ── core/brain.py ............... Brain: the sole intelligence/execution
    │   system. Owns the lifecycle:  Intake → Context → Memory → Planning →
    │   Permissions → Execution →    Observation → Verification →
    │   Memory Update → Response     Memory → Response.
    │
    ├─► agents/ ............ PlanningAgent (LLM → JSON ExecutionPlan)
    ├─► models/ ............ DefaultModelRouter → NVIDIA / Groq / Gemini
    ├─► skills/ + tools/ .... reusable capabilities → external actions
    ├─► permissions/ ........ DefaultPermissionPolicy (ALLOW / ASK / DENY)
    ├─► verification/ ....... DefaultVerifier (outcome predicates)
    └─► memory/ ............. SQLite + local embeddings, supersession
```

### Canonical composition (`app.py :: create_runtime`)

`create_runtime()` is the single wiring point. It:

1. Builds the provider list from environment credentials — NVIDIA (`NVIDIAModelProvider`, incl. a vision variant), Groq, Gemini. Providers whose keys are missing are skipped; **at least one is required** or startup fails with a clear error.
2. Creates the default capability registry (`core/capabilities.py`).
3. Creates `DefaultModelRouter(providers)`, `PlanningAgent(router, capabilities)`, `AgentPlanner(planning_agent)`.
4. Creates `PersistentStore()` (SQLite) unless a memory store is injected.
5. Creates the mixed task executor (`skills/mixed.py :: create_mixed_task_executor`) binding every intent to its skill handler.
6. Assembles `Brain(planner, executor, memory, model_router, capabilities)` and wraps it in `MambaRuntime`.

`create_brain()` exists as a backwards-compatible convenience returning the same Brain.

---

## 3. Core Execution Lifecycle

`Brain.run(request)` executes one canonical lifecycle. (Names below match the implementation.)

```
Request
  │  _intake: normalize str/UserRequest; reject empty/invalid
  │  attach capability summary + system context for the planner
  │  propagate prior-turn context & active entities (file, folder, repo, …)
  │  handle approval / denial / replacement phrases ("yes", "no, instead …")
  │  resolve referents: "that file" → active file, "the repo" → active repo …
  ▼
Context ── ExecutionContext.from_request; project auto-discovery
  │        (core/project.py: root markers, framework, entry points)
  ▼
Memory ── retrieve top-5 relevant memories (MemoryManager → store +
  │       embeddings, keyword fallback). Failures never crash execution.
  ▼
Planning ── PlanningAgent → ModelRouter → LLM → validated JSON
  │         ExecutionPlan { steps: [PlanStep{description, intent, metadata}],
  │         needs_replanning }. Planner prompt is grounded by the
  │         CapabilityRegistry: only advertised intents can be planned.
  ▼
Execution (bounded loop, max 10 cycles):
  │  per step:
  │   1. capability availability check (NOT_CONFIGURED → honest message)
  │   2. permission evaluation → ALLOW executes; ASK pauses with
  │      PendingApproval; DENY fails the step
  │   3. execute via mixed task executor → Observation{content, success, metadata}
  │   4. evaluate observation → continue / replan / finish / fail
  │   5. verification (when applicable) → failed verification replans
  │  loop guards: skip already-completed step signatures; stop on
  │  identical-plan repetition; stop when plan exhausts cycles
  ▼
Observation ── every step's real output is captured into the context and
  │            feeds the next planning cycle (observation-driven replanning)
  ▼
Verification ── DefaultVerifier checks expectations:
  │            contains / not_contains / exit_code / file_exists /
  │            file_absent / content_matches / provider_verified …
  ▼
Memory ── _update_memory: persists durable outcomes only.
  │        Transient reads (list_dir, read_file, system_info, screenshot,
  │        web_search, …) are filtered. Screen interpretations are excluded.
  ▼
Response ── ExecutionResult{status, output, observations} → console / TTS / WS
```

Multi-turn continuity is explicit state on the Brain: `_pending_approval` (paused step + context + plan), `_last_turn_context`, and `_active_entities` (file, folder, repository, command, url, email, meeting, conversation) extracted from observation metadata and the user's goal text.

---

## 4. Orchestration

**The orchestrator is `core/brain.py :: Brain`.** It coordinates the bounded reason → plan → execute → observe → verify loop described above. `core/runtime.py :: MambaRuntime` is a thin boundary that delegates to `Brain.run()`; it exists so every interface (CLI, voice, transport) shares one entry point.

> Note: `core/orchestrator.py` defines a legacy `Orchestrator` class that is **not** on the live execution path (nothing outside `core/__init__.py` imports it). Do not build on it; Brain is the coordinator.

There is exactly one orchestration system. The frontend, the Electron shell, and the transport adapter contain none.

---

## 5. Agents

`agents/` provides the agent abstraction and the planning implementation used by Core:

| Module | Role |
| :--- | :--- |
| `agents/planning_agent.py` | `PlanningAgent`: sends goal + context to the LLM via the model router, parses and validates the JSON plan into `ExecutionPlan`/`PlanStep`. The planner never executes anything. |
| `agents/planner.py` | `AgentPlanner`: adapts `PlanningAgent` to the `Planner` protocol consumed by Brain. |
| `agents/agent.py`, `protocols.py`, `types.py` | `Agent` / `AgentHandler` abstractions and errors. |
| `agents/registry.py`, `agents/tools/*`, `agents/server.py` | **Legacy.** A parallel tool registry and a standalone `POST /execute` server on port 8765 (references a `server.ts` bridge that does not exist). Not imported by `app.py`, the transport adapter, or the Electron shell. Left in place; not part of the live architecture. |

Planning rules enforced in the prompt: single JSON object only; `description` / `intent` / `metadata` per step; metadata carries operational arguments only — never security fields (`risk_level`, `destructive`, `approved`), which are assigned authoritatively by skill/tool metadata and the permission policy.

The prompt is a request, not a defense. Everything the model returns passes through one
parse-and-validate function (`_validate_and_build_plan`), and security-bearing fields are
discarded **there** — before Core sees them — because a plan is a proposal about the world,
not an authorization to act on it. The rule, the reasoning, and the residual risks are
specified in [SECURITY.md](SECURITY.md) §1–§3.

---

## 6. Skills vs Tools

Mamba separates **reusable capabilities** from **external actions** across two layers:

- **`skills/` — Skills: one reusable Mamba capability.** A `Skill` (`skills/skill.py`) is a named, described unit (e.g. `ReadFileSkill`, `WebSearchSkill`, `GitHubReadFileSkill`). Skill *task handlers* (`skills/mixed.py`) map planner **intents** (`read_file`, `run_command`, `web_search`, …) to handlers and expose authoritative metadata the permission system trusts: `risk_level`, `destructive`, `irreversible`, `user_sensitive`, `externally_visible`, `tool_name`.
- **`tools/` — Tools: perform external actions.** A `BaseTool` (`tools/tool.py`) validates input and delegates to a `ToolHandler` that touches the outside world: `tools/filesystem`, `tools/terminal`, `tools/desktop`, `tools/system`, `tools/screen` (screenshot + OCR), `tools/web` (Tavily), `tools/browser` (Playwright MCP / Playwright), `tools/github` (GitHub API), `tools/email`, `tools/calendar`, `tools/messaging`, each with `protocols.py` / `types.py` / `errors.py` contracts.

Flow per step: `PlanStep.intent` → mixed executor → skill handler → tool → handler → `Observation`. Skills never bypass tools for external effects; tools never plan.

The full action/risk surface of all **14** registered capabilities is documented per
capability in [SKILLS.md](SKILLS.md).

### 6.1 Cross-Application Interaction (Supported Windows Applications)

Cross-application interaction lives inside the existing desktop capability — it is **not** a separate intelligence system. There is no `DesktopBrain`, `DesktopAgent`, `NotepadBrain`, `ChromeBrain`, or `CrossAppOrchestrator`; the same Brain lifecycle drives it, and adding an application is a **registry entry**, not new execution logic.

Layering (each layer is generic; only the adapter record is application-specific):

| Layer | Module | Responsibility |
| :--- | :--- | :--- |
| Win32 primitives | `tools/desktop/_win32.py` | enumerate/bind windows, foreground, focus reclamation, read a text control, key press, resolve executables |
| Observation probes | `tools/desktop/observation.py` | the smallest reliable "did it happen?" mechanism per application: `TextControlProbe`, `ClipboardCopyProbe`, `WindowStateProbe` |
| Adapter registry | `tools/desktop/apps.py` | one declarative record per application: identity (process/class/title), launch spec, observation probes, supported actions |
| Generic driver | `tools/desktop/driver.py` | `CrossAppDriver`: discover / launch / bind / focus / type / observe, with target checks; `BoundApplicationDriver` scopes it to one app |
| Generic tools | `tools/desktop/cross_app_tools.py` | `LaunchApplication`, `TypeTextInApplication`, `ReadApplicationText`, `InspectApplications` handlers + tools |
| Skills | `skills/desktop.py` | `LaunchApplicationSkill`, `TypeTextInApplicationSkill`, `ReadApplicationTextSkill`, `InspectApplicationsSkill`, plus Notepad-scoped subclasses |
| Compatibility | `tools/desktop/notepad.py`, `notepad_tools.py` | the Notepad surface as a thin preset of the generic layers |

Supported applications (adapters): **Notepad** (launch, type, read back), **Calculator** (launch, observe display via its native Ctrl+C), **File Explorer** (launch at a folder, verify the folder window), **VS Code** (launch, window observation only). Applications that render their content without a Win32 control (Electron/UWP) declare no content probe, and Mamba then reports *executed but not independently verified* instead of claiming success.

Flow for *"Open Notepad and type Hello from Mamba"*:

```
intent launch_application  → resolve "notepad" via the adapter registry (or default when
                             the intent itself names Notepad)
                           → launch the OS binary, bind the window that identifies as Notepad
intent type_text           → Brain pins {app_id, text} ON THE STEP, evaluates permission
                             (MEDIUM → ALLOW), re-verifies the bound window is live and is the
                             ACTIVE window, then types
verification (automatic)   → reads the text back out of the bound window through the adapter's
                             declared probe and passes it to DefaultVerifier as `contains`
→ "Verified: 'Hello from Mamba' is present in the Notepad window"
```

Safety properties enforced by construction:

- **Explicit target binding.** A window is a target only if it positively identifies as a supported application: expected process name, window class, and title pattern, re-verified at the moment of use. A stale handle from an earlier task, a foreign application handle, or an unknown window is refused — never silently replaced by "whatever is focused".
- **Verify before typing.** The bound window must be the current foreground window immediately before the first keystroke; otherwise the action stops with a clear failure. Windows' foreground lock is cleared with the standard `AttachThreadInput`/ALT-activation workaround; the force-switching `SwitchToThisWindow` API is deliberately **not** used.
- **Per-application capability declaration.** Typing requires the adapter to set `supports_text_input`; applications without it (Calculator, VS Code, File Explorer) refuse typing rather than being typed into blindly.
- **Observation, not assumption.** The outcome is read back through the adapter's declared probe. If nothing can observe it, the result is **inconclusive** (or "executed but not independently verified"), never success. Where the live result cannot be verified, the reported result says so.
- **No arbitrary operations.** There is no arbitrary-application launcher, no arbitrary window text reader, and no generic RPA/DSL surface: every operation is scoped to an application that declares itself in the adapter registry. The driver itself never closes anything. Window closing does exist as a **separate desktop tool** (`close_window`, which posts `WM_CLOSE` to a positively identified window) — see [SKILLS.md](SKILLS.md) §3.3 for its risk classification and the honest caveat that it is currently LOW/ALLOW.

Permission metadata (authoritative, from `DESKTOP_OPERATIONS`): launching and reading are `LOW`; typing into an application is `MEDIUM` with `externally_visible=True` and `destructive=False` / `irreversible=False`. Under the existing policy MEDIUM maps to ALLOW, so no second confirmation mechanism is introduced and nothing risky is auto-authorized.

---

## 7. Models & Model Routing

`models/` keeps Core provider-independent:

- **Providers** (`models/providers/`): `NVIDIAModelProvider` (NVIDIA NIM, OpenAI-compatible chat completions), `GroqModelProvider`, `GeminiModelProvider`. Each declares `ModelInfo` (model id, capabilities such as multimodal) and maps provider responses into the shared `ModelResponse` type. HTTP is done with stdlib `urllib` — no provider SDKs.
- **Defaults** (overridable by constructor arg or env):
  - NVIDIA text: `nvidia/nemotron-3-super-120b-a12b`
  - NVIDIA vision: `meta/llama-3.2-11b-vision-instruct` (`NVIDIA_VISION_MODEL`)
  - Groq: `qwen/qwen3.8-27b` (`GROQ_MODEL`)
  - Gemini: `gemini-2.5-flash` (`GEMINI_MODEL` / `GOOGLE_API_KEY` also accepted)
- **Router** (`models/router.py :: DefaultModelRouter`): deterministic, capability-aware policy —
  1. explicit `metadata["provider"]` requirement,
  2. explicit `metadata["model"]` requirement,
  3. explicit `metadata["capability"]` requirement,
  4. multimodal/image compatibility (`request.has_images`),
  5. available providers satisfying the above,
  6. stable provider order as final tie-break.
  
  An unsatisfiable explicit requirement raises `ModelRoutingError` honestly instead of silently substituting another provider. `route_with_reason()` exposes the decision for explainability.
- **Streaming** (`DefaultModelRouter.stream(request, on_text)`): the same candidate policy as `invoke`, plus a first-token gate — no provider switch after any delta has been emitted, cancellation always re-raised. Providers that do not advertise `capabilities["streaming"]` degrade to `invoke()`. Optional surface: `BaseModelProvider.stream()` raises unless the provider implements it. See §23.4.

Voice models are separate: STT `whisper-large-v3-turbo` via Groq (`GROQ_STT_MODEL`), TTS `@cf/deepgram/aura-1` via Cloudflare (`CLOUDFLARE_TTS_MODEL`).

---

## 8. Permissions

`permissions/policy.py :: DefaultPermissionPolicy` is authoritative — the planner/model can never bypass it.

- **Risk → decision mapping:** `LOW` → ALLOW, `MEDIUM` → ALLOW, `HIGH` → ASK, `CRITICAL` → DENY.
- **Metadata escalation:** flags from skill/tool metadata (`destructive`, `irreversible`, `user_sensitive`, `externally_visible`) raise the decision: HIGH/CRITICAL risk with any sensitive flag escalates toward ASK, CRITICAL + destructive/irreversible escalates to DENY. Escalation is **monotonic** — a flag can move a decision toward DENY, never toward ALLOW.
- **Who classifies:** risk comes from the capability's own operation table. A step may only ever *raise* that classification; nothing a plan claims can lower a capability's own risk level, and sensitivity flags are rebuilt from authoritative metadata so a plan cannot clear a flag the capability set.
- **ASK behavior:** Brain pauses the step into `PendingApproval` and returns an `awaiting_approval` observation. The CLI prints `[Confirmation Required]`; the transport adapter emits a `permission_request` WebSocket message; the React UI shows the `SudoPopup` modal. The next user message is matched against approval phrases ("yes", "go ahead", "do it", …) or denial phrases ("no", "cancel", "stop", …), including extended regex variants and replacement forms ("no, instead …", "make that <file>").
- Approval resumes exactly where execution paused (`_resume_pending_approval`), re-running only the remaining steps; denial cancels cleanly. An unrelated new request clears a stale pending approval.
- **Approval is step-scoped and Core-held.** It is recorded against the `PlanStep.id` that Core generated for the paused step, in a session set that is cleared when the user changes the subject, denies, or replaces the action. Approving one destructive step therefore does not authorize a later step or a replanned one — and no metadata field, from a plan or a request, is read as approval.
- **Voice cannot approve.** A pending ASK is always a HIGH-risk action, and a voice-originated "yes" is refused: the user must confirm visually or by typed input. A voice "no" still cancels, because that is the safe direction.

Policy mechanics, the invariants this establishes, and the residual risks it does not close
are specified in [SECURITY.md](SECURITY.md).

---

## 9. Verification

`verification/verifier.py :: DefaultVerifier` checks whether requested outcomes actually occurred. Brain invokes it when a step declares `expected`/`verify` metadata, or automatically for externally-visible intents (`send_email`, `create_event`, `send_message`, … — verified via `provider_verified` receipts).

Supported predicate families: `contains` / `not_contains` (with regex), `pattern`, `exit_code`, `file_exists` / `file_absent`, `content_matches` (path + expected content), `equals`, `provider_verified` receipts, and a non-empty-output check when verification is requested without a concrete expectation. Results are `VERIFIED` / `FAILED` / `INCONCLUSIVE`. A failed verification triggers replanning by default (unless the step opts out), so Mamba retries with new information rather than asserting success.

The distinction that makes this meaningful is three-way: **expected outcome** (what the plan
asked for) ≠ **observed outcome** (what the tool actually returned) ≠ **verified outcome**
(the verifier's verdict over the two). The verifier consumes observations, never the
model's restatement of them, and no field a plan supplies is read as a verdict — a step
cannot declare itself verified, and cannot declare verification skipped. `INCONCLUSIVE` is a
first-class answer: when nothing can observe an effect, Mamba says "executed but not
independently verified" instead of claiming success.

---

## 10. Memory

`memory/` is persistent, local, provider-independent context:

- **Store** (`memory/store.py`, `memory/persistent.py`): SQLite with WAL mode and parameterized queries at `.mamba/memory.db` (override via `MAMBA_MEMORY_DB`).
- **Manager** (`memory/manager.py :: MemoryManager`): `remember` / `retrieve` / `update` / `supersede` / `forget` / `summarize` / `reindex`; content classification, capture heuristics (`should_capture`), secret filtering, and ephemeral-noise filtering.
- **Embeddings** (`memory/embedding.py`): `SentenceTransformerEmbeddingProvider` (`all-MiniLM-L6-v2`) loaded lazily; if unavailable, retrieval degrades to keyword token-overlap with stopword filtering (`memory/stopwords.py`). Embeddings never leave the machine.
- **Types** (`memory/types.py`): `MemoryType` = `user_preference`, `user_fact`, `project_context`, `project_decision`, `task_context`, `knowledge`, `conversation_summary`; `MemoryStatus` = `active`, `superseded`, `deleted`.
- **Supersession:** conflicting updates mark the old entry `superseded` with a `superseded_by` link instead of accumulating contradictions.
- **Transient hygiene:** Brain's `_update_memory` skips executions composed entirely of transient read/inspection intents and never persists screen interpretations.

See [MEMORY.md](MEMORY.md) for details.

---

## 11. Project Understanding

Two cooperating pieces:

- `core/project.py`: deterministic local project discovery — walks up from the working directory for root markers (`pyproject.toml`, `package.json`, `Cargo.toml`, `go.mod`, `.git`, …), detects language/framework, entry points, test layout, and captures `GitState` (branch, staged/unstaged changes, recent commits). Attached to every request as `project_context` metadata.
- `skills/project.py`: the `project_understanding` capability exposing `project_info`, `explain_architecture`, `find_problems`, `relevant_files`, `git_context`. Read-only by contract — it never mutates code or runs git writes.

See [PROJECT_UNDERSTANDING.md](PROJECT_UNDERSTANDING.md).

---

## 12. GitHub Integration

`tools/github/` + `skills/github.py` provide **read-only** repository inspection against the live GitHub REST API: `get_repository`, `read_file`, `list_directory`, `get_issue` / `list_issues`, `get_pull_request` / `list_pull_requests`, `search_code`. Authentication is via `GITHUB_TOKEN` (env or injected credential); unauthenticated use works within public rate limits. Tokens are redacted from error messages and can never be passed as tool arguments. No write operations (no commits, pushes, merges) are implemented.

---

## 13. Web Search vs Browser Automation

These are two different capabilities that are easy to conflate, so they are separated by
name, by code path, and by risk profile.

| | `web` (search) | `browser` (automation) |
| :--- | :--- | :--- |
| Purpose | **Discovery** — find out what is out there | **Interaction** — drive a real page |
| Provider | Tavily HTTP API | Playwright MCP (default) or Playwright directly |
| Transport | one stdlib `urllib` POST, 15 s | JSON-RPC 2.0 over stdio to a subprocess |
| State | stateless | a bound page, kept across steps |
| Risk | LOW (read-only) | MEDIUM, escalating to HIGH for consequential actions |
| Touches Chrome? | **no** | yes |

### 13.1 Web search — `web`

`tools/web/` + `skills/web.py`: live web search via the **Tavily API** (`TavilyProvider`,
`POST https://api.tavily.com/search`), enabled by `TAVILY_API_KEY`. `max_results` is clamped
to 1–10 and the key is read from the environment only — never from a `.env` file and never
from tool arguments. Without the key the `web` capability reports `NOT_CONFIGURED` and Brain
tells the user plainly instead of hallucinating results. API keys are sanitized out of logs
and errors. No browser, no MCP, no Node.js is involved.

### 13.2 Browser automation — `browser`

The chain is explicit, and each layer has one job:

```
Mamba (core/brain.py — plan step, intent e.g. click_element)
  → BrowserSkill              (skills/browser.py — intent → action params)
    → BrowserTool             (tools/browser/tool.py — resolve action, authoritative risk metadata)
      → BrowserSession        (tools/browser/session.py — bind one page, verify it is still the same page)
        → PlaywrightMcpProvider (tools/browser/mcp.py — MCP tool calls, snapshot parsing)
          → McpStdioClient    (JSON-RPC 2.0, newline-delimited, over the subprocess pipes)
            → @playwright/mcp  (npx subprocess — the MCP server)
              → Chrome         (the controlled browser)
```

**Launch.** `npx -y @playwright/mcp@0.0.83 --browser chrome --headless --isolated`, with
`--user-data-dir`, `--viewport-size`, or `--cdp-endpoint` substituted when configured
(`tools/browser/mcp.py:_resolved_command`). Configuration is by environment so the
capability stays provider-independent:

| Variable | Default | Effect |
| :--- | :--- | :--- |
| `MAMBA_BROWSER_PROVIDER` | `playwright-mcp` | `playwright` selects the direct-library provider instead |
| `MAMBA_BROWSER_HEADLESS` | `1` | `0` shows the browser window |
| `MAMBA_BROWSER_ISOLATED` | `1` | throwaway browser profile |
| `MAMBA_BROWSER_CDP_ENDPOINT` | — | attach to an already-running Chrome over CDP (mode `connect`) |
| `MAMBA_BROWSER_USER_DATA_DIR` | — | persistent profile (mutually exclusive with `--isolated`) |
| `MAMBA_BROWSER_PACKAGE` | pinned `0.0.83` | MCP server package override |
| `MAMBA_BROWSER_ACTION_TIMEOUT` | `180` | per-call deadline |

**Protocol.** The client has no MCP framework dependency: it performs the handshake
(`initialize` at protocol version `2025-06-18`, then `notifications/initialized`) and issues
every action as a `tools/call` request — `browser_navigate`, `browser_snapshot`,
`browser_find`, `browser_click`, `browser_type`, `browser_press_key`,
`browser_select_option`, `browser_tabs`, and friends. The process is started lazily on the
first browser action and reused for the session; a process that died is transparently
restarted on the next call.

**Targeting is by element reference, never by coordinates.** The page is read as an
accessibility snapshot (`tools/browser/snapshot.py`), parsed into
`BrowserElement(ref, role, name, url, value, disabled)`, and resolved ref-first with a
role/name/text fallback. Zero matches and ambiguous matches are **refused** rather than
guessed, and the MCP arguments carry only the resolved reference. Scrolling is keyboard
(`PageDown`/`Home`/…) rather than mouse-wheel. The session additionally re-verifies that the
page it is about to act on is still the page it bound to, and refuses drift.

**Risk.** `browser_operation_metadata()` keeps read-only and navigation actions LOW/MEDIUM,
and escalates `click` / `type` / `press_key` / `select_option` to **HIGH** (with
`irreversible` + `externally_visible`) when the action can submit, post, send, purchase,
delete, or change account state — decided from explicit params plus a deliberately
conservative keyword scan of the step description and resolved element name. HIGH routes
through the existing approval flow; there is **no** second browser-specific permission
mechanism, and nothing consequential is auto-authorized.

**Failure modes.** Missing Node.js `npx` is reported as `browser_unavailable` before any
dispatch; a hung or exited MCP process surfaces as an explicit target error naming the
deadline. Neither is probed at startup, so the first browser action is where a broken
environment becomes visible. A live caveat: the client re-checks its deadline *between*
stdout lines, so a server that stops emitting at all can block past the nominal timeout —
known, not yet hardened.

**Known gaps** in this chain (tab activation on the default provider, unreachable
`fill_form`/`close_page`, definitions without intent mappings) are enumerated in
[SKILLS.md](SKILLS.md) §4.

> `agents/tools/browser.py` is a **separate legacy** async-Playwright implementation
> served by the unused `agents/server.py`. It is not on this chain and not reachable from
> `app.py`, the transport adapter, or the desktop shell (§5).

---

## 14. Voice & Text Interfaces

**Text (CLI)** — `app.py`: interactive REPL (`mamba>` prompt), one-shot (`python app.py "<goal>"`), and `voice`/`--voice` switching. Progress milestones (`Understanding…`, `Planning…`, `Executing…`) print inline; `[Confirmation Required]` marks approval pauses.

**Voice (Python)** — `voice/`:
```
Microphone → Groq Whisper STT (whisper-large-v3-turbo)
  → MambaRuntime → voice/normalization.py (strip markdown/code for speech)
  → Cloudflare Workers AI Aura-1 TTS (@cf/deepgram/aura-1) → Speaker
```
`VoiceInterface.voice_loop()`: press Enter to record, Enter to stop; empty audio is reported cleanly. On TTS HTTP 429 the session degrades to text-only audio mode for the rest of the session without failing execution. `process_voice_input()` is the shared entry used by both the CLI loop and the transport adapter.

**Voice (desktop)** — the React app owns capture, activity detection, and playback; the
Python side owns transcription, execution, and synthesis. The microphone is acquired **once**
and the session loops continuously: energy-based VAD segments one utterance, it is sent over
`/live` as `{"type": "audio", "format": "wav", "audio": "<base64>"}`, the server transcribes
and executes through the same `MambaRuntime`, the reply arrives as **one complete** audio
blob (no streaming — the TTS provider does not stream, and none is faked), playback ends, and
the mic re-arms. Speaking over the reply **barge-in**-interrupts playback and starts a new
turn. See [VOICE_INTERFACE.md](VOICE_INTERFACE.md) for thresholds and the state machine.

**Wake word (desktop)** — local, offline keyword spotting for **"hey mamba"**:
`sherpa-onnx` streaming Zipformer KWS (Apache-2.0, int8 models pinned in
`public/wake/kws/`). The spotter runs in the **Electron main process** (`electron/wakeKws.cjs`)
because the WASM build requires Emscripten NODERAWFS and cannot initialize in a
`nodeIntegration: false` renderer; the Orb renderer captures 16 kHz PCM and streams it over
IPC, and detections flow back through the existing activation path. Nothing is recorded,
persisted, or sent anywhere else. It is a working **prototype**, not a production wake
system: exactly one phrase, one English model, and diagnostic logging still in the path.

See [VOICE_INTERFACE.md](VOICE_INTERFACE.md).

---

## 15. Electron Desktop Shell

`electron/` is the desktop shell: **lifecycle + presentation boundary** for the desktop app. It never plans, routes models, executes tools, or interprets user goals.

```
electron/main.cjs ............ app entry: single-instance lock, window creation,
                               IPC wiring, boot sequence, clean teardown
electron/backendManager.cjs .. owns the Python backend process lifecycle
electron/frontendManager.cjs . resolves the React frontend URL
electron/staticServer.cjs .... serves dist/ + proxies /api + tunnels /live WS
electron/lifecycleManager.cjs  DORMANT/ACTIVE/IDLE state machine
electron/trayManager.cjs ..... system tray icon + menu
electron/hotkeyManager.cjs ... global Ctrl+Space activation
electron/preload.cjs ......... context-isolated window.mambaDesktop bridge
electron/wakeKws.cjs ......... wake-word spotter service (main process)
electron/sherpa/ ............. pinned sherpa-onnx WASM + KWS glue (see VERSIONS.md)
electron/run.cjs .............. launcher (clears ELECTRON_RUN_AS_NODE)
```

**Boot sequence** (`bootApp`): create hidden main window → create tray → register global hotkey → resolve frontend URL (existing Vite dev server → spawned Vite → built `dist/` via StaticServer) → create floating Orb window (`?mode=orb`) → initialize LifecycleManager in **DORMANT**. The main window intercepts close → hides instead of quitting; quit happens only via tray "Quit Mamba" or app quit, which tears down hotkey, tray, lifecycle, frontend, and any owned backend.

---

## 16. Floating Mamba Orb

The Orb is a **presentation layer, not an AI agent**:

- A 220×220 frameless, transparent, always-on-top, `skipTaskbar` `BrowserWindow` anchored bottom-right, loading the same React bundle with `?mode=orb` → `src/FloatingOrb.tsx`.
- Visuals: Three.js particle + haze shaders (`src/orb/shaders/`: particle, haze, plus the analyser) rendered through `MambaPresence`.
- **States** (exact): `idle`, `listening`, `thinking`, `executing`, `verifying`, `speaking`, `permission`, `error`. `executing` and `verifying` are **presentation-only phases** derived in the renderer from the `progress` milestones the transport already sends; they are not new transport vocabulary. `LiveState`, the `mamba:state` IPC vocabulary and the lifecycle logic are unchanged — the shell still pushes `mamba:state` over IPC, the main app reports its `LiveState` via `window.mambaDesktop.reportState()`, and the lifecycle manager maps shell states (STARTING→thinking, DORMANT/IDLE→idle).
- **Interaction:** clicking the orb's central hit-target sends `mamba:activate` → `LifecycleManager.requestActivation("orb-click")` (starts backend if dormant; toggles the main window if active). The orb window is draggable via `-webkit-app-region: drag`; its close is intercepted → hide, never quit.
- The orb stays alive in DORMANT — it is the persistent visual anchor while the backend is off, and it is the renderer that hosts the wake listener precisely because it is the only renderer alive then (`src/FloatingOrb.tsx` → `src/wake/controller.ts`). Its close is intercepted → hide, never quit. A wake detection is a *presentation-layer event*: it asks the shell to activate, exactly like a click; the Orb still plans and executes nothing.

---

## 17. Active / Dormant Lifecycle

`electron/lifecycleManager.cjs` implements the explicit shell state machine:

```
DORMANT ──activation──► STARTING ──backend healthy──► ACTIVE
  ▲                                                  │ inactivity 10 min
  │                                                  ▼ (no task/permission/voice)
  └──────── SHUTTING_DOWN ◄── IDLE ◄────────────────┘
        (backend stopped)   │ 5-min grace
                            └──activity──► ACTIVE
```

- **DORMANT** (startup default): no Python backend running. Orb visible, main window hidden, tray + hotkey armed.
- **STARTING**: `BackendManager.start()` spawns `python app.py --server`, polls `/health` until ready (or reuses an already-running backend it doesn't own), then loads the frontend URL into the main window.
- **ACTIVE**: window shown/focused; 10-minute idle timer (`MAMBA_IDLE_TIMEOUT_MS`, default 10 min).
- **IDLE**: after inactivity; backend still alive; 5-minute shutdown grace (`MAMBA_IDLE_SHUTDOWN_TIMEOUT_MS`). Any activity returns to ACTIVE.
- **SHUTTING_DOWN → DORMANT**: hides window, stops the owned backend, waits for the next activation.

**Task protection:** `isBusy()` = in-flight tasks (`mamba:report-task-state`) OR awaiting permission (`mamba:report-awaiting-permission`) OR voice active. The preload script observes `/live` WebSocket traffic (user transcriptions, `thinking`/`speaking` statuses, `permission_request`, `turnComplete`) and reports these automatically, so idle shutdown never kills a running task or strands a permission prompt. Second-instance launches route to `requestActivation("second-instance")` instead of starting a duplicate.

---

## 18. Tray & Global Hotkey

- **Tray** (`trayManager.cjs`): notification-area icon (`electron/assets/icon.png`) with menu **Show Mamba** → `requestActivation("tray-show")`, **Hide Mamba** → hide window, **Quit Mamba** → full teardown. Available in every lifecycle state.
- **Global hotkey** (`hotkeyManager.cjs`): `CommandOrControl+Space` registered via `globalShortcut`; on press → `requestActivation("global-hotkey")`. It only brings Mamba forward — it never executes tasks or touches other applications. Registration failure (key reserved by the OS) is logged, not fatal.

---

## 19. Backend Lifecycle & Ownership

`electron/backendManager.cjs` manages `python app.py --server` on `127.0.0.1:8000` (`MAMBA_PORT`):

1. **Reuse:** if `/health` already responds, attach without owning (`owned=false`).
2. **Spawn:** otherwise launch via workspace `.venv` Python (or `PYTHON_PATH`, or system `python`), poll `/health` up to 40 s, surface recent logs on failure.
3. **Stop:** terminates **only** the process it spawned (`taskkill /T /F` on Windows, `SIGTERM` elsewhere); externally-started backends are left untouched.

The Python backend itself is stateless across restarts except for `.mamba/` on disk (SQLite memory, settings, reminders) — so dormant shutdown loses nothing durable.

---

## 20. Frontend / React Boundary

`src/` is the **interface layer**. It renders, captures input, and forwards everything to Core over the transport — it contains no planner, no router, no tool execution, no permission logic of its own:

- `MambaApp.tsx`: main chat/voice UI. `MambaAudioSession` (`src/audio.ts`) opens `WebSocket(<origin>/live)`, holds the microphone for a **continuous** session, performs client-side energy VAD and barge-in detection in the same module (thresholds at `src/audio.ts:40-58`, WAV framing in `src/audio/wav.ts`), streams utterances as `audio` messages, sends `text` messages, and handles `transcription` / `progress` / `status` / `permission_request` / `audio` / `turnComplete`. A permission decision is sent back as an ordinary `{type: "text", text: "yes" | "no"}` message — a click on the popup, i.e. typed approval, never a spoken word. Sub-panels: `TranscriptPanel` (the rail — the whole thread, provisional streamed text included), `ThreadEntry` (one rendered entry, shared by rail and stage), `SudoPopup` (approval modal), `BrowserAgent` (in-app browser view), settings (`settingsStore.ts`, synced to `/api/settings`). The stage/rail split has one source of truth: the stage shows only the last authoritative answer, never provisional deltas.
- Wake word (`src/wake/`): `controller.ts` (arming, re-arming, sensitivity) over a swappable `WakeEngine` interface. The active engine is `sherpaOnnxEngine.ts` — renderer-side mic capture streaming PCM to the Electron main process, which runs the spotter (§14, [VOICE_INTERFACE.md](VOICE_INTERFACE.md)). `webSpeechEngine.ts` is the retired stopgap kept only as a compatibility shim; `wakeWord.ts` is its legacy wrapper and is not what runs.
- `FloatingOrb.tsx` + `src/orb/`: the orb presentation described in §16, and the host that arms the wake listener while the shell is DORMANT.
- `preload.cjs` exposes the minimal `window.mambaDesktop` bridge (lifecycle state, activity/task/permission/voice reporting, state sync, activate/toggle) with `contextIsolation` on and no Node.js internals leaked. It also passively observes `/live` WebSocket messages to feed the lifecycle manager.
- **Same-origin transport:** in dev, Vite proxies `/api` → `127.0.0.1:8000` and `/live` → `ws://127.0.0.1:8000` (`vite.config.ts`); in production, `staticServer.cjs` serves `dist/` and proxies/tunnels the same paths to the Python backend. The React code only ever talks to its own origin.

---

## 21. API / Transport Boundary

`api/server.py` is a **transport adapter, not a second brain** — its module docstring states this explicitly and the implementation honors it: every handler forwards to `MambaRuntime` (and optionally `VoiceInterface`) and formats the result.

| Endpoint | Purpose |
| :--- | :--- |
| `GET /health` | Liveness probe (`{"status": "ok", "runtime": "mamba"}`); used by `BackendManager` |
| `POST /api/chat` | `{"input", "metadata?"}` → `ChatResponse{execution_id, status, output, error, awaiting_approval, reason}` |
| `POST /api/voice` | multipart audio upload → `VoiceInterface.process_voice_input(..., speak_response=False)` → prompt + result |
| `WS /live` | bidirectional. **In:** `{"type": "text", "text"}`, `{"type": "audio", "format": "wav", "audio": "<base64 WAV>"}` (RIFF/WAVE framing validated), `{"type": "video"}` (acknowledged, not processed), `{"type": "cancel"}` (cancels the in-flight turn; a no-op when none is running). **Out:** `transcription` (role `user`/`model`, the model frame being the **authoritative** turn text), `delta` (provisional streamed text, `/live` only, coalesced), `progress` (milestones), `status` (`connected`/`thinking`/`permission`/`cancelling`/`listening`), `permission_request`, `audio` (complete synthesized reply, `format: "mp3"`), `cancelled` (terminal — a cancelled turn emits no model frame and no `turnComplete`), `turnComplete`, `error`. There is **no** inbound `permission_response` type: an approval is delivered as an ordinary `text` message (`"yes"`/`"no"`), so the pause is resolved by the same intake path any other input uses |
| `GET/POST /api/settings` | UI preferences ↔ `.mamba/settings.json` |
| `GET/POST /api/reminders` | reminders ↔ `.mamba/reminders.json` |

CORS is permissive (loopback desktop use). Approval pauses surface as `awaiting_approval: true` / `permission_request` so any client can render its own confirmation UI.

---

## 22. Capability Status

Labels: **IMPLEMENTED** (wired end-to-end, real effect, tested) · **IMPLEMENTED-HARDENING**
(implemented plus explicit trust-boundary/safety work) · **PLANNED** (designed, no code path
executes it) · **DEFERRED** (deliberately out of scope). A capability is not called complete
while a known blocking limitation applies to it.

### ✅ IMPLEMENTED

- **Core lifecycle** — intake, referent resolution, context, memory retrieval, capability-grounded planning, permission-gated execution, observation, verification, replanning, memory update (§3).
- **14 capabilities** in the registry: filesystem, terminal, desktop, **browser**, system, screen, web, github, memory, email, calendar, messaging, analyze, project_understanding. Per-capability actions, gates, and gaps: [SKILLS.md](SKILLS.md).
- **Trust boundary** — model output cannot grant permission, lower a capability's own risk classification, clear a sensitivity flag, or declare an outcome verified; approval is held by Core and bound to a Core-generated step id. Covered by 13 dedicated regression tests. **IMPLEMENTED-HARDENING** (§8, [SECURITY.md](SECURITY.md)).
- **Browser automation** — `Mamba → Browser Skill → Browser Tool → MCP Adapter → Playwright MCP → Chrome`, element-reference targeting with no coordinate clicks, headless by default, consequential actions escalated to ASK. **IMPLEMENTED**, with the caveats in §13.2 and [SKILLS.md](SKILLS.md) §4.
- **Cross-application interaction** — generic adapter registry (Notepad, Calculator, File Explorer, VS Code): launch, bind the exact window, focus, type where the adapter declares a text field, and observe through the adapter's declared probe (§6.1).
- **Permissions** — LOW/MEDIUM→ALLOW, HIGH→ASK, CRITICAL→DENY, monotonic metadata escalation, phrase-based approval/denial/resume (§8).
- **Verification** — predicate checks over real observations, `INCONCLUSIVE` as a first-class verdict, replan-on-failure (§9).
- **Memory** — SQLite WAL, local embeddings + keyword fallback, supersession, transient filtering, secret filtering (§10).
- **Project understanding** — automatic local discovery on every request + git context, read-only (§11).
- **GitHub read integration** — repos, files, issues, PRs, code search via the REST API; no write path exists (§12).
- **Web search** — Tavily, key-gated, honest unavailable messaging (§13.1).
- **Voice** — Groq Whisper STT, Cloudflare Aura-1 TTS, normalization, TTS-429 degraded mode, CLI loop + `/live` WebSocket path (§14).
- **Continuous conversation + VAD + barge-in** (desktop) — one mic acquisition, energy-threshold segmentation, silence/timeout endpoints, interruption during playback, self-ending idle session (§14).
- **Wake word (prototype)** — offline sherpa-onnx KWS in the Electron main process, single phrase "hey mamba", default-on, released Orb host (§14, §16).
- **Transport adapter** — FastAPI + WebSocket, zero-intelligence contract (§21).
- **Electron shell** — dormant-first lifecycle, backend ownership, tray, global hotkey, floating Orb, preload bridge (§15–§19).
- **Multi-turn continuity** — pending approvals, active-entity tracking, prior-turn context.

### ⚠️ IMPLEMENTED with a declared limitation

- **Email / Calendar / Messaging** — intents, skills, tools, permission gates, and verification receipts are fully wired, but the providers are **simulated** (`SimulatedEmailProvider`, `SimulatedCalendarProvider`, `SimulatedMessagingProvider`, sample data). They exercise the complete UX flow; **no real mailbox, calendar, or chat service is connected**, so no real message can be sent today. Real integration: **PLANNED**.
- **Terminal** — direct-exec only; shell interpreters and shell-mode flags are rejected (§3.2 in [SKILLS.md](SKILLS.md)). No interpreted pipelines.
- **Screen visual understanding** — capture and OCR work locally; `visual_understanding` needs a configured vision-capable provider and OCR needs the Tesseract engine. Absent either, the action reports unavailable.
- **Browser environment detection** — the `browser` capability is registered as configured without probing, so a missing `npx` surfaces on the first action rather than at startup (§13.2). Startup health probe: **PLANNED**.
- **Tab activation** — binding verifies a tab's identity but cannot make it frontmost on the default MCP provider; needs `MAMBA_BROWSER_PROVIDER=playwright` ([SKILLS.md](SKILLS.md) §4).
- **Wake word** — exactly one phrase, one English spotting model, diagnostic instrumentation still present; accuracy and DORMANT power draw are not established (§14).
- **Reminders** — the transport persists reminder lists (`.mamba/reminders.json`) and the React UI manages them, but there is no due-time scheduler that fires them. (Root-level `server_reminders.ts`/`server_path.ts` are unreferenced legacy.)
- **Voice approval** — deliberate asymmetry: a spoken "yes" is refused for pending HIGH-risk actions, a spoken "no" is honoured (§8, [VOICE_INTERFACE.md](VOICE_INTERFACE.md)).

### 🔮 PLANNED (designed, not implemented — do not describe as present)

- Real email / calendar / messaging provider integrations.
- Reminder scheduling and delivery engine.
- Fail-closed risk default for handlers that declare no metadata, and content-provenance labeling for observations fed back into planning ([SECURITY.md](SECURITY.md) §9).
- Browser provider health probe and MCP call-timeout hardening.
- Multi-phrase / system-level wake word with verified accuracy and power budget.

### 🚫 DEFERRED (out of scope by design)

- Packaged desktop distribution (installer, auto-update, code signing).
- Mobile companion / remote access beyond loopback — which also means no authentication on the transport (§21, [SECURITY.md](SECURITY.md) §8).
- Multi-user / team workspaces. Mamba is strictly single-user, local-first.
- Sandboxed tool execution (container / restricted token / job object). The permission model gates *risk*, not *privilege*.
- Removing the legacy `agents/registry.py`, `agents/tools/*`, `agents/server.py` package and the unreferenced root TypeScript/`web_fetch` leftovers. Left in place; documented as unused.

---

## 23. Explicit Architecture Rules

### 23.1 Layer responsibilities

These are invariants. Code and documentation must not contradict them:

1. **Mamba Core is the intelligence/execution system.** Planning, permissions, skills, tools, verification, memory, and model routing live in `core/`, `agents/`, `models/`, `skills/`, `tools/`, `permissions/`, `verification/`, `memory/`.
2. **Electron is the desktop shell / presentation / lifecycle boundary.** Window, tray, hotkey, orb hosting, wake-word hosting, backend process ownership, idle lifecycle. Nothing else.
3. **React is the interface layer.** Renders state, captures input, forwards to transport. No orchestration, no planning, no tool calls.
4. **`api/server.py` is a transport adapter, not a second brain.** Forward + format only.
5. **The Orb is a presentation layer, not an AI agent.** It visualizes state, activates the session, and hosts the wake listener; it plans nothing.
6. **Skills provide reusable capabilities.** Named, described, intent-mapped units with authoritative risk metadata.
7. **Tools perform external actions.** Validated handlers that touch the outside world.
8. **Permissions govern risky actions.** The policy is authoritative over the model.
9. **Verification checks whether requested outcomes actually occurred.** Predicates over observations, not model self-assessment.
10. **Memory provides persistent context.** Durable facts with supersession; transient reads never persist.
11. **Models are intelligence providers and must remain provider-independent.** Router + provider abstraction; no provider SDKs; stdlib HTTP.
12. **Do not create a second orchestration system** in the frontend or Electron shell. There is one execution loop: Brain's. Likewise there is **one** permission system, **one** browser automation chain, and **one** capability registry — cross-application and browser control extend the existing desktop/browser capabilities through registries and adapters rather than new top-level layers.

### 23.2 Trust-boundary invariants

The security half of the invariant set — model output is untrusted with respect to
security authority, claims can only escalate, approval is step-scoped and Core-held,
expected ≠ observed ≠ verified, voice cannot approve, unconfigured capabilities fail
honestly, unregistered intents do not execute, credentials are never model arguments, and
execution is bounded — is enumerated as **15 numbered invariants** with their enforcement
points in [SECURITY.md](SECURITY.md) §3. Rules 8, 9, and 12 above are where those meet the
layer boundaries.

### 23.3 Request-latency controls

Phase 3 added three conservative, measurement-driven latency controls. None of them
creates a second decision-maker: the **planner is not the authority and the fast path is
also not the authority** — permissions, verification, cancellation, execution state and
memory capture all still run on every path.

1. **Project-discovery relevance gate** (`core/quickpath.py::is_clearly_project_irrelevant`,
   consumed by `Brain._project_context_needed`). Uncached `discover_project()` (an FS walk
   plus four git subprocesses, ~458–607 ms on this machine) was paid by *every* request.
   The gate skips it only for a request that is **confidently** project-irrelevant
   (deterministic arithmetic, time queries, greeting-shaped, memory-write). Anything
   uncertain, project/coding/tool/browser-relevant, or any request with an active
   repository retains discovery. Safe-by-default: the classifier returns `False` (keep
   discovery) whenever it is not sure.

2. **Arithmetic-only fast path** (`core/quickpath.py::build_fast_plan`, dispatched in
   `Brain._run_impl` before context assembly and executed through the normal
   `Brain._execution_loop(seed_plan=…)` → `_execute_step()` pipeline). It resolves **pure
   additive/multiplicative arithmetic** deterministically with a safe `ast` evaluator (no
   `eval`; exponentiation excluded) and answers without a planning model call. A broader
   simple-Q&A fast path (short single-verb requests) was tried and **removed** — it
   answered goals locally that other subsystems assert must reach the planner, becoming a
   de-facto "second brain" and regressing 6 tests. The fast path still honours the ambient
   cancellation token and the permission evaluation.

3. **Provider routing / bounded fallback** (`models/router.py::DefaultModelRouter.invoke`).
   Candidate providers are tried serially. Cancellation is re-raised immediately and never
   triggers a fallback; a **permanent** failure (bad key, unsupported/absent model, invalid
   request — HTTP 400/401/403/404/405/409/413/415/422 or matching phrases) stops the loop
   at once; a **transient** failure (HTTP 408/425/429/5xx or timeout/rate-limit/network)
   advances to the next candidate, bounded by `MAMBA_PROVIDER_TOTAL_TIMEOUT`. The original
   failure is always surfaced when nothing succeeds. `agents/planning_agent.py` re-raises
   `MambaCancelledError` at both of its model-invocation sites so a cancel is never
   recorded as a planning failure or retried.

**Environment variables:**

| Var | Default | Effect |
|---|---|---|
| `MAMBA_TIMING` | off | When truthy, `core/timing.py` accumulates per-phase durations (ms) into `request.metadata["latency_ms"]`, surfaced on `ExecutionResult.metadata["latency_ms"]`. Durations only — no user content, credentials, page or memory text. No-op (zero cost) when disabled. |
| `MAMBA_PROVIDER_TOTAL_TIMEOUT` | `90` | Wall-clock budget (seconds) for the serial multi-provider fallback, so a dead primary cannot stack several socket timeouts into a multi-minute stall. |

**Measured effect** (deterministic bench `tests/bench_phase3_latency.py`, ~480 ms discovery):
arithmetic 1048 → **5.9 ms (−99.4%)**, memory-write 1049 → **567 ms (−45.9%)**; every
project-relevant or uncertain class is unchanged by design. Replanning stayed at one cycle
across all classes, so the planning-cycle cap (10) was left untouched, and global
`discover_project()` caching was **not** added (forbidden by the no-global-caching rule and
stale-context risk). See `docs/superpowers/plans/2026-10-03-phase3-latency-optimization.md`
for the full before/after table.

---

### 23.4 Streaming (Phase 4)

Phase 4 is about **perceived speed**: the answer text arrives incrementally while the turn is
still running. It is explicitly **not** a latency change (Phase 3 owns that) and it adds no new
decision-maker.

**One rule governs everything below: streamed text is provisional.** Deltas are presentation
only. `ExecutionResult` / `ModelResponse` remain the sole authority, and the transport's final
model frame — not the last delta — is what the UI renders as the answer.

The chain, with exactly one attach point per layer:

1. **Providers** (`models/providers/gemini.py`, `groq.py`, `nvidia.py`) implement
   `stream(request, on_text) -> ModelResponse`, declared as an optional surface on
   `BaseModelProvider` (`models/provider.py`) which raises by default. A streaming provider
   **must return the complete response**; deltas never replace it. Capability is advertised in
   the open `ModelInfo.capabilities` dict as `streaming` (checked by truthiness, never
   membership), so no type was widened. Groq/NVIDIA parse `text/event-stream` with the shared
   stdlib parser `models/providers/_openai_sse.py::parse_sse_delta`; NVIDIA additionally
   collects `reasoning_content` without ever emitting it, matching `invoke()` so a
   reasoning-only model does not lose its answer.
2. **Router** (`models/router.py::DefaultModelRouter.stream`) reuses the Phase 3 candidate
   ordering and `_classify_transient`, adding a **first-token gate**: a failure before any
   delta may fall back to the next provider, a failure after the first delta is re-raised
   (switching providers mid-answer would splice two different completions), and cancellation
   is always re-raised, never a fallback. Providers without the capability degrade to
   `invoke()` and emit the complete text as a single delta — honest, not fake streaming.
3. **Core** (`core/streaming.py`) carries the sink the same way `core/cancellation.py` carries
   the token: a dependency-free ambient `ContextVar` set by `Brain.run(stream_sink=…)`,
   forwarded by `MambaRuntime.run`, and read by the one place that produces answer prose —
   `skills/analyze.py`. `_run`/`_run_impl`/`_execution_loop`/`_execute_step` are untouched, and
   no other skill was made streamable.
4. **Transport** (`api/server.py`, `/live` only — the existing Mamba UI path; `/api/chat` is
   byte-identical) coalesces deltas into `{"type":"delta","text":…}` frames (first delta
   flushes immediately, later ones over `MAMBA_STREAM_COALESCE_MS`) via
   `run_coroutine_threadsafe`, because the whole core runs synchronously on the single
   `mamba-worker` thread and a generator cannot cross that boundary. Before the authoritative
   frame the pending flush is awaited, so frame order is deterministic: deltas → model frame →
   `turnComplete`. A cancelled turn sends `{"type":"cancelled"}` and **no** model frame.
5. **UI** (`src/audio.ts`, `src/MambaApp.tsx`, `src/TranscriptPanel.tsx`, `src/Composer.tsx`)
   renders one provisional bubble per turn, rAF-coalesced, replaced wholesale by the model
   frame. `delta` frames are only forwarded while a turn is in flight (opened by the server's
   `thinking` status, closed by `turnComplete`/`cancelled`/`error`), and a turn that ends
   without authoritative text **discards** its provisional bubble. The frontend never marks a
   turn approved, verified, successful or completed.

**Safety invariants kept:** streaming bypasses no permission check (an approval prompt still
ends the turn pending visual confirmation), no verification, no target binding, no cancellation
and no execution-state transition. Partial output is never persisted to memory as a completed
answer — only the final `ExecutionResult` is.

**Environment variables (added by Phase 4):**

| Var | Default | Effect |
|---|---|---|
| `MAMBA_STREAM_COALESCE_MS` | `40` | Minimum interval between `/live` delta frames. `0` flushes every delta; larger values trade granularity for fewer socket writes. |
| `MAMBA_REAL_PROVIDER_TESTS` | off | Opt-in switch for `tests/test_phase4_real_provider.py`, which makes real (paid) provider calls. Never runs with the normal suite. |

**Real-provider finding (2026-10-04):** against the live Gemini endpoint the answer arrived as
a **single** chunk while still satisfying `"".join(deltas) == response.content`. Chunk
granularity is provider-controlled, so the paid test asserts parity and "at least one delta"
only; multi-delta behaviour is asserted deterministically by the SSE/chunk unit tests. The
configured Groq key returned HTTP 401, so the Groq path remains unvalidated against the live API.

---

## Appendix A — Repository Map

```
app.py ..................... entry point: CLI / voice / --server, create_runtime()
api/ ....................... FastAPI transport adapter (server.py)
core/ ...................... runtime.py (boundary), brain.py (lifecycle),
                             capabilities.py (14-capability registry),
                             project.py (discovery), quickpath.py (shared
                             latency classifier + fast path + safe arithmetic),
                             timing.py (env-gated latency spans),
                             streaming.py (ambient provisional-delta sink),
                             context/state/types
agents/ .................... planning_agent.py, planner.py (live);
                             registry.py, tools/*, server.py (legacy, unused)
models/ .................... router.py (invoke + stream/first-token gate),
                             providers/ (nvidia, groq, gemini + shared
                             _openai_sse.py delta parser)
skills/ .................... capability skills + mixed task executor (intent routing)
tools/ ..................... external-action handlers (filesystem, terminal,
                             desktop incl. cross-app adapters, system, screen, web,
                             browser incl. MCP stdio client, github, email,
                             calendar, messaging)
permissions/ ................ DefaultPermissionPolicy
verification/ .............. DefaultVerifier
memory/ .................... SQLite store, manager, embeddings, retrieval
voice/ ..................... VoiceInterface, Groq STT, Cloudflare TTS, audio, normalization
tasks/ ..................... TaskExecutor / TaskHandler contracts
electron/ .................. desktop shell (main, backend/frontend/lifecycle/
                             tray/hotkey managers, preload, static server,
                             wakeKws.cjs + sherpa/ runtime)
src/ ....................... React UI (MambaApp, FloatingOrb, orb shaders, audio WS
                             session with VAD + barge-in, provisional streaming
                             transcript + turn cancel, wake/ engines + controller,
                             settings)
public/wake/ ............... pinned offline KWS models + VERSIONS.md (Apache-2.0)
docs/ ...................... ARCHITECTURE, SECURITY, SKILLS, MEMORY,
                             VOICE_INTERFACE, PROJECT_UNDERSTANDING
tests/ ..................... Observed on 2026-10-04 (incl. Phase 3 latency and
                             Phase 4 streaming/transport suites): 331 passed,
                             5 failing, 11 skipped. The 5 failures are
                             pre-existing browser-capability cases that need a real
                             MCP/Chrome environment (independently re-confirmed as
                             failing at the committed HEAD source, not a Phase 3
                             regression). Real-desktop cases
                             opt in via MAMBA_REAL_DESKTOP_TESTS=1; real (paid)
                             provider streaming via MAMBA_REAL_PROVIDER_TESTS=1
.mamba/ .................... runtime data (memory.db, settings.json,
                             reminders.json) — created at runtime, gitignored
```
