<div align="center">

# 🐍 MAMBA-AI
### The Personal AI Operating Layer

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Tests](https://img.shields.io/badge/tests-154%20passing-brightgreen.svg)]()
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Architecture: Fixed Core](https://img.shields.io/badge/architecture-Mamba%20Core-purple.svg)](docs/ARCHITECTURE.md)

**Mamba** is a personal AI operating layer between you and your digital tools.
It understands your goal, retrieves relevant context and memory, plans atomic steps, checks safety permissions, executes tools, observes and verifies results, replans when necessary, remembers durable facts, and reports back via text or voice.

</div>

---

## 🧭 Why Mamba?

Mamba is **not** a generic chatbot wrapper, a coding-only agent, or an uncontrolled tool loop. It is an operating layer designed with strict safety, verification, and deterministic execution guarantees:

- **🔒 Permissions-First**: Destructive or externally visible actions (deleting files, running mutating commands, sending emails) automatically pause and require explicit user approval.
- **👁️ Full Operating Awareness**: Real access to local filesystem, terminal, Windows desktop applications, screen capture, OCR, system hardware metrics, and web search.
- **🧠 Long-Term Memory**: Persistent local SQLite storage with hybrid semantic embedding search, keyword matching fallback, intelligent supersession, and transient read filtering.
- **🔄 Observation & Replanning**: Executes steps sequentially, inspects real outputs, verifies predicates (exit codes, expected text, file creation), and replans recovery steps if a failure occurs.
- **🎙️ Voice Native**: Speech-to-Text via Groq Whisper and natural Text-to-Speech via Cloudflare Workers AI Aura-1.
- **🔀 Provider Independent**: Hot-swappable model routing across **NVIDIA NIM**, **Groq**, and **Google Gemini** with automatic fallback.
- **🖥️ Desktop Native**: An Electron shell with a floating Mamba Orb, system tray, and global hotkey — the Python backend only runs while you are actively using it.

---

## 🏛️ Architecture

All user interactions converge into a single canonical execution path. There is exactly one brain — **Mamba Core** — and every interface is a thin presentation or transport layer in front of it:

```text
USER
  │  text / speech / click
  ▼
INTERFACE (presentation only — no planning, no tool execution)
  ├─► app.py CLI            (interactive text shell, one-shot commands)
  ├─► voice/ VoiceInterface (microphone loop)
  └─► Electron desktop app  (React UI + floating Orb window)
  │
  ▼
TRANSPORT (api/server.py — FastAPI adapter, zero intelligence)
  ├─► POST /api/chat        (JSON request/response)
  ├─► POST /api/voice       (audio upload → transcription + execution)
  └─► WS   /live            (real-time bidirectional: text, audio, progress,
                             transcriptions, permission requests)
  │
  ▼
MAMBA RUNTIME (core/runtime.py — the single application-facing boundary)
  │
  ▼
MAMBA CORE (core/brain.py — the sole intelligence/execution system)
  ├─► Intake & Referent Resolution ("it", "that file", "the error")
  ├─► Context & Long-Term Memory Retrieval (SQLite + sentence-transformers,
  │                                          keyword fallback)
  ├─► Planning (agents/planning_agent.py) & Model Routing (models/router.py:
  │                                          NVIDIA / Groq / Gemini)
  ├─► Permission Evaluation (ALLOW / ASK / DENY + metadata escalation)
  ├─► Skill & Tool Execution (skills/* → tools/*: Filesystem, Terminal,
  │                           Desktop, Screen, GitHub, Web, Productivity…)
  ├─► Observation & Replanning (bounded: max 10 reasoning cycles)
  ├─► Verification (predicates: exit codes, expected text, file existence…)
  ├─► Memory Update (durable facts only; transient reads filtered out)
  ▼
RESPONSE ──► Console / Voice TTS / WebSocket UI
```

**Hard boundaries** (see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)):

- **Mamba Core is the intelligence/execution system.** Planning, permissions, tools, verification, and memory all live here.
- **Electron is the desktop shell** — lifecycle and presentation only. It never plans, routes models, or executes tools.
- **React is the interface layer** — it renders state and forwards user input. It contains no orchestration.
- **`api/server.py` is a transport adapter, not a second brain.** It forwards requests to `MambaRuntime` and formats responses.
- **The Orb is a presentation layer, not an AI agent.** It visualizes shell state (`idle`, `listening`, `thinking`, `speaking`, `permission`, `error`) and activates the session on click.
- **Skills provide reusable capabilities; tools perform external actions; permissions govern risky actions; verification checks outcomes; memory provides persistent context; models are provider-independent intelligence.**

---

## ⚡ Quick Start

### 1. Prerequisites
- **Python 3.11+**
- **Node.js 18+** (only for the Electron desktop app)
- (Optional for OCR) [Tesseract OCR for Windows](https://github.com/UB-Mannheim/tesseract/wiki)
- API key for at least one supported model provider: **NVIDIA**, **Groq**, or **Gemini**.

### 2. Installation
```powershell
# Clone the repository
git clone https://github.com/omkarmusle510-web/MAMBA-AI.git
cd MAMBA-AI

# Create and activate virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Install dependencies
pip install -r requirements.txt
```

### 3. Configure Environment Variables
Copy `.env.example` to `.env` and insert your API keys:
```powershell
cp .env.example .env
```

Edit `.env`:
```env
# At least one model provider is required:
NVIDIA_API_KEY=nvapi-...
GROQ_API_KEY=gsk_...
GEMINI_API_KEY=...

# Optional Capabilities
TAVILY_API_KEY=tvly-...             # Live web search
GITHUB_TOKEN=ghp_...                # GitHub repo & code inspection (higher rate limits)
CLOUDFLARE_ACCOUNT_ID=...          # Voice TTS
CLOUDFLARE_API_TOKEN=...           # Voice TTS
```

---

## 🚀 Usage

### Interactive Text Mode
Launch the interactive shell:
```powershell
python app.py
```
```text
Mamba AI (type 'voice' for voice mode, 'exit' or 'quit' to quit)

mamba> What is this project?
  ⟩ Understanding...
  ⟩ Planning...
  ⟩ Executing...

[Status: completed]
This is MAMBA-AI, a personal AI operating layer bridging digital tools and user commands.
```

### One-Shot Command Execution
Pass your prompt directly as arguments:
```powershell
python app.py "Inspect current system memory and CPU usage"
```

### Interactive Voice Mode
Talk directly with Mamba using hands-free microphone input:
```powershell
python app.py --voice
```
*(Or type `voice` inside the interactive text prompt).*

### HTTP / WebSocket Transport
Run the transport adapter (used by the desktop app and any HTTP client):
```powershell
python app.py --server
```
This starts the FastAPI transport adapter on `http://127.0.0.1:8000` (override with `MAMBA_PORT`):
- `GET /health` — liveness probe
- `POST /api/chat` — `{"input": "..."}` → execution result (`status`, `output`, `awaiting_approval`)
- `POST /api/voice` — audio file upload → transcription + execution
- `WS /live` — real-time channel: send `{"type": "text"}` / `{"type": "audio"}`; receive `transcription`, `progress`, `status`, `permission_request`, `turnComplete` messages
- `GET/POST /api/settings`, `GET/POST /api/reminders` — UI preferences and reminders persisted under `.mamba/`

### Desktop App (Electron)
```powershell
npm install
npm run build
npm run electron
```
What you get:
- **Floating Mamba Orb** — a frameless, always-on-top orb in the bottom-right corner. Click it to wake Mamba; its animation mirrors live state (idle, listening, thinking, speaking, permission, error).
- **Main window** — the full React chat/voice interface (opens on activation).
- **System tray** — Show / Hide / Quit Mamba.
- **Global hotkey** — `Ctrl + Space` activates Mamba from anywhere.
- **Dormant-first lifecycle** — on launch the shell starts **DORMANT**: the Python backend is *not* running. Pressing `Ctrl+Space`, clicking the Orb, or using the tray starts the backend (`python app.py --server`) and transitions to **ACTIVE**. After 10 minutes of inactivity (with no in-flight task, pending permission, or active voice session) it goes **IDLE**, then shuts the backend down and returns to **DORMANT**. The shell only ever terminates backend processes it started itself.

---

## 🛠️ Capability Catalog

Mamba includes **13 discoverable capabilities** grounded in runtime reality. The planner can only use capabilities the registry advertises — tools are never hallucinated.

| Capability | Intent Examples | Description | Safety Gate |
| :--- | :--- | :--- | :--- |
| **Filesystem** | `read_file`, `write_file`, `delete_file` | Manage local files & directories | Deletions & overwrites require **Approval** |
| **Terminal** | `run_command` | Execute PowerShell/shell commands | Mutating commands require **Approval** |
| **Desktop** | `open_application`, `open_url`, `clipboard` | App launcher, browser opener, clipboard | Allowed |
| **System** | `system_info`, `gpu_info` | CPU, GPU, memory, platform specs | Read-only / Allowed |
| **Screen** | `screenshot`, `ocr`, `visual_understanding` | Capture screens & read on-screen text | Read-only / Allowed |
| **Web** | `web_search` | Real-time search via Tavily API | Read-only / Allowed |
| **GitHub** | `get_repository`, `list_issues`, `search_code` | Inspect GitHub repos, files, issues, PRs | Read-only / Allowed |
| **Memory** | `remember`, `recall` | SQLite-backed facts & preferences | Allowed |
| **Email** ⚠️ | `search_emails`, `draft_email`, `send_email` | Simulated provider (sample inbox, dev/test) | Sending requires **Approval** |
| **Calendar** ⚠️ | `list_events`, `check_conflicts`, `create_event` | Simulated provider (sample calendar, dev/test) | Modifying requires **Approval** |
| **Messaging** ⚠️ | `read_messages`, `draft_message`, `send_message` | Simulated provider (sample chats, dev/test) | Sending requires **Approval** |
| **Analyze** | `analyze`, `calculate`, `summarize`, `respond` | Model-backed reasoning & response synthesis | Allowed |
| **Project Understanding** | `project_info`, `explain_architecture`, `find_problems` | Discover frameworks, layout, bugs, git status | Read-only / Allowed |

> ⚠️ **Email, Calendar, and Messaging currently run on built-in *simulated* providers** with sample data, intended for development and testing. There is not yet a real Gmail/Outlook/Calendar/chat integration — connecting one is on the roadmap. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full currently-implemented / partially-implemented / planned breakdown.

### Models & Routing

`models/router.py` routes every model call across the configured providers with deterministic, capability-aware policy (explicit provider/model/capability hints → multimodal compatibility → available providers → stable order tie-break), failing honestly instead of silently substituting when an explicit requirement can't be met.

| Provider | Default model | Notes |
| :--- | :--- | :--- |
| **NVIDIA NIM** | `nvidia/nemotron-3-super-120b-a12b` | OpenAI-compatible endpoint; vision default `meta/llama-3.2-11b-vision-instruct` (`NVIDIA_VISION_MODEL`) |
| **Groq** | `qwen/qwen3.8-27b` | Fast inference; also powers Whisper STT |
| **Google Gemini** | `gemini-2.5-flash` | Multimodal-capable |

Providers without credentials are skipped at startup; at least one is required.

---

## 🧪 Testing & Verification

Mamba includes a comprehensive test suite validating core lifecycles, compound recovery, permissions, memory, and model fallbacks:

```powershell
# Run the complete test suite (154 tests)
pytest tests/ -v
```

```text
tests/test_api_server.py                   transport adapter (FastAPI + WebSocket)
tests/test_capability_registry.py          capability grounding & availability
tests/test_communication_productivity.py   email/calendar/messaging skills
tests/test_compound_workflow.py            multi-step recovery workflows
tests/test_core_lifecycle.py               Brain request → response lifecycle
tests/test_memory_v2.py                    memory store, supersession, retrieval
tests/test_model_router.py                 provider routing & fallback
tests/test_permission_and_continuation.py  ASK/approval pause & resume
tests/test_planning_agent.py               JSON plan generation & validation
tests/test_project_understanding.py        project discovery & git context
tests/test_reliability_hardening.py        loop prevention, failure handling
tests/test_voice.py                        STT/TTS providers & voice loop
```

---

## 📚 Documentation

- [**Architecture Specification**](docs/ARCHITECTURE.md) — Full system spec: Core lifecycle, orchestration, agents, skills/tools, models, permissions, verification, memory, Electron shell, Orb, transport boundaries, and the implemented / partial / planned capability matrix.
- [**Project Understanding Guide**](docs/PROJECT_UNDERSTANDING.md) — Codebase analysis, architectural diagnosis, and multi-turn entity resolution.
- [**Long-Term Memory**](docs/MEMORY.md) — SQLite persistence, vector embeddings, supersession, and transient data filtering.
- [**Voice Interface Guide**](docs/VOICE_INTERFACE.md) — Groq Whisper STT, Cloudflare Aura-1 TTS, speech normalization, and the WebSocket voice path.

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).
created by omkar musale
