/**
 * Audio session for the Mamba desktop voice transport (/live WebSocket).
 *
 * Two session modes share one transport:
 *
 * One-turn (startVoiceTurn): acquire mic -> listening cue -> VAD-segmented
 * capture -> ONE WAV-framed utterance sent as {type:"audio", format:"wav"}.
 * The server transcribes (STT) -> MambaRuntime -> speaks the reply back as a
 * complete audio blob. Playback end + turnComplete -> the turn ends and the
 * mic is released.
 *
 * Continuous (startContinuousSession, Phase 8): the mic is acquired ONCE and
 * held for the session. A session loop VAD-segments utterances and sends
 * each through the same transport; after each turn's TTS playback drains,
 * the session returns to LISTENING without user action. While TTS is
 * playing, a barge-in watcher listens for sustained user speech: on
 * detection it stops playback immediately and captures the new utterance.
 * stopContinuousSession() (or the mic toggle) releases everything.
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

// --- Continuous session + barge-in tuning (Phase 8) ---
// Barge-in listens for sustained speech while TTS is playing. The threshold
// sits above the capture VAD so speaker echo (after echo cancellation) does
// not false-trigger; the streak requires ~400ms of sustained energy and the
// grace period skips the TTS onset transient.
const BARGE_IN_RMS_THRESHOLD = 0.05;
const BARGE_IN_POLL_MS = 100;
const BARGE_IN_STREAK = 4;
const BARGE_IN_COOLDOWN_MS = 2500;
const BARGE_IN_PLAYBACK_GRACE_MS = 800;
// Quiet capture cycles before an idle continuous session ends itself
// (~20s per cycle -> ~5 minutes of silence).
const CONTINUOUS_IDLE_TURNS = 15;

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
  // TTS audio messages received but not yet playing (decode in flight).
  // Prevents turnComplete from ending the turn while decodeAudioData runs.
  private pendingAudioMessages = 0;
  private playbackStartedAt = 0;

  // Voice-turn lifecycle
  private voiceTurnActive = false;
  private turnCompleteReceived = false;

  // Continuous session (Phase 8): mic held open across turns; barge-in
  // allowed while TTS is playing. One-turn mode leaves these untouched.
  private continuousActive = false;
  private serverTurnActive = false;
  private bargedIn = false;
  private staleCompletionPending = false;
  private sessionEndNotified = false;
  private idleTurns = 0;
  private turnEndWaiters: Array<(bargedIn: boolean) => void> = [];
  private bargeInTimer: number | null = null;
  private bargeInStreak = 0;
  private bargeInCooldownUntil = 0;

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
            this.pendingAudioMessages++;
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
      if (!(await this.acquireCapture())) return;

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

      this.sendUtterance(utterance);
      // Server now drives thinking -> (speaking) -> turnComplete.
    } catch (err: any) {
      console.error("Voice turn failed:", err);
      this.onError(err?.message || "Microphone unavailable.");
      this.completeVoiceTurn();
    }
  }

  /**
   * Start a continuous voice session (Phase 8): the mic is acquired ONCE
   * and held open. The session loop VAD-segments utterances and sends each
   * through the normal transport; after every turn the session returns to
   * LISTENING automatically. Barge-in is armed while TTS plays. End with
   * stopContinuousSession() (mic toggle) — a long silence also ends it.
   */
  public async startContinuousSession(): Promise<void> {
    if (this.continuousActive || this.voiceTurnActive) return;
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      throw new Error("Transport not connected.");
    }

    this.continuousActive = true;
    this.voiceTurnActive = true;
    this.turnCompleteReceived = false;
    this.sessionEndNotified = false;
    this.idleTurns = 0;
    this.setState("listening");

    try {
      if (!(await this.acquireCapture())) {
        this.continuousActive = false;
        this.voiceTurnActive = false;
        return;
      }
    } catch (err: any) {
      this.continuousActive = false;
      this.voiceTurnActive = false;
      this.setState("error");
      this.onError(err?.message || "Microphone unavailable.");
      return;
    }

    this.playListeningCue();
    this.startBargeInWatcher();
    // The loop runs in the background until stopContinuousSession(); any
    // unexpected failure ends the session safely instead of hanging.
    this.runSessionLoop().catch((err) => {
      console.error("Voice session loop failed:", err);
      this.finishContinuousSession();
    });
  }

  /** End a continuous voice session and release all audio resources. */
  public stopContinuousSession(): void {
    if (!this.continuousActive) return;
    this.finishContinuousSession();
  }

  public isContinuousSessionActive(): boolean {
    return this.continuousActive;
  }

  /** The session loop: capture -> send -> await turn end -> listen again. */
  private async runSessionLoop(): Promise<void> {
    while (this.continuousActive) {
      this.setState("listening");
      let utterance: Float32Array;
      try {
        utterance = await this.captureUtterance();
      } catch {
        break;
      }
      if (!this.continuousActive) break;

      if (utterance.length === 0) {
        // Quiet cycle; keep listening, but don't hold the mic forever.
        this.idleTurns++;
        if (this.idleTurns >= CONTINUOUS_IDLE_TURNS) {
          console.log("[Audio] Voice session idle timeout; ending session.");
          break;
        }
        continue;
      }
      this.idleTurns = 0;

      // Begin the server turn for this utterance.
      this.serverTurnActive = true;
      this.turnCompleteReceived = false;
      this.setState("thinking");
      try {
        this.sendUtterance(utterance);
      } catch (err: any) {
        console.error("Failed to send utterance:", err);
        this.onError("Failed to send audio.");
        this.serverTurnActive = false;
        continue;
      }

      // Resolves when the turn completes (turnComplete + playback drained)
      // or when barge-in fires. The mic stays open either way.
      await this.waitForTurnEnd();
      this.serverTurnActive = false;
      if (!this.continuousActive) break;
      // Loop continues -> LISTENING (set at the top).
    }
    this.finishContinuousSession();
  }

  /** Resolve when the current server turn ends; true if it was barged-in. */
  private waitForTurnEnd(): Promise<boolean> {
    return new Promise((resolve) => {
      this.turnEndWaiters.push(resolve);
    });
  }

  private resolveTurnEnd(bargedIn: boolean): void {
    const waiters = this.turnEndWaiters;
    this.turnEndWaiters = [];
    for (const w of waiters) {
      try {
        w(bargedIn);
      } catch {
        /* ignore */
      }
    }
  }

  /** Idempotent teardown for a continuous session. */
  private finishContinuousSession(): void {
    const wasActive = this.continuousActive || this.voiceTurnActive;
    this.continuousActive = false;
    this.voiceTurnActive = false;
    this.serverTurnActive = false;
    this.bargedIn = false;
    this.turnCompleteReceived = false;
    this.staleCompletionPending = false;
    this.stopBargeInWatcher();
    // Unblock the session loop wherever it is; capture aborts via the
    // voiceTurnActive flag in the processor callback.
    this.resolveTurnEnd(false);
    this.stopPlayback();
    this.releaseCapture();
    this.setState("idle");
    if (wasActive && !this.sessionEndNotified) {
      this.sessionEndNotified = true;
      try {
        this.onVoiceTurnComplete?.();
      } catch {
        /* ignore */
      }
    }
  }

  /** Acquire the mic + input graph. Returns false if aborted mid-acquire. */
  private async acquireCapture(): Promise<boolean> {
    const AudioContextClass = window.AudioContext || (window as any).webkitAudioContext;
    if (!AudioContextClass) {
      throw new Error("Web Audio API unavailable.");
    }
    const ctx = new AudioContextClass({ sampleRate: 16000 });
    this.inputAudioCtx = ctx;
    if (ctx.state === "suspended") {
      await ctx.resume().catch(() => {});
    }

    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });
    if (!this.voiceTurnActive || this.inputAudioCtx !== ctx) {
      stream.getTracks().forEach((t) => {
        try {
          t.stop();
        } catch {}
      });
      return false;
    }
    this.micStream = stream;

    this.inputAnalyser = ctx.createAnalyser();
    this.inputAnalyser.fftSize = 256;
    this.micSourceNode = ctx.createMediaStreamSource(this.micStream);
    this.micSourceNode.connect(this.inputAnalyser);
    return true;
  }

  /** Send one VAD-segmented utterance as a WAV-framed audio message. */
  private sendUtterance(utterance: Float32Array): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      throw new Error("Transport not connected.");
    }
    const base64 = wavToBase64(utterance, 16000);
    this.ws.send(JSON.stringify({ type: "audio", format: "wav", audio: base64 }));
  }

  /** Stop all TTS playback immediately (barge-in / session end). */
  private stopPlayback(): void {
    for (const source of this.activeSources) {
      try {
        source.stop();
      } catch {
        // Already finished or stopped
      }
    }
    this.activeSources = [];
    // NOTE: each stopped source's onended still fires -> maybeCompleteVoiceTurn.
    // After barge-in the turn is already resolved, so those are no-ops.
  }

  // --- Barge-in (Phase 8): sustained user speech while TTS is playing ---

  private startBargeInWatcher(): void {
    this.stopBargeInWatcher();
    this.bargeInStreak = 0;
    this.bargeInCooldownUntil = 0;
    this.bargeInTimer = window.setInterval(() => this.bargeInTick(), BARGE_IN_POLL_MS);
  }

  private stopBargeInWatcher(): void {
    if (this.bargeInTimer !== null) {
      clearInterval(this.bargeInTimer);
      this.bargeInTimer = null;
    }
    this.bargeInStreak = 0;
  }

  private bargeInTick(): void {
    if (!this.continuousActive || !this.serverTurnActive) {
      this.bargeInStreak = 0;
      return;
    }
    // Only while TTS is actually playing.
    if (this.activeSources.length === 0) {
      this.bargeInStreak = 0;
      return;
    }
    const now = Date.now();
    if (now < this.bargeInCooldownUntil) return;
    if (now - this.playbackStartedAt < BARGE_IN_PLAYBACK_GRACE_MS) return;
    const rms = this.readMicRms();
    if (rms >= BARGE_IN_RMS_THRESHOLD) {
      this.bargeInStreak++;
      if (this.bargeInStreak >= BARGE_IN_STREAK) {
        this.bargeInStreak = 0;
        this.bargeInCooldownUntil = now + BARGE_IN_COOLDOWN_MS;
        this.triggerBargeIn();
      }
    } else {
      this.bargeInStreak = 0;
    }
  }

  private readMicRms(): number {
    try {
      if (!this.inputAnalyser) return 0;
      const buf = new Float32Array(this.inputAnalyser.fftSize);
      this.inputAnalyser.getFloatTimeDomainData(buf);
      let sum = 0;
      for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
      return Math.sqrt(sum / buf.length);
    } catch {
      return 0;
    }
  }

  private triggerBargeIn(): void {
    if (!this.continuousActive || !this.serverTurnActive) return;
    console.log("[Audio] Barge-in detected: stopping TTS playback.");
    // If the server's turnComplete hasn't arrived yet, its eventual arrival
    // is stale — swallow it so it cannot end the replacement turn.
    this.staleCompletionPending = !this.turnCompleteReceived;
    this.bargedIn = true;
    this.stopPlayback();
    this.setState("listening");
    // End the current server turn as barged-in; the session loop captures
    // the interrupting speech immediately.
    this.completeVoiceTurn();
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
          if (this.micProcessorNode) {
            this.micProcessorNode.onaudioprocess = null;
            // Disconnect the per-utterance processor so continuous sessions
            // don't accumulate dead nodes across turns.
            this.micProcessorNode.disconnect();
          }
        } catch {}
        this.micProcessorNode = null;
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
    const settleAudioMessage = () => {
      this.pendingAudioMessages = Math.max(0, this.pendingAudioMessages - 1);
    };
    if (!this.outputAudioCtx || !this.outputGainNode) {
      settleAudioMessage();
      this.maybeCompleteVoiceTurn();
      return;
    }
    // A stale blob (turn ended while it was in flight, e.g. the session
    // was stopped mid-turn) must not start playing over the next turn.
    if (!this.voiceTurnActive) {
      settleAudioMessage();
      return;
    }
    try {
      this.setState("speaking");
      const bytes = base64ToUint8Array(base64Audio);
      // decodeAudioData detaches the buffer it decodes — decode a copy.
      const copy = new Uint8Array(bytes.byteLength);
      copy.set(bytes);
      const audioBuffer: AudioBuffer = await this.outputAudioCtx.decodeAudioData(copy.buffer);

      // The turn may have ended (barge-in / stop) while decoding.
      if (!this.voiceTurnActive) {
        settleAudioMessage();
        return;
      }

      const source = this.outputAudioCtx.createBufferSource();
      source.buffer = audioBuffer;
      source.connect(this.outputGainNode);
      source.onended = () => {
        const i = this.activeSources.indexOf(source);
        if (i > -1) this.activeSources.splice(i, 1);
        settleAudioMessage();
        this.maybeCompleteVoiceTurn();
      };
      this.activeSources.push(source);
      source.start();
      this.playbackStartedAt = Date.now();
    } catch (playbackError) {
      console.error("TTS playback failed:", playbackError);
      settleAudioMessage();
      this.maybeCompleteVoiceTurn();
    }
  }

  /** End the turn once the server finished AND playback drained. */
  private maybeCompleteVoiceTurn(): void {
    if (!this.voiceTurnActive) return;
    if (
      this.turnCompleteReceived &&
      this.activeSources.length === 0 &&
      this.pendingAudioMessages === 0
    ) {
      if (this.staleCompletionPending) {
        // Completion for a barged-in (abandoned) turn; swallow it so it
        // cannot end the turn that replaced it.
        this.staleCompletionPending = false;
        this.turnCompleteReceived = false;
        return;
      }
      this.completeVoiceTurn();
    }
  }

  private completeVoiceTurn(): void {
    if (!this.voiceTurnActive) return;
    if (this.continuousActive) {
      // End of one utterance's server turn. The session loop keeps the mic
      // open and returns to LISTENING; per-turn completion must NOT release
      // capture or notify session end.
      const wasBargeIn = this.bargedIn;
      const wasServerTurn = this.serverTurnActive;
      this.bargedIn = false;
      this.serverTurnActive = false;
      this.turnCompleteReceived = false;
      this.resolveTurnEnd(wasBargeIn);
      if (!wasServerTurn) {
        // turnComplete for a typed-chat turn that overlapped voice capture —
        // the session loop is listening, so restore the state the text
        // turn's "thinking" status overrode.
        this.setState("listening");
      }
      return;
    }
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
    if (this.continuousActive) {
      this.stopContinuousSession();
      return;
    }
    this.completeVoiceTurn();
  }

  // Interruption: stop all active playback immediately (server-sent).
  private handleInterruption() {
    console.log("[Audio] Interruption signal received; stopping playback.");
    this.stopPlayback();
    this.setState("listening");
    if (this.continuousActive) {
      // The server abandoned the turn; wake the session loop so it goes
      // back to listening instead of waiting on a dead turn.
      this.bargedIn = true;
      this.completeVoiceTurn();
    }
  }

  // Fully cleanup and release connection sockets
  public disconnect() {
    this.isActivated = false;
    // Tear down any voice turn/session first (idempotent; fires
    // onVoiceTurnComplete once if a turn or session was active).
    this.finishContinuousSession();
    this.setState("disconnected");

    // Close WS socket
    if (this.ws) {
      try {
        this.ws.close();
      } catch (e) {}
      this.ws = null;
    }

    // Close output context
    if (this.outputAudioCtx) {
      try {
        this.outputAudioCtx.close();
      } catch (e) {}
      this.outputAudioCtx = null;
    }

    this.inputAnalyser = null;
    this.outputAnalyser = null;
    this.outputGainNode = null;
  }
}

export { MambaAudioSession as ElysiaAudioSession };
