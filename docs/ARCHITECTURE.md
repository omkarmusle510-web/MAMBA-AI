# Mamba AI Architecture Specification

> **Mamba** is a personal AI operating layer between the user and digital tools.

This document describes the system **as implemented**. Every claim here is grounded in the repository source. Where a capability is incomplete or only planned, it is labeled as such — see §22.

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

---

## 6. Skills vs Tools

Mamba separates **reusable capabilities** from **external actions** across two layers:

- **`skills/` — Skills: one reusable Mamba capability.** A `Skill` (`skills/skill.py`) is a named, described unit (e.g. `ReadFileSkill`, `WebSearchSkill`, `GitHubReadFileSkill`). Skill *task handlers* (`skills/mixed.py`) map planner **intents** (`read_file`, `run_command`, `web_search`, …) to handlers and expose authoritative metadata the permission system trusts: `risk_level`, `destructive`, `irreversible`, `user_sensitive`, `externally_visible`, `tool_name`.
- **`tools/` — Tools: perform external actions.** A `BaseTool` (`tools/tool.py`) validates input and delegates to a `ToolHandler` that touches the outside world: `tools/filesystem`, `tools/terminal`, `tools/desktop`, `tools/system`, `tools/screen` (screenshot + OCR), `tools/web` (Tavily), `tools/github` (GitHub API), `tools/email`, `tools/calendar`, `tools/messaging`, each with `protocols.py` / `types.py` / `errors.py` contracts.

Flow per step: `PlanStep.intent` → mixed executor → skill handler → tool → handler → `Observation`. Skills never bypass tools for external effects; tools never plan.

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

Voice models are separate: STT `whisper-large-v3-turbo` via Groq (`GROQ_STT_MODEL`), TTS `@cf/deepgram/aura-1` via Cloudflare (`CLOUDFLARE_TTS_MODEL`).

---

## 8. Permissions

`permissions/policy.py :: DefaultPermissionPolicy` is authoritative — the planner/model can never bypass it.

- **Risk → decision mapping:** `LOW` → ALLOW, `MEDIUM` → ALLOW, `HIGH` → ASK, `CRITICAL` → DENY.
- **Metadata escalation:** flags from skill/tool metadata (`destructive`, `irreversible`, `user_sensitive`, `externally_visible`) raise the decision: HIGH/CRITICAL risk with any sensitive flag escalates toward ASK, CRITICAL + destructive/irreversible escalates to DENY.
- **ASK behavior:** Brain pauses the step into `PendingApproval` and returns an `awaiting_approval` observation. The CLI prints `[Confirmation Required]`; the transport adapter emits a `permission_request` WebSocket message; the React UI shows the `SudoPopup` modal. The next user message is matched against approval phrases ("yes", "go ahead", "do it", …) or denial phrases ("no", "cancel", "stop", …), including extended regex variants and replacement forms ("no, instead …", "make that <file>").
- Approval resumes exactly where execution paused (`_resume_pending_approval`), re-running only the remaining steps; denial cancels cleanly. An unrelated new request clears a stale pending approval.

---

## 9. Verification

`verification/verifier.py :: DefaultVerifier` checks whether requested outcomes actually occurred. Brain invokes it when a step declares `expected`/`verify` metadata, or automatically for externally-visible intents (`send_email`, `create_event`, `send_message`, … — verified via `provider_verified` receipts).

Supported predicate families: `contains` / `not_contains` (with regex), `exit_code`, `file_exists` / `file_absent`, `content_matches` (path + expected content), and provider receipts. Results are `VERIFIED` / `FAILED` / `INCONCLUSIVE`. A failed verification triggers replanning by default (unless the step opts out), so Mamba retries with new information rather than asserting success.

---

## 10. Memory

`memory/` is persistent, local, provider-independent context:

- **Store** (`memory/store.py`, `memory/persistent.py`): SQLite with WAL mode and parameterized queries at `.mamba/memory.db` (override via `MAMBA_DB_PATH`).
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

## 13. Web / Search Capabilities

`tools/web/` + `skills/web.py`: live web search via the **Tavily API** (`TavilyProvider`, `https://api.tavily.com/search`), enabled by `TAVILY_API_KEY`. Without the key the `web` capability reports `NOT_CONFIGURED` and Brain tells the user plainly instead of hallucinating results. API keys are sanitized out of logs and errors.

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

**Voice (desktop)** — the React app streams microphone PCM over the `/live` WebSocket as `{"type": "audio"}` messages; the transport adapter transcribes via the server-side STT provider and executes through the same `MambaRuntime`. The browser tab also offers a Web Speech API wake-word detector ("hey mamba") and a text-chat fallback panel.

See [VOICE_INTERFACE.md](VOICE_INTERFACE.md).

---

## 15. Electron Desktop Shell

`electron/` is the desktop shell: **lifecycle + presentation boundary** for the desktop app. It never plans, routes models, executes tools, or interprets user goals.

```
electron/main.cjs ......... app entry: single-instance lock, window creation,
                            IPC wiring, boot sequence, clean teardown
electron/backendManager.cjs  owns the Python backend process lifecycle
electron/frontendManager.cjs resolves the React frontend URL
electron/staticServer.cjs ... serves dist/ + proxies /api + tunnels /live WS
electron/lifecycleManager.cjs  DORMANT/ACTIVE/IDLE state machine
electron/trayManager.cjs .... system tray icon + menu
electron/hotkeyManager.cjs .. global Ctrl+Space activation
electron/preload.cjs ........ context-isolated window.mambaDesktop bridge
electron/run.cjs ............ launcher (clears ELECTRON_RUN_AS_NODE)
```

**Boot sequence** (`bootApp`): create hidden main window → create tray → register global hotkey → resolve frontend URL (existing Vite dev server → spawned Vite → built `dist/` via StaticServer) → create floating Orb window (`?mode=orb`) → initialize LifecycleManager in **DORMANT**. The main window intercepts close → hides instead of quitting; quit happens only via tray "Quit Mamba" or app quit, which tears down hotkey, tray, lifecycle, frontend, and any owned backend.

---

## 16. Floating Mamba Orb

The Orb is a **presentation layer, not an AI agent**:

- A 220×220 frameless, transparent, always-on-top, `skipTaskbar` `BrowserWindow` anchored bottom-right, loading the same React bundle with `?mode=orb` → `src/FloatingOrb.tsx`.
- Visuals: Three.js shader orb (`src/orb/`: sphere/particle/backdrop shaders, analyser) rendered through `MambaPresence`.
- **States** (exact): `idle`, `listening`, `thinking`, `speaking`, `permission`, `error`. The shell pushes `mamba:state` over IPC; the main app reports its `LiveState` via `window.mambaDesktop.reportState()`, and the lifecycle manager maps shell states (STARTING→thinking, DORMANT/IDLE→idle).
- **Interaction:** clicking the orb's central hit-target sends `mamba:activate` → `LifecycleManager.requestActivation("orb-click")` (starts backend if dormant; toggles the main window if active). The orb window is draggable via `-webkit-app-region: drag`; its close is intercepted → hide, never quit.
- The orb stays alive in DORMANT — it is the persistent visual anchor while the backend is off.

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

- `MambaApp.tsx`: main chat/voice UI. `MambaAudioSession` (`src/audio.ts`) opens `WebSocket(<origin>/live)`, streams mic PCM as `audio` messages, sends `text` messages, and handles `transcription` / `progress` / `status` / `permission_request` / `turnComplete`. Permission approvals go back as `permission_response`. Sub-panels: `TranscriptPanel`, `TextChatFallback`, `SudoPopup` (approval modal), `BrowserAgent` (in-app browser view), settings (`settingsStore.ts`, synced to `/api/settings`), wake word (`wakeWord.ts`, Web Speech API, "hey mamba").
- `FloatingOrb.tsx` + `src/orb/`: the orb presentation described in §16.
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
| `WS /live` | bidirectional: `text` / `audio`(base64) in; `transcription`, `progress` (milestones), `status` (`connected/thinking/permission/listening`), `permission_request`, `turnComplete`, `error` out; `video` frames acknowledged; `permission_response` resumes |
| `GET/POST /api/settings` | UI preferences ↔ `.mamba/settings.json` |
| `GET/POST /api/reminders` | reminders ↔ `.mamba/reminders.json` |

CORS is permissive (loopback desktop use). Approval pauses surface as `awaiting_approval: true` / `permission_request` so any client can render its own confirmation UI.

---

## 22. Capability Status: Implemented / Partial / Planned

### ✅ Currently implemented

- **Core lifecycle** — intake, referent resolution, context, memory retrieval, capability-grounded planning, permission-gated execution, observation, verification, replanning, memory update (§3).
- **13 capabilities** in the registry: filesystem, terminal, desktop, system, screen (+OCR), web, github, memory, analyze, project_understanding — plus email/calendar/messaging skill wiring (see partial).
- **Permissions** — LOW/MEDIUM→ALLOW, HIGH→ASK, CRITICAL→DENY, metadata escalation, phrase-based approval/denial/resume (§8).
- **Verification** — predicate checks with replan-on-failure (§9).
- **Memory** — SQLite WAL, local embeddings + keyword fallback, supersession, transient filtering (§10).
- **Project understanding** — local discovery + git context, read-only (§11).
- **GitHub read integration** — repos, files, issues, PRs, code search via API (§12).
- **Web search** — Tavily, key-gated with honest unavailable messaging (§13).
- **Voice** — Groq Whisper STT, Cloudflare Aura-1 TTS, normalization, degraded-mode handling; CLI loop + WebSocket audio path (§14).
- **Transport adapter** — FastAPI + WebSocket, zero-intelligence contract (§21).
- **Electron shell** — dormant-first lifecycle, backend ownership, tray, global hotkey, floating Orb, preload bridge (§15–§19).
- **Multi-turn continuity** — pending approvals, active-entity tracking, prior-turn context.

### ⚠️ Partially implemented

- **Email / Calendar / Messaging** — intents, skills, tools, permission gates, and verification receipts are fully wired, but the providers are **simulated** (`SimulatedEmailProvider`, `SimulatedCalendarProvider`, `SimulatedMessagingProvider` with sample data). They demonstrate the complete UX flow; no real mailbox/calendar/chat service is connected.
- **Reminders** — the transport persists reminder lists (`.mamba/reminders.json`) and the React UI manages them, but there is no due-time scheduler firing them yet. (Root-level `server_reminders.ts`/`server_path.ts` are unreferenced legacy from an earlier prototype.)
- **Screen visual understanding** — screenshot/OCR work locally; deeper visual analysis routes through the model router and depends on a configured vision-capable provider.
- **Wake word** — browser Web Speech API detection ("hey mamba") exists in the React UI; always-on system-level listening is not implemented.

### 🔮 Planned / future (not implemented — do not document as present)

- Real email/calendar/messaging provider integrations (Gmail/Outlook/Google Calendar/WhatsApp-style).
- Reminder scheduling/delivery engine.
- Packaged desktop distribution (installer, auto-update, code signing).
- Mobile companion / remote access beyond loopback.
- Multi-user / team workspaces (Mamba is strictly single-user local-first today).

---

## 23. Explicit Architecture Rules

These are invariants. Code and documentation must not contradict them:

1. **Mamba Core is the intelligence/execution system.** Planning, permissions, skills, tools, verification, memory, and model routing live in `core/`, `agents/`, `models/`, `skills/`, `tools/`, `permissions/`, `verification/`, `memory/`.
2. **Electron is the desktop shell / presentation / lifecycle boundary.** Window, tray, hotkey, orb hosting, backend process ownership, idle lifecycle. Nothing else.
3. **React is the interface layer.** Renders state, captures input, forwards to transport. No orchestration, no planning, no tool calls.
4. **`api/server.py` is a transport adapter, not a second brain.** Forward + format only.
5. **The Orb is a presentation layer, not an AI agent.** It visualizes state and activates the session.
6. **Skills provide reusable capabilities.** Named, described, intent-mapped units with authoritative risk metadata.
7. **Tools perform external actions.** Validated handlers that touch the outside world.
8. **Permissions govern risky actions.** The policy is authoritative over the model.
9. **Verification checks whether requested outcomes actually occurred.** Predicates over observations, not model self-assessment.
10. **Memory provides persistent context.** Durable facts with supersession; transient reads never persist.
11. **Models are intelligence providers and must remain provider-independent.** Router + provider abstraction; no provider SDKs; stdlib HTTP.
12. **Do not create a second orchestration system** in the frontend or Electron shell. There is one execution loop: Brain's.

---

## Appendix A — Repository Map

```
app.py ..................... entry point: CLI / voice / --server, create_runtime()
api/ ....................... FastAPI transport adapter (server.py)
core/ ...................... runtime.py (boundary), brain.py (lifecycle),
                             capabilities.py (13-capability registry),
                             project.py (discovery), context/state/types
agents/ .................... planning_agent.py, planner.py (live);
                             registry.py, tools/*, server.py (legacy, unused)
models/ .................... router.py, providers/ (nvidia, groq, gemini)
skills/ .................... capability skills + mixed task executor
tools/ ..................... external-action handlers (filesystem, terminal,
                             desktop, system, screen, web, github, email,
                             calendar, messaging)
permissions/ ................ DefaultPermissionPolicy
verification/ .............. DefaultVerifier
memory/ .................... SQLite store, manager, embeddings, retrieval
voice/ ..................... VoiceInterface, Groq STT, Cloudflare TTS
tasks/ ..................... TaskExecutor / TaskHandler contracts
electron/ .................. desktop shell (main, backend/frontend/lifecycle/
                             tray/hotkey managers, preload, static server)
src/ ....................... React UI (MambaApp, FloatingOrb, orb shaders,
                             audio WS session, wake word, settings)
docs/ ...................... this documentation set
tests/ ..................... 154 tests across 12 files
.mamba/ .................... runtime data (memory.db, settings.json,
                             reminders.json) — created at runtime, gitignored
```
