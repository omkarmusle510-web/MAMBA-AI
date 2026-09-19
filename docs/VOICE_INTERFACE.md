# Mamba Voice Interface Guide

Mamba features a complete, native hands-free voice interface that coordinates speech input and natural speech output through the canonical Mamba Core runtime.

---

## 1. End-to-End Voice Flow

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

---

## 2. Prerequisites & Credentials

1. **Speech-to-Text (STT)**:
   - Powered by Groq's high-speed Whisper endpoint.
   - Requires `GROQ_API_KEY` in `.env`.
   - Optional model override: `GROQ_STT_MODEL=whisper-large-v3-turbo`.

2. **Text-to-Speech (TTS)**:
   - Powered by Cloudflare Workers AI Aura-1.
   - Requires `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_API_TOKEN` in `.env`.
   - Optional model override: `CLOUDFLARE_TTS_MODEL=@cf/deepgram/aura-1`.

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

---

## 4. Voice Conversation Loop

Once voice mode is active:
1. Press **`[Enter]`** to start recording your voice command.
2. Speak your request clearly into your microphone.
3. Press **`[Enter]`** to finish recording.
4. Mamba transcribes your speech, executes the request through Core, displays results in terminal, and speaks the answer aloud.

To exit voice mode, type `exit` or `text` to return to the text CLI.

---

## 5. Graceful Degradation

- **TTS Rate Limits (HTTP 429)**: If Cloudflare TTS hits a rate limit or quota ceiling, Mamba marks TTS degraded for the remainder of the session and continues smoothly in text-only audio mode without failing the core execution result.
- **Empty Audio**: If no speech is detected, Mamba reports a clean notification rather than raising an exception.
- **Audio Playback**: Player errors are safely absorbed without corrupting background execution state.

