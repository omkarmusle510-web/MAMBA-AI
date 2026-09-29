/**
 * INTERIM wake-word engine backend: browser Web Speech API.
 *
 * This is a stopgap, NOT the target implementation. It is kept so the
 * end-to-end one-turn voice pipeline (wake → activation → capture → STT →
 * MambaRuntime → TTS) can be proven before committing to a wake-word
 * technology.
 *
 * EVALUATION GATE — do NOT introduce a model asset (e.g. WASM/ONNX keyword
 * spotter) until ALL of the following are established for the exact phrase
 * "Hey Mamba":
 *   1. Model availability — a usable pre-trained model exists (no custom
 *      training project in disguise).
 *   2. Licensing — the model + runtime are cleared for bundling.
 *   3. Electron/Chromium compatibility — runs in the desktop shell's
 *      renderer (not just Chrome).
 *   4. CPU usage — acceptable while DORMANT (target: low single-digit %).
 *   5. Accuracy — measured false-accept / false-reject on the phrase.
 *
 * Known limitations of this interim backend:
 * - Sends audio to the OS/browser vendor's speech service (not fully local).
 * - webkitSpeechRecognition is unreliable or unavailable inside Electron
 *   builds; this backend is primarily useful for browser-dev validation.
 */
import type { WakeEngine, WakeEngineOptions, WakeEngineState } from "./types";

// Minimal typed shim for the unprefixed SpeechRecognition API.
interface SpeechRecognitionLike {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  maxAlternatives: number;
  start(): void;
  stop(): void;
  abort(): void;
  onresult: ((e: any) => void) | null;
  onerror: ((e: any) => void) | null;
  onend: (() => void) | null;
  onstart: (() => void) | null;
}

type SpeechRecognitionCtor = new () => SpeechRecognitionLike;

function getSpeechRecognitionCtor(): SpeechRecognitionCtor | null {
  if (typeof window === "undefined") return null;
  const w = window as any;
  return (w.SpeechRecognition || w.webkitSpeechRecognition || null) as
    | SpeechRecognitionCtor
    | null;
}

export class WebSpeechWakeEngine implements WakeEngine {
  private recognition: SpeechRecognitionLike | null = null;
  private ctor: SpeechRecognitionCtor | null;
  private phrase = "hey mamba";
  private sensitivity = 60;
  private onTriggered: (() => void) | null = null;
  private onState: ((s: WakeEngineState) => void) | null = null;

  private intended = false;
  private active = false;
  private lastTrigger = 0;
  private debounceMs = 4000;
  private restartTimer: ReturnType<typeof setTimeout> | null = null;
  private consecutiveErrors = 0;

  constructor() {
    this.ctor = getSpeechRecognitionCtor();
  }

  isSupported(): boolean {
    return getSpeechRecognitionCtor() !== null;
  }

  get isActive(): boolean {
    return this.active;
  }

  start(opts: WakeEngineOptions): boolean {
    if (!this.ctor) {
      this.setState("error");
      return false;
    }
    this.phrase = (opts.phrase || "hey mamba").toLowerCase().trim();
    this.sensitivity = opts.sensitivity ?? this.sensitivity;
    this.onTriggered = opts.onTriggered ?? null;
    this.onState = opts.onState ?? null;
    // sensitivity 0..100 -> debounce 7000ms..1500ms (higher sens = faster re-arm)
    this.debounceMs = Math.round(7000 - (this.sensitivity / 100) * 5500);
    this.intended = true;
    this.consecutiveErrors = 0;
    this.launch();
    return true;
  }

  stop(): void {
    this.intended = false;
    if (this.restartTimer) {
      clearTimeout(this.restartTimer);
      this.restartTimer = null;
    }
    this.teardown();
    this.setState("stopped");
  }

  setPhrase(phrase: string): void {
    this.phrase = (phrase || "hey mamba").toLowerCase().trim();
  }

  setSensitivity(value: number): void {
    this.sensitivity = Math.max(0, Math.min(100, value));
    this.debounceMs = Math.round(7000 - (this.sensitivity / 100) * 5500);
  }

  private launch(): void {
    if (!this.ctor || !this.intended) return;
    this.teardown();
    try {
      const rec = new this.ctor();
      rec.continuous = true;
      rec.interimResults = true;
      rec.lang = "en-US";
      rec.maxAlternatives = 3;

      rec.onstart = () => {
        this.consecutiveErrors = 0;
        this.active = true;
        this.setState("listening");
      };

      rec.onresult = (e: any) => {
        for (let i = e.resultIndex; i < e.results.length; i++) {
          const res = e.results[i];
          if (!res) continue;
          for (let j = 0; j < res.length; j++) {
            const transcript = (res[j]?.transcript || "").toString().toLowerCase();
            if (transcript.includes(this.phrase)) {
              this.fire();
              return;
            }
          }
        }
      };

      rec.onerror = (e: any) => {
        const err = e?.error || "unknown";
        if (err === "no-speech" || err === "aborted") return;
        this.consecutiveErrors++;
        this.setState("error");
      };

      rec.onend = () => {
        this.active = false;
        if (!this.intended) return;
        const delay = Math.min(1000 * this.consecutiveErrors * 2, 15000);
        this.restartTimer = setTimeout(() => this.launch(), Math.max(150, delay));
      };

      this.recognition = rec;
      rec.start();
    } catch {
      this.setState("error");
      this.restartTimer = setTimeout(() => this.launch(), 1000);
    }
  }

  private teardown(): void {
    if (this.recognition) {
      try {
        this.recognition.onresult = null;
        this.recognition.onerror = null;
        this.recognition.onend = null;
        this.recognition.onstart = null;
        this.recognition.abort();
      } catch {
        /* ignore */
      }
      this.recognition = null;
    }
    this.active = false;
  }

  private fire(): void {
    const now = Date.now();
    if (now - this.lastTrigger < this.debounceMs) return;
    this.lastTrigger = now;
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
      const Ctx = (window as any).AudioContext || (window as any).webkitAudioContext;
      if (!Ctx) return;
      const ctx: AudioContext = new Ctx();
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
      setTimeout(() => ctx.close().catch(() => {}), 600);
    } catch {
      /* audio is best-effort */
    }
  }

  private setState(s: WakeEngineState): void {
    try {
      this.onState?.(s);
    } catch {
      /* ignore */
    }
  }
}
