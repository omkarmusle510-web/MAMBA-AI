# Mamba AI Architecture Specification

> **Mamba** is a personal AI operating layer between the user and digital tools.

---

## 1. Fixed Architectural Flow

Mamba strictly adheres to a deterministic, observation-driven lifecycle without arbitrary agent loops or unconstrained autonomous execution:

```
USER
  │ (text / speech input)
  ▼
INTERFACE (app.py CLI / voice_loop)
  │
  ▼
MAMBA RUNTIME (core/runtime.py)
  │
  ▼
MAMBA CORE (core/brain.py)
  │
  ├─► 1. INTAKE & REFERENT RESOLUTION
  │      Deterministic entity binding ("it", "that", "the file", "the error")
  │      Approval & denial intent handling
  │
  ├─► 2. CONTEXT & MEMORY RETRIEVAL (memory/v2)
  │      Project discovery (root, framework, entry points)
  │      Semantic embedding retrieval + keyword fallback via SQLite
  │
  ├─► 3. PLANNING & MODEL ROUTING (agents/planning_agent.py, models/router.py)
  │      Multi-provider LLM routing (NVIDIA, Groq, Gemini)
  │      Resilient JSON plan generation with capability grounding
  │
  ├─► 4. PERMISSIONS & SAFETY (permissions/policy.py)
  │      DefaultPermissionPolicy: LOW/MEDIUM -> ALLOW, HIGH -> ASK, CRITICAL -> DENY
  │      Metadata escalation (destructive, irreversible, user-sensitive)
  │
  ├─► 5. TASK EXECUTION (skills/mixed.py, tools/*)
  │      Filesystem, Terminal, Desktop, System, Screen, Web, GitHub,
  │      Email, Calendar, Messaging, Memory, Project Understanding
  │
  ├─► 6. OBSERVATION & REPLANNING
  │      Bounded cycle (max 10 iterations)
  │      Tracks completed steps to prevent identical plan re-execution
  │
  ├─► 7. VERIFICATION (verification/verifier.py)
  │      Predicate checks: contains, not_contains, exit_code, file existence
  │
  ├─► 8. MEMORY PERSISTENCE
  │      Filters transient inspection (list_dir, read_file, system_info)
  │      Persists durable facts, preferences, project decisions
  │
  ▼
RESPONSE ──► User (Console / Voice TTS)
```

---

## 2. Core Layers & Responsibilities

| Layer | Primary Module | Responsibility |
| :--- | :--- | :--- |
| **Runtime** | `core/runtime.py` | Single application-facing boundary wrapping Brain. Threaded progress updates. |
| **Brain** | `core/brain.py` | Central lifecycle coordinator. Coordinates plan-execute-observe-verify cycles. |
| **Capabilities** | `core/capabilities.py` | Grounding registry of 12 discoverable capabilities with explicit limits and provider status. |
| **Models** | `models/router.py` | Provider-independent routing across NVIDIA, Groq, and Gemini with automatic candidate fallback. |
| **Planning** | `agents/planning_agent.py` | Translates goals and context into atomic `PlanStep` instructions. |
| **Permissions** | `permissions/policy.py` | Authoritative permission checks. Intercepts high-risk actions for user confirmation. |
| **Verification** | `verification/verifier.py` | Validates step outcomes against expectations before marking steps completed. |
| **Memory** | `memory/` | SQLite-backed Memory V2 with local sentence-transformers semantic embeddings and keyword fallback. |
| **Voice** | `voice/interface.py` | Voice input coordinator with Groq Whisper STT, Cloudflare Aura-1 TTS, and audio capture. |

---

## 3. Capability Catalog

Mamba defines **12 discoverable capabilities**:

1. **Filesystem (`filesystem`)**: Read, write, create, list, and delete files/directories on local disk. Destructive operations require user confirmation.
2. **Terminal & Shell (`terminal`)**: Execute PowerShell/shell commands. Mutating commands escalate to user approval.
3. **Desktop & Windows (`desktop`)**: Launch applications, open browser URLs, focus windows, manage clipboard.
4. **System Information (`system`)**: Query CPU, GPU, memory, platform, and OS statistics.
5. **Screen & Vision (`screen`)**: Capture full screen or bounding regions, extract text via OCR, analyze layout visually.
6. **Web Search (`web`)**: Query live web sources via Tavily API.
7. **GitHub & Git (`github`)**: Inspect repositories, files, issues, PRs, and commit history.
8. **Long-Term Memory (`memory`)**: Explicitly store and recall user facts, preferences, and project context.
9. **Email (`email`)**: Search, read, draft, and send emails with receipt verification.
10. **Calendar & Scheduling (`calendar`)**: Check schedule, detect meeting conflicts, create/modify events.
11. **Messaging & Chat (`messaging`)**: Read chat conversations, search messages, draft and send messages.
12. **Project Understanding (`project_understanding`)**: Discover project architecture, entry points, problem areas, and git state.

---

## 4. Key Architectural Guarantees

- **No Hallucinated Tools**: The planner prompt is strictly grounded by `CapabilityRegistry`.
- **Authoritative Permissions**: The model/planner can never bypass the permission policy. Any step marked `ASK` pauses execution cleanly until the user inputs an approval phrase.
- **Fail-Safe Replanning**: If a step fails, the brain replans with prior failure observations in context. If the planner produces an identical failing plan, the loop stops cleanly rather than spinning.
- **Provider Independence**: Core execution is decoupled from specific LLM providers. If NVIDIA is rate-limited, Groq or Gemini seamlessly handle routing.
- **Transient Memory Hygiene**: Ephemeral tool reads (e.g. `list_directory`, `read_file`, `screenshot`) are not persisted into durable memory, keeping retrieval clean and fast.

