/**
 * Audio session for the Mamba desktop voice transport (/live WebSocket).
 *
 * Phase A (one-turn voice):
 * - connect(): WebSocket + TTS playback output only. The microphone is NOT
 *   held open; it is acquired per voice turn and released afterwards.
 * - startVoiceTurn(): acquire mic -> listening cue -> VAD-segmented capture
 *   -> ONE WAV-framed utterance sent as {type:"audio", format:"wav"}.
 * - The server transcribes (Groq STT) -> MambaRuntime -> speaks the reply
 *   back as a complete audio blob (no fake streaming).
 * - Playback end + turnComplete -> onVoiceTurnComplete -> the turn ends and
 *   the mic is released. No continuous conversation (Phase B), no barge-in
 *   (Phase C).
 *
 * Text turns (typed chat) use the same socket via sendText() and are
 * unaffected by voice-turn state.
 */

import { wavToBase64 } from "./audio/wav";

export type LiveState =
  | "disconnected"
  | "connecting"
  | "idle"
  | "listening"
  | "thinking"
  | "speaking"
  | "permission"
  | "error";

// --- One-turn capture tuning (VAD-lite, energy based) ---
// RMS is computed on float32 mic samples: silence ~= 0.001-0.005,
// conversational speech ~= 0.02-0.2. These constants are conservative
// (prefer capturing a little extra over clipping the start).
const VAD_START_THRESHOLD = 0.02;
const VAD_END_THRESHOLD = 0.012;
const VAD_SILENCE_END_MS = 1200;
const VAD_MAX_TURN_MS = 20000;
const VAD_MIN_AUDIO_MS = 400;

// Convert Base64 string to Uint8Array
function base64ToUint8Array(base64: string): Uint8Array {
  const binaryString = window.atob(base64);
  const len = binaryString.length;
  const bytes = new Uint8Array(len);
  for (let i = 0; i < len; i++) {
    bytes[i] = binaryString.charCodeAt(i);
  }
  return bytes;
}

export class MambaAudioSession {
  private ws: WebSocket | null = null;

  // Output context for TTS playback (kept for the session lifetime).
  private outputAudioCtx: AudioContext | null = null;

  // Per-turn capture resources (acquired in startVoiceTurn, released in endVoiceTurn).
  private inputAudioCtx: AudioContext | null = null;
  private micStream: MediaStream | null = null;
  private micSourceNode: MediaStreamAudioSourceNode | null = null;
  private micProcessorNode: ScriptProcessorNode | null = null;

  // Visualisers
  public inputAnalyser: AnalyserNode | null = null;
  public outputAnalyser: AnalyserNode | null = null;
  private outputGainNode: GainNode | null = null;

  // Playback
  private activeSources: AudioBufferSourceNode[] = [];

  // Voice-turn lifecycle
  private voiceTurnActive = false;
  private turnCompleteReceived = false;

  // State Callbacks
  private onStateChange: (state: LiveState) => void;
  private onTranscription: (role: "user" | "model", text: string) => void;
  private onToolCall?: (name: string, args: any, callback: (result: any) => void) => void;
  private onError: (error: string) => void;
  private onProgress?: (milestone: string) => void;
  private onPermissionRequest?: (command: string, reason: string) => void;
  private onMemorySync?: (memories: any[]) => void;
  private onReminder?: (text: string, id: string) => void;
  private onTerminalOutput?: (tool: string, args: any, output: string) => void;
  private onVoiceTurnComplete?: () => void;

  private currentState: LiveState = "disconnected";
  private isActivated = false;

  constructor(handlers: {
    onStateChange: (state: LiveState) => void;
    onTranscription: (role: "user" | "model", text: string) => void;
    onToolCall?: (name: string, args: any, callback: (result: any) => void) => void;
    onError: (error: string) => void;
    onProgress?: (milestone: string) => void;
    onPermissionRequest?: (command: string, reason: string) => void;
    onMemorySync?: (memories: any[]) => void;
    onReminder?: (text: string, id: string) => void;
    onTerminalOutput?: (tool: string, args: any, output: string) => void;
    onVoiceTurnComplete?: () => void;
  }) {
    this.onStateChange = handlers.onStateChange;
    this.onTranscription = handlers.onTranscription;
    this.onToolCall = handlers.onToolCall;
    this.onError = handlers.onError;
    this.onProgress = handlers.onProgress;
    this.onPermissionRequest = handlers.onPermissionRequest;
    this.onMemorySync = handlers.onMemorySync;
    this.onReminder = handlers.onReminder;
    this.onTerminalOutput = handlers.onTerminalOutput;
    this.onVoiceTurnComplete = handlers.onVoiceTurnComplete;
  }

  public sendText(text: string): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: "text", text }));
    }
  }

  public sendPermissionResponse(approved: boolean): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: "text", text: approved ? "yes" : "no" }));
    }
  }

  private setState(state: LiveState) {
    this.currentState = state;
    this.onStateChange(state);
  }

  public getState(): LiveState {
    return this.currentState;
  }

  public isVoiceTurnActive(): boolean {
    return this.voiceTurnActive;
  }

  /**
   * Pushes a compressed JPEG base64 screenshot frame directly to the live WebSocket server.
   */
  public sendVideoFrame(base64Data: string) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN && this.currentState !== "disconnected") {
      this.ws.send(JSON.stringify({ type: "video", video: base64Data }));
    }
  }

  /**
   * Connect the transport: WebSocket + TTS output. No microphone is held.
   */
  public async connect(voice?: string, avatarStyle?: "character" | "orb") {
    if (this.isActivated) return;
    this.isActivated = true;
    this.setState("connecting");

    try {
      const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";

      const params = new URLSearchParams();
      if (voice) params.set("voice", voice);
      if (avatarStyle) params.set("avatarStyle", avatarStyle);
      const queryStr = params.toString() ? `?${params.toString()}` : "";

      this.ws = new WebSocket(`${protocol}//${window.location.host}/live${queryStr}`);

      this.ws.onopen = async () => {
        console.log("[Mamba] Connected to /live transport");
        try {
          if (!this.isActivated) return;

          const AudioContextClass = window.AudioContext || (window as any).webkitAudioContext;
          if (!AudioContextClass) {
            throw new Error("Voice unavailable: Web Audio API missing in this environment.");
          }

          this.outputAudioCtx = new AudioContextClass({ sampleRate: 24000 });
          if (this.outputAudioCtx.state === "suspended") {
            await this.outputAudioCtx.resume().catch(() => {});
          }

          this.outputGainNode = this.outputAudioCtx.createGain();
          this.outputAnalyser = this.outputAudioCtx.createAnalyser();
          this.outputAnalyser.fftSize = 256;
          this.outputAnalyser.smoothingTimeConstant = 0.8;

          this.outputGainNode.connect(this.outputAnalyser);
          this.outputAnalyser.connect(this.outputAudioCtx.destination);

          // Transport ready. Mic stays released until a voice turn starts.
          this.setState("idle");
        } catch (audioError: any) {
          console.error("Output audio initialization failed:", audioError);
          this.onError(`Audio error: ${audioError.message || "Could not initialize playback."}`);
          this.disconnect();
        }
      };

      this.ws.onmessage = async (event) => {
        try {
          const data = JSON.parse(event.data);

          // Root Error Handler message
          if (data.type === "error") {
            this.onError(data.error);
            if (this.voiceTurnActive) {
              // Voice-turn failures end the turn, not the whole session.
              this.completeVoiceTurn();
            } else {
              this.disconnect();
            }
            return;
          }

          // Shutdown Handler message
          if (data.type === "shutdown") {
            window.close();
            this.disconnect();
            return;
          }

          // Handle server-side states
          if (data.type === "status") {
            console.log("[Mamba WS Status]:", data.status);
            if (data.status === "connected") {
              if (!this.voiceTurnActive) this.setState("idle");
            } else if (
              data.status === "thinking" ||
              data.status === "speaking" ||
              data.status === "permission" ||
              data.status === "idle"
            ) {
              // During a voice turn the turn lifecycle drives state; the
              // server's trailing "listening" must not override "speaking".
              if (data.status === "listening" && this.voiceTurnActive) return;
              this.setState(data.status);
            } else if (data.status === "session_closed") {
              this.disconnect();
            }
            return;
          }

          // Handle progress milestones (Understanding..., Planning..., Executing...)
          if (data.type === "progress" && data.milestone) {
            this.setState("thinking");
            this.onProgress?.(data.milestone);
            return;
          }

          // Handle permission confirmation request from Mamba Core
          if (data.type === "permission_request") {
            this.setState("permission");
            this.onPermissionRequest?.(data.command || "", data.reason || "");
            return;
          }

          // Handle TTS audio payload: a COMPLETE audio blob (the provider
          // returns whole audio, not a stream). Decoded format-agnostically.
          if (data.type === "audio" && data.audio) {
            await this.playAudioMessage(data.audio);
            return;
          }

          // Handle interruption signal (reserved for Phase C barge-in)
          if (data.type === "interrupted") {
            this.handleInterruption();
          }

          // Turn complete
          if (data.type === "turnComplete") {
            this.turnCompleteReceived = true;
            this.maybeCompleteVoiceTurn();
            return;
          }

          // Handle live captions transcription
          if (data.type === "transcription") {
            this.onTranscription(data.role, data.text);
          }

          // Handle memory synchronization
          if (data.type === "memory_sync" && data.memories) {
            if (this.onMemorySync) {
              this.onMemorySync(data.memories);
            }
          }

          // Handle reminder notifications
          if (data.type === "reminder" && data.text) {
            if (this.onReminder) {
              this.onReminder(data.text, data.id || "");
            }
          }

          // Handle terminal output (tool execution results)
          if (data.type === "terminal_output" && data.output) {
            if (this.onTerminalOutput) {
              this.onTerminalOutput(data.tool, data.args, data.output);
            }
          }

          // Handle Tool Calling
          if (data.type === "toolCall") {
            const { callId, name, args } = data;
            if (this.onToolCall) {
              this.onToolCall(name, args, (result) => {
                if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                  this.ws.send(JSON.stringify({
                    type: "toolResponse",
                    id: callId,
                    name: name,
                    output: result
                  }));
                }
              });
            }
          }

        } catch (parseError) {
          console.error("Error reading server packet:", parseError);
        }
      };

      this.ws.onerror = (wsError) => {
        console.error("WebSocket transport error:", wsError);
        this.onError("Connection lost. Please check the backend and retry.");
        this.disconnect();
      };

      this.ws.onclose = () => {
        console.log("WebSocket connection closed");
        this.disconnect();
      };

    } catch (e: any) {
      console.error("Connection establish sequence failed:", e);
      this.onError(e.message || "Failed to initialize transport.");
      this.disconnect();
    }
  }

  /**
   * Run a single voice turn: acquire the mic, play a listening cue, capture
   * one VAD-segmented utterance, send it as a WAV-framed audio message.
   * The turn completes when the server sends turnComplete AND any TTS
   * playback has finished (see maybeCompleteVoiceTurn).
   */
  public async startVoiceTurn(): Promise<void> {
    if (this.voiceTurnActive) return;
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      throw new Error("Transport not connected.");
    }

    this.voiceTurnActive = true;
    this.turnCompleteReceived = false;
    this.setState("listening");

    try {
      const AudioContextClass = window.AudioContext || (window as any).webkitAudioContext;
      if (!AudioContextClass) {
        throw new Error("Web Audio API unavailable.");
      }
      this.inputAudioCtx = new AudioContextClass({ sampleRate: 16000 });
      if (this.inputAudioCtx.state === "suspended") {
        await this.inputAudioCtx.resume().catch(() => {});
      }

      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      if (!this.voiceTurnActive || !this.inputAudioCtx) {
        stream.getTracks().forEach((t) => { try { t.stop(); } catch {} });
        return;
      }
      this.micStream = stream;

      this.inputAnalyser = this.inputAudioCtx.createAnalyser();
      this.inputAnalyser.fftSize = 256;
      this.micSourceNode = this.inputAudioCtx.createMediaStreamSource(this.micStream);
      this.micSourceNode.connect(this.inputAnalyser);

      // Listening cue so the user knows when to speak (backend is up by now).
      this.playListeningCue();

      const utterance = await this.captureUtterance();
      // Mic is released as soon as capture ends — before the network round-trip.
      this.releaseCapture();

      if (!this.voiceTurnActive) return;

      if (utterance.length === 0) {
        // Nothing captured; end the turn quietly.
        this.completeVoiceTurn();
        return;
      }

      const base64 = wavToBase64(utterance, 16000);
      this.ws.send(JSON.stringify({ type: "audio", format: "wav", audio: base64 }));
      // Server now drives thinking -> (speaking) -> turnComplete.
    } catch (err: any) {
      console.error("Voice turn failed:", err);
      this.onError(err?.message || "Microphone unavailable.");
      this.completeVoiceTurn();
    }
  }

  /** Capture one utterance with energy-based end-of-speech detection. */
  private captureUtterance(): Promise<Float32Array> {
    return new Promise((resolve) => {
      const chunks: Float32Array[] = [];
      let speechStarted = false;
      let silenceMs = 0;
      const startedAt = Date.now();
      let lastChunkAt = Date.now();
      let finished = false;

      const finish = () => {
        if (finished) return;
        finished = true;
        try {
          if (this.micProcessorNode) this.micProcessorNode.onaudioprocess = null;
        } catch {}
        const total = chunks.reduce((n, c) => n + c.length, 0);
        const out = new Float32Array(total);
        let off = 0;
        for (const c of chunks) {
          out.set(c, off);
          off += c.length;
        }
        resolve(out);
      };

      const ctx = this.inputAudioCtx!;
      const source = this.micSourceNode!;
      this.micProcessorNode = ctx.createScriptProcessor(2048, 1, 1);
      source.connect(this.micProcessorNode);
      // ScriptProcessor requires a destination connection to run in some browsers.
      const mute = ctx.createGain();
      mute.gain.value = 0;
      this.micProcessorNode.connect(mute);
      mute.connect(ctx.destination);

      this.micProcessorNode.onaudioprocess = (e) => {
        if (finished || !this.voiceTurnActive) {
          finish();
          return;
        }
        const now = Date.now();
        const data = e.inputBuffer.getChannelData(0);
        const copy = new Float32Array(data);

        // RMS energy of this chunk.
        let sum = 0;
        for (let i = 0; i < copy.length; i++) sum += copy[i] * copy[i];
        const rms = Math.sqrt(sum / copy.length);
        const dt = now - lastChunkAt;
        lastChunkAt = now;

        if (rms >= VAD_START_THRESHOLD) {
          speechStarted = true;
          silenceMs = 0;
          chunks.push(copy);
        } else if (speechStarted) {
          chunks.push(copy);
          if (rms < VAD_END_THRESHOLD) {
            silenceMs += dt;
            if (silenceMs >= VAD_SILENCE_END_MS) {
              finish();
              return;
            }
          } else {
            silenceMs = 0;
          }
        }
        // Pre-speech audio is discarded; the listening cue tells the user
        // when capture is live, so nothing before it matters.

        if (now - startedAt >= VAD_MAX_TURN_MS) {
          finish();
        }
      };

      // Safety: never capture longer than the hard cap, even if the
      // processor stalls.
      setTimeout(() => finish(), VAD_MAX_TURN_MS + 2000);
    });
  }

  /** Soft single blip: "I'm listening now." */
  private playListeningCue(): void {
    try {
      if (!this.outputAudioCtx) return;
      const ctx = this.outputAudioCtx;
      const now = ctx.currentTime;
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = "sine";
      osc.frequency.value = 880;
      gain.gain.setValueAtTime(0.0001, now);
      gain.gain.exponentialRampToValueAtTime(0.12, now + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.15);
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start(now);
      osc.stop(now + 0.18);
    } catch {
      /* cue is best-effort */
    }
  }

  /** Release per-turn capture resources (mic tracks, input graph). */
  private releaseCapture(): void {
    if (this.micProcessorNode) {
      try {
        this.micProcessorNode.onaudioprocess = null;
        this.micProcessorNode.disconnect();
      } catch {}
      this.micProcessorNode = null;
    }
    if (this.micSourceNode) {
      try {
        this.micSourceNode.disconnect();
      } catch {}
      this.micSourceNode = null;
    }
    if (this.micStream) {
      this.micStream.getTracks().forEach((t) => { try { t.stop(); } catch {} });
      this.micStream = null;
    }
    if (this.inputAudioCtx) {
      try {
        this.inputAudioCtx.close();
      } catch {}
      this.inputAudioCtx = null;
    }
    this.inputAnalyser = null;
  }

  /**
   * Play a complete TTS audio blob from the server. The blob is decoded
   * format-agnostically (MP3/WAV/OGG) — the TTS provider returns whole
   * audio, not a PCM stream.
   */
  private async playAudioMessage(base64Audio: string): Promise<void> {
    if (!this.outputAudioCtx || !this.outputGainNode) return;
    try {
      this.setState("speaking");
      const bytes = base64ToUint8Array(base64Audio);
      // decodeAudioData detaches the buffer it decodes — decode a copy.
      const copy = new Uint8Array(bytes.byteLength);
      copy.set(bytes);
      const audioBuffer: AudioBuffer = await this.outputAudioCtx.decodeAudioData(copy.buffer);

      const source = this.outputAudioCtx.createBufferSource();
      source.buffer = audioBuffer;
      source.connect(this.outputGainNode);
      source.onended = () => {
        const i = this.activeSources.indexOf(source);
        if (i > -1) this.activeSources.splice(i, 1);
        this.maybeCompleteVoiceTurn();
      };
      this.activeSources.push(source);
      source.start();
    } catch (playbackError) {
      console.error("TTS playback failed:", playbackError);
      this.maybeCompleteVoiceTurn();
    }
  }

  /** End the turn once the server finished AND playback drained. */
  private maybeCompleteVoiceTurn(): void {
    if (!this.voiceTurnActive) return;
    if (this.turnCompleteReceived && this.activeSources.length === 0) {
      this.completeVoiceTurn();
    }
  }

  private completeVoiceTurn(): void {
    if (!this.voiceTurnActive) return;
    this.voiceTurnActive = false;
    this.turnCompleteReceived = false;
    this.releaseCapture();
    this.setState("idle");
    try {
      this.onVoiceTurnComplete?.();
    } catch {
      /* ignore */
    }
  }

  /** Public: abandon the current voice turn (e.g. window closing). */
  public endVoiceTurn(): void {
    this.completeVoiceTurn();
  }

  // Interruption: stop all active playback immediately (reserved for barge-in).
  private handleInterruption() {
    console.log("[Audio] Interruption signal received; stopping playback.");
    this.activeSources.forEach((source) => {
      try {
        source.stop();
      } catch {
        // Already finished or stopped
      }
    });
    this.activeSources = [];
    this.setState("listening");
  }

  // Fully cleanup and release connection sockets
  public disconnect() {
    const wasVoiceTurn = this.voiceTurnActive;
    this.isActivated = false;
    this.voiceTurnActive = false;
    this.turnCompleteReceived = false;
    this.releaseCapture();
    this.setState("disconnected");

    // Close WS socket
    if (this.ws) {
      try {
        this.ws.close();
      } catch (e) {}
      this.ws = null;
    }

    // Stop any playback
    this.activeSources.forEach((source) => {
      try {
        source.stop();
      } catch {}
    });

    // Close output context
    if (this.outputAudioCtx) {
      try {
        this.outputAudioCtx.close();
      } catch (e) {}
      this.outputAudioCtx = null;
    }

    this.activeSources = [];
    this.inputAnalyser = null;
    this.outputAnalyser = null;
    this.outputGainNode = null;

    if (wasVoiceTurn) {
      try {
        this.onVoiceTurnComplete?.();
      } catch {}
    }
  }
}

export { MambaAudioSession as ElysiaAudioSession };
