/**
 * LOCAL wake-word engine backend: sherpa-onnx keyword spotting (prototype).
 *
 * Architecture: the sherpa-onnx WASM build requires Emscripten NODERAWFS,
 * which is only supported in a Node.js environment — it cannot initialize
 * inside the Chromium renderer (nodeIntegration: false). So the spotter
 * itself runs in the Electron MAIN process (electron/wakeKws.cjs, plain
 * Node.js). This renderer-side engine only:
 *   1. captures microphone audio locally (16 kHz),
 *   2. streams PCM chunks to main over IPC,
 *   3. receives "wake-kws-detected" from main and fires the trigger.
 *
 * Fully offline: pinned int8 Zipformer model, no cloud, no API key, no
 * network. See public/wake/VERSIONS.md for pinned artifacts.
 *
 * Audio: microphone -> 16 kHz AudioContext -> ScriptProcessor pump ->
 * IPC to main. Nothing is recorded, persisted, or sent anywhere else.
 * The mic is released when the engine stops (e.g. on trigger, when the
 * Phase A voice session takes over).
 *
 * Prototype scope: only the "hey mamba" phrase is supported. Any other
 * configured phrase reports an explicit error (no silent fallback).
 */
import type { WakeEngine, WakeEngineOptions, WakeEngineState } from "./types";
import { wakeDiag } from "./diag";

const SUPPORTED_PHRASE = "hey mamba";
const SAMPLE_RATE = 16000;
const PUMP_CHUNK = 4096; // ~256 ms at 16 kHz
const TRIGGER_DEBOUNCE_MS = 4000;
// A live analog mic never yields exact-zero PCM for long. ~10.2 s of exact
// zeros means the device went dead (seen with Bluetooth headsets after
// sleep); re-acquire instead of listening to a mute stream forever.
const ZERO_RUN_CHUNKS = 40;
const REACQUIRE_COOLDOWN_MS = 60000;

export class SherpaOnnxWakeEngine implements WakeEngine {
  private phrase = SUPPORTED_PHRASE;
  private sensitivity = 60;
  private onTriggered: (() => void) | null = null;
  private onState: ((s: WakeEngineState) => void) | null = null;

  private intended = false;
  private active = false;
  private lastTrigger = 0;

  private removeDetectionListener: (() => void) | null = null;

  private micStream: MediaStream | null = null;
  private audioCtx: AudioContext | null = null;
  private sourceNode: MediaStreamAudioSourceNode | null = null;
  private processorNode: ScriptProcessorNode | null = null;
  private zeroRun = 0;
  private lastReacquire = 0;
  private reacquiring = false;

  isSupported(): boolean {
    if (typeof window === "undefined") return false;
    const w = window as any;
    return (
      !!navigator?.mediaDevices?.getUserMedia &&
      !!(w.AudioContext || w.webkitAudioContext) &&
      typeof window.mambaDesktop?.wakeKwsInit === "function"
    );
  }

  get isActive(): boolean {
    return this.active;
  }

  async start(opts: WakeEngineOptions): Promise<boolean> {
    const phrase = (opts.phrase || SUPPORTED_PHRASE).toLowerCase().trim();
    this.sensitivity = opts.sensitivity ?? this.sensitivity;
    this.onTriggered = opts.onTriggered ?? null;
    this.onState = opts.onState ?? null;

    wakeDiag(`sherpa engine.start() called (phrase="${phrase}", sensitivity=${this.sensitivity})`);

    // Prototype supports exactly one phrase — never pretend otherwise.
    if (phrase !== SUPPORTED_PHRASE) {
      wakeDiag(
        `sherpa engine.start() FAILED: phrase "${phrase}" not supported in prototype (only "${SUPPORTED_PHRASE}")`
      );
      this.setState("error");
      return false;
    }
    this.phrase = phrase;

    if (!this.isSupported()) {
      wakeDiag(`sherpa engine.start() FAILED: mic/AudioContext or main-process KWS bridge unavailable`);
      this.setState("error");
      return false;
    }

    this.intended = true;
    this.setState("loading");
    try {
      // The spotter runs in the main process (Node). Init is lazy and takes
      // ~2s the first time (WASM + model load); instant on re-arm.
      wakeDiag(`sherpa engine: requesting KWS init in main process ...`);
      const res = await window.mambaDesktop!.wakeKwsInit!({ threshold: this.threshold() });
      if (!this.intended) return false; // stopped during init
      if (!res || res.ok !== true) {
        throw new Error(`main-process KWS init failed: ${(res && res.error) || "unknown error"}`);
      }
      wakeDiag(`sherpa engine: main-process KWS READY`);

      this.removeDetectionListener = window.mambaDesktop!.onWakeKwsDetected!((keyword) =>
        this.handleRemoteDetection(keyword)
      );

      await this.startMic();
      if (!this.intended) return false;
      this.active = true;
      this.setState("listening");
      wakeDiag(`sherpa engine: listening for "${this.phrase}" (mic -> main-process KWS)`);
      return true;
    } catch (err: any) {
      wakeDiag(`sherpa engine.start() FAILED: ${err?.message || err}`);
      this.teardown();
      this.setState("error");
      return false;
    }
  }

  stop(): void {
    wakeDiag(`sherpa engine.stop() called`);
    this.intended = false;
    if (this.removeDetectionListener) {
      try {
        this.removeDetectionListener();
      } catch {}
      this.removeDetectionListener = null;
    }
    try {
      window.mambaDesktop?.wakeKwsStop?.();
    } catch {}
    this.teardown();
    this.setState("stopped");
  }

  setPhrase(phrase: string): void {
    this.phrase = (phrase || SUPPORTED_PHRASE).toLowerCase().trim();
  }

  setSensitivity(value: number): void {
    this.sensitivity = Math.max(0, Math.min(100, value));
  }

  /**
   * sensitivity 0..100 -> KWS threshold 0.09 (strict) .. 0.005 (loose).
   * Calibrated on real-mic captures: the default sensitivity 60 -> 0.03,
   * which detects the real user's "hey mamba" (scores 0.04-0.06) while
   * non-phrase speech stays < 0.001.
   */
  private threshold(): number {
    return Math.max(0.005, 0.09 - this.sensitivity * 0.001);
  }

  private async startMic(): Promise<void> {
    const w = window as any;
    const Ctx = w.AudioContext || w.webkitAudioContext;
    const audioCtx: AudioContext = new Ctx({ sampleRate: SAMPLE_RATE });
    this.audioCtx = audioCtx;
    if (audioCtx.state === "suspended") {
      await audioCtx.resume().catch(() => {});
    }

    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: false,
        noiseSuppression: false,
        autoGainControl: false,
        channelCount: 1,
        sampleRate: SAMPLE_RATE,
      },
    });
    if (!this.intended || this.audioCtx !== audioCtx) {
      stream.getTracks().forEach((t) => {
        try {
          t.stop();
        } catch {}
      });
      throw new Error("stopped while acquiring microphone");
    }
    this.micStream = stream;

    this.sourceNode = audioCtx.createMediaStreamSource(this.micStream);
    this.processorNode = audioCtx.createScriptProcessor(PUMP_CHUNK, 1, 1);
    this.sourceNode.connect(this.processorNode);
    // ScriptProcessor needs a destination connection to run; keep it muted.
    const mute = audioCtx.createGain();
    mute.gain.value = 0;
    this.processorNode.connect(mute);
    mute.connect(audioCtx.destination);

    this.zeroRun = 0;
    this.processorNode.onaudioprocess = (e) => {
      if (!this.intended) return;
      const data = e.inputBuffer.getChannelData(0);
      let nonzero = false;
      for (let i = 0; i < data.length; i++) {
        if (data[i] !== 0) {
          nonzero = true;
          break;
        }
      }
      if (nonzero) {
        this.zeroRun = 0;
      } else if (++this.zeroRun >= ZERO_RUN_CHUNKS) {
        this.maybeReacquireMic();
      }
      // Stream PCM to the main-process spotter (fire-and-forget IPC).
      try {
        window.mambaDesktop?.wakeKwsAudioChunk?.(new Float32Array(data));
      } catch (err: any) {
        wakeDiag(`sherpa engine: audio chunk send failed: ${err?.message || err}`);
      }
    };
    wakeDiag(`sherpa engine: microphone acquired, streaming to main process`);
  }

  private maybeReacquireMic(): void {
    const now = Date.now();
    if (this.reacquiring || !this.intended) return;
    if (now - this.lastReacquire < REACQUIRE_COOLDOWN_MS) return;
    this.reacquiring = true;
    this.lastReacquire = now;
    this.zeroRun = 0;
    void this.reacquireMic();
  }

  /** Swap in a fresh MediaStream after a dead-stream (digital silence)
   *  stretch; the AudioContext and the KWS pipeline stay untouched. */
  private async reacquireMic(): Promise<void> {
    const ctx = this.audioCtx;
    wakeDiag(`sherpa engine: ~10s of exact-zero PCM from mic, re-acquiring`);
    try {
      const fresh = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: false,
          noiseSuppression: false,
          autoGainControl: false,
          channelCount: 1,
          sampleRate: SAMPLE_RATE,
        },
      });
      if (!this.intended || this.audioCtx !== ctx || !this.processorNode) {
        fresh.getTracks().forEach((t) => {
          try {
            t.stop();
          } catch {}
        });
        return;
      }
      const old = this.micStream;
      if (this.sourceNode) {
        try {
          this.sourceNode.disconnect();
        } catch {}
      }
      if (old) {
        old.getTracks().forEach((t) => {
          try {
            t.stop();
          } catch {}
        });
      }
      this.micStream = fresh;
      this.sourceNode = ctx!.createMediaStreamSource(fresh);
      this.sourceNode.connect(this.processorNode);
      wakeDiag(`sherpa engine: microphone re-acquired`);
    } catch (err: any) {
      wakeDiag(`sherpa engine: microphone re-acquire failed: ${err?.message || err}`);
    } finally {
      this.reacquiring = false;
    }
  }

  /** A detection arrived from the main-process spotter. */
  private handleRemoteDetection(keyword: string): void {
    if (!this.intended) return;
    wakeDiag(`sherpa engine: main process detected keyword "${keyword}"`);
    this.fire();
  }

  private fire(): void {
    const now = Date.now();
    if (now - this.lastTrigger < TRIGGER_DEBOUNCE_MS) {
      wakeDiag(`sherpa TRIGGER suppressed by debounce`);
      return;
    }
    this.lastTrigger = now;
    wakeDiag(`sherpa TRIGGER: notifying controller`);
    this.playActivationSound();
    this.setState("triggered");
    try {
      this.onTriggered?.();
    } catch {
      /* never let a handler error kill the detector */
    }
  }

  /** Soft two-tone chime synthesized via Web Audio (no asset needed). */
  private playActivationSound(): void {
    try {
      const ctx = this.audioCtx;
      if (!ctx) return;
      const now = ctx.currentTime;
      const notes = [
        { f: 660, t: 0 },
        { f: 880, t: 0.12 },
      ];
      notes.forEach(({ f, t }) => {
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.type = "sine";
        osc.frequency.value = f;
        gain.gain.setValueAtTime(0.0001, now + t);
        gain.gain.exponentialRampToValueAtTime(0.18, now + t + 0.02);
        gain.gain.exponentialRampToValueAtTime(0.0001, now + t + 0.18);
        osc.connect(gain);
        gain.connect(ctx.destination);
        osc.start(now + t);
        osc.stop(now + t + 0.2);
      });
    } catch {
      /* audio is best-effort */
    }
  }

  private teardown(): void {
    this.active = false;
    if (this.processorNode) {
      try {
        this.processorNode.onaudioprocess = null;
        this.processorNode.disconnect();
      } catch {}
      this.processorNode = null;
    }
    if (this.sourceNode) {
      try {
        this.sourceNode.disconnect();
      } catch {}
      this.sourceNode = null;
    }
    if (this.micStream) {
      this.micStream.getTracks().forEach((t) => {
        try {
          t.stop();
        } catch {}
      });
      this.micStream = null;
    }
    if (this.audioCtx) {
      try {
        this.audioCtx.close();
      } catch {}
      this.audioCtx = null;
    }
  }

  private setState(s: WakeEngineState): void {
    wakeDiag(`sherpa engine state -> ${s}`);
    try {
      this.onState?.(s);
    } catch {
      /* ignore */
    }
  }
}
