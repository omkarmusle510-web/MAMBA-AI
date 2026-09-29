# Mamba Voice Interface Guide

Mamba features a complete, native hands-free voice interface that coordinates speech input and natural speech output through the canonical Mamba Core runtime. Voice is available in two clients — the Python CLI loop and the desktop React app — both executing through the same `MambaRuntime`.

---

## 1. End-to-End Voice Flow (CLI)

```
Microphone
  │ (recorded audio .wav)
  ▼
STT Provider (Groq Whisper Large v3 Turbo)
  │ (transcribed text)
  ▼
Canonical Runtime (core/runtime.py -> core/brain.py)
  │ (intake -> planning -> permission -> tools -> verification -> memory)
  ▼
Response Normalizer (voice/normalization.py)
  │ (strips markdown, code blocks, formats fluent speech)
  ▼
TTS Provider (Cloudflare Workers AI Aura-1)
  │ (synthesized .mp3 audio)
  ▼
Speaker (Audio Playback)
```

## 1b. Voice Flow (Desktop App) — Phase A (one-turn)

```
Orb renderer (DORMANT): wake listener (src/wake/) hears "hey mamba"
  │  IPC mamba:wake-detected  →  shell activates session (existing lifecycle)
  ▼
Main window: one voice turn — listening cue → VAD captures ONE utterance
  │  WebSocket /live  {"type": "audio", "format": "wav", "audio": "<base64 WAV>"}
  ▼
api/server.py — validates WAV framing, transcribes (Groq STT)
  │  {"type": "transcription", "role": "user", "text": "…"}
  ▼
MambaRuntime (identical Core lifecycle as the CLI; request tagged input_modality="voice")
  │  {"type": "transcription", "role": "model", "text": "…"}
  ▼
Server synthesizes the reply (Cloudflare TTS) and returns the COMPLETE
audio blob — no streaming:  {"type": "audio", "format": "mp3", "audio": "…"}
  ▼
Renderer decodes + plays it; playback end + turnComplete → mic released,
turn ends. No continuous conversation (Phase B), no barge-in (Phase C).
```

The desktop path reuses the same transport adapter and Core — no separate voice brain. The wake listener is a desktop interaction service hosted by the Orb renderer (the only renderer alive while DORMANT); the Orb itself stays presentation-only.

**Wake engine (interim):** the current backend is the browser Web Speech API wrapped in a swappable `WakeEngine` interface (`src/wake/`). It exists so the one-turn pipeline can be proven end-to-end. Do NOT bundle a keyword-spotter model until availability, licensing, Electron/Chromium compatibility, DORMANT CPU usage, and "Hey Mamba" accuracy are all established.

**Voice approval policy (conservative):** voice-originated requests are tagged `input_modality="voice"`. A spoken "yes" NEVER approves a pending HIGH-risk action — the user must confirm visually (SudoPopup click) or by typed approval. The pending approval survives a rejected voice approval. Voice "no"/"cancel" still cancels (safe direction).

---

## 2. Prerequisites & Credentials

1. **Speech-to-Text (STT)**:
   - Powered by Groq's hosted Whisper endpoint (`voice/stt.py :: GroqSTTProvider`).
   - Requires `GROQ_API_KEY` in `.env`.
   - Optional model override: `GROQ_STT_MODEL` (default `whisper-large-v3-turbo`).

2. **Text-to-Speech (TTS)**:
   - Powered by Cloudflare Workers AI Aura-1 (`voice/tts.py :: CloudflareTTSProvider`).
   - Requires `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_API_TOKEN` in `.env`.
   - Optional model override: `CLOUDFLARE_TTS_MODEL` (default `@cf/deepgram/aura-1`).

---

## 3. Starting Voice Mode

### Direct Command Line Flag
```powershell
python app.py --voice
```

### Switching Inside Interactive Prompt
```text
mamba> voice
Switching to voice interface...
```

### Desktop App
Click the Orb presence (or say the wake phrase while dormant) to run a single voice turn: a listening cue plays, one utterance is captured, Mamba answers by voice, and the mic is released. Enable/disable wake-word listening in Settings (default phrase **"hey mamba"**, configurable sensitivity). A text-chat fallback panel is available when no microphone is present.

---

## 4. Voice Conversation Loop

Once voice mode is active (CLI):
1. Press **`[Enter]`** to start recording your voice command.
2. Speak your request clearly into your microphone.
3. Press **`[Enter]`** to finish recording.
4. Mamba transcribes your speech, executes the request through Core, displays results in terminal, and speaks the answer aloud.

To exit voice mode, type `exit` or `text` to return to the text CLI.

`VoiceInterface.process_voice_input()` is the shared entry point used by the CLI loop, the `POST /api/voice` endpoint, and the `/live` WebSocket voice-turn branch. On transport paths, `speak_response=False` — transcription and execution happen server-side while audio playback stays client-side (`synthesize_speech_text()` returns a `(format, bytes)` blob for the socket; it never raises).

---

## 5. Graceful Degradation

- **TTS Rate Limits (HTTP 429)**: If Cloudflare TTS hits a rate limit or quota ceiling, Mamba marks TTS degraded for the remainder of the session and continues smoothly in text-only audio mode without failing the core execution result.
- **Empty Audio**: If no speech is detected, Mamba reports a clean notification rather than raising an exception.
- **Audio Playback**: Player errors are safely absorbed without corrupting background execution state.
- **Missing credentials**: STT/TTS providers raise clear configuration errors naming the exact missing environment variable; text mode is unaffected.
