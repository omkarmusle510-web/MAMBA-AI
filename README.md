<div align="center">

# 🐍 MAMBA-AI
### The Personal AI Operating Layer

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Tests](https://img.shields.io/badge/tests-150%20passing-brightgreen.svg)]()
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
- **🧠 Long-Term Memory V2**: Persistent local SQLite storage with hybrid semantic embedding search, keyword matching fallback, intelligent supersession, and transient read filtering.
- **🔄 Observation & Replanning**: Executes steps sequentially, inspects real outputs, verifies predicates (exit codes, expected text, file creation), and replans recovery steps if a failure occurs.
- **🎙️ Voice Native**: Speech-to-Text via Groq Whisper and natural Text-to-Speech via Cloudflare Workers AI Aura-1.
- **🔀 Provider Independent**: Hot-swappable model routing across **NVIDIA NIM**, **Groq**, and **Google Gemini** with automatic fallback.

---

## 🏛️ Architecture

All user interactions converge into a single canonical execution path:

```text
USER (Text CLI or Voice Input)
  │
  ▼
MAMBA RUNTIME (core/runtime.py)
  │
  ▼
MAMBA CORE (core/brain.py)
  ├─► Intake & Referent Resolution ("it", "that file", "the error")
  ├─► Context & Long-Term Memory Retrieval (SQLite + sentence-transformers)
  ├─► Planning & Provider Routing (NVIDIA / Groq / Gemini)
  ├─► Permission Evaluation (ALLOW / ASK / DENY)
  ├─► Tool & Skill Execution (Filesystem, Terminal, Desktop, Screen, GitHub, Web, Productivity)
  ├─► Observation & Verifier Checks (Predicates, Regex, Exit Codes)
  ├─► Memory Update (Durable facts only; transient reads filtered)
  ▼
RESPONSE ──► Console / Voice TTS
```

*For complete architectural specifications, see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).*

---

## ⚡ Quick Start

### 1. Prerequisites
- **Python 3.11+**
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
GITHUB_TOKEN=ghp_...                # GitHub repo & code inspection
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

---

## 🛠️ Capability Catalog

Mamba includes **12 discoverable capabilities** grounded in runtime reality:

| Capability | Intent Examples | Description | Safety Gate |
| :--- | :--- | :--- | :--- |
| **Filesystem** | `read_file`, `write_file`, `delete_file` | Manage local files & directories | Deletions & overwrites require **Approval** |
| **Terminal** | `run_command` | Execute PowerShell/shell commands | Mutating commands require **Approval** |
| **Desktop** | `open_application`, `open_url`, `clipboard` | App launcher, browser opener, clipboard | Allowed |
| **System** | `system_info`, `gpu_info` | CPU, GPU, memory, platform specs | Read-only / Allowed |
| **Screen** | `screenshot`, `ocr`, `visual_understanding`| Capture screens & read on-screen text | Read-only / Allowed |
| **Web** | `web_search` | Real-time search via Tavily API | Read-only / Allowed |
| **GitHub** | `get_repository`, `list_issues`, `search_code` | Inspect GitHub repos and code | Read-only / Allowed |
| **Memory** | `remember`, `recall` | SQLite-backed facts & preferences | Allowed |
| **Email** | `search_emails`, `draft_email`, `send_email` | Search, draft, and send emails | Sending requires **Approval** |
| **Calendar** | `list_events`, `check_conflicts`, `create_event` | Manage schedule & meetings | Modifying requires **Approval** |
| **Messaging** | `read_messages`, `draft_message`, `send_message` | Chat search and replies | Sending requires **Approval** |
| **Project Understanding** | `project_info`, `explain_architecture`, `find_problems` | Discover frameworks, layout, bugs, git status | Read-only / Allowed |

---

## 🧪 Testing & Verification

Mamba includes a comprehensive test suite validating core lifecycles, compound recovery, permissions, memory, and model fallbacks:

```powershell
# Run the complete test suite (150 tests)
pytest tests/ -v
```

```text
tests/test_capability_registry.py          17 passed
tests/test_communication_productivity.py   12 passed
tests/test_compound_workflow.py             4 passed
tests/test_core_lifecycle.py               12 passed
tests/test_memory_v2.py                    22 passed
tests/test_model_router.py                 16 passed
tests/test_permission_and_continuation.py  16 passed
tests/test_planning_agent.py               12 passed
tests/test_project_understanding.py         9 passed
tests/test_reliability_hardening.py        18 passed
tests/test_voice.py                        12 passed
======================= 150 passed in 55.67s =======================
```

---

## 📚 Documentation

- [**Architecture Specification**](docs/ARCHITECTURE.md) — Detailed diagram of Core, Runtime, Brain, and Safety Invariants.
- [**Project Understanding Guide**](docs/PROJECT_UNDERSTANDING.md) — Codebase analysis, architectural diagnosis, and multi-turn entity resolution.
- [**Long-Term Memory V2**](docs/MEMORY.md) — SQLite persistence, vector embeddings, supersession, and transient data filtering.
- [**Voice Interface Guide**](docs/VOICE_INTERFACE.md) — Groq Whisper STT, Cloudflare Aura-1 TTS, and speech normalization.

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).
created by omkar musale
