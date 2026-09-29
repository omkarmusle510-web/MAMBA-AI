/**
 * LOCAL wake-word engine backend: sherpa-onnx keyword spotting (prototype).
 *
 * Fully offline: a streaming Zipformer KWS model (int8, ~6 MB) runs inside
 * the Electron renderer via the vendored sherpa-onnx WASM build. No cloud
 * speech service, no Google API, no API key, no network.
 *
 * Pinned artifacts: see public/wake/VERSIONS.md
 *   - sherpa-onnx npm 1.13.8 (JS glue + WASM vendored under src/wake/sherpa/)
 *   - sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01 (English, int8)
 *   - keywords.txt: "HEY MAMBA" -> BPE tokens via documented text2token
 *
 * Audio: microphone -> 16 kHz AudioContext -> ScriptProcessor pump ->
 * stream.acceptWaveform(). Nothing is recorded, persisted, or sent anywhere.
 * The mic is released when the engine stops (e.g. on trigger, when the
 * Phase A voice session takes over).
 *
 * Prototype scope: only the "hey mamba" phrase is supported. Any other
 * configured phrase reports an explicit error (no silent fallback).
 */
import type { WakeEngine, WakeEngineOptions, WakeEngineState } from "./types";
import { wakeDiag } from "./diag";

// Vendored sherpa-onnx 1.13.8 (no type declarations; ESM-adapted, see file headers).
// @ts-ignore: vendored JS without types
import createSherpaModule from "./sherpa/sherpa-onnx-wasm-nodejs.js";
// @ts-ignore: vendored JS without types
import { createKws } from "./sherpa/sherpa-onnx-kws.js";
// @ts-ignore: vendored JS without types
import pathPosix from "./sherpa/path-browserify.js";

const SUPPORTED_PHRASE = "hey mamba";
const SAMPLE_RATE = 16000;
const PUMP_CHUNK = 4096; // ~256 ms at 16 kHz
const TRIGGER_DEBOUNCE_MS = 4000;

const ASSET = {
  wasm: "wake/sherpa-onnx-wasm-nodejs.wasm",
  encoder: "wake/kws/encoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx",
  decoder: "wake/kws/decoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx",
  joiner: "wake/kws/joiner-epoch-12-avg-2-chunk-16-left-64.int8.onnx",
  tokens: "wake/kws/tokens.txt",
  keywords: "wake/kws/keywords.txt",
};

async function fetchBytes(url: string): Promise<Uint8Array> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`fetch ${url} -> HTTP ${res.status}`);
  return new Uint8Array(await res.arrayBuffer());
}

async function fetchText(url: string): Promise<string> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`fetch ${url} -> HTTP ${res.status}`);
  return await res.text();
}

export class SherpaOnnxWakeEngine implements WakeEngine {
  private phrase = SUPPORTED_PHRASE;
  private sensitivity = 60;
  private onTriggered: (() => void) | null = null;
  private onState: ((s: WakeEngineState) => void) | null = null;

  private intended = false;
  private active = false;
  private lastTrigger = 0;

  private Module: any = null;
  private kws: any = null;
  private stream: any = null;

  private micStream: MediaStream | null = null;
  private audioCtx: AudioContext | null = null;
  private sourceNode: MediaStreamAudioSourceNode | null = null;
  private processorNode: ScriptProcessorNode | null = null;

  isSupported(): boolean {
    if (typeof window === "undefined") return false;
    const w = window as any;
    return (
      typeof WebAssembly !== "undefined" &&
      !!navigator?.mediaDevices?.getUserMedia &&
      !!(w.AudioContext || w.webkitAudioContext)
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
      wakeDiag(`sherpa engine.start() FAILED: WebAssembly/getUserMedia/AudioContext unavailable`);
      this.setState("error");
      return false;
    }

    this.intended = true;
    this.setState("loading");
    try {
      await this.initKws();
      if (!this.intended) return false; // stopped during init
      await this.startMic();
      if (!this.intended) return false;
      this.active = true;
      this.setState("listening");
      wakeDiag(`sherpa engine: listening for "${this.phrase}" (threshold=${this.threshold().toFixed(2)})`);
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
    this.teardown();
    this.setState("stopped");
  }

  setPhrase(phrase: string): void {
    this.phrase = (phrase || SUPPORTED_PHRASE).toLowerCase().trim();
  }

  setSensitivity(value: number): void {
    this.sensitivity = Math.max(0, Math.min(100, value));
  }

  /** sensitivity 0..100 -> KWS threshold 0.45 (strict) .. 0.10 (loose). */
  private threshold(): number {
    return 0.45 - (this.sensitivity / 100) * 0.35;
  }

  private async initKws(): Promise<void> {
    const base = window.location.origin;
    wakeDiag(`sherpa engine: fetching WASM + model from ${base}/wake/ ...`);

    const [wasmBinary, encoder, decoder, joiner, tokens, keywords] = await Promise.all([
      fetchBytes(`${base}/${ASSET.wasm}`),
      fetchBytes(`${base}/${ASSET.encoder}`),
      fetchBytes(`${base}/${ASSET.decoder}`),
      fetchBytes(`${base}/${ASSET.joiner}`),
      fetchText(`${base}/${ASSET.tokens}`),
      fetchText(`${base}/${ASSET.keywords}`),
    ]);
    wakeDiag(
      `sherpa engine: assets fetched (wasm=${(wasmBinary.length / 1e6).toFixed(1)}MB, ` +
        `encoder=${(encoder.length / 1e6).toFixed(1)}MB)`
    );

    const t0 = Date.now();
    // The vendored Emscripten NODE build calls require("path") unconditionally
    // inside its factory (Emscripten's PATH helpers). The renderer has no
    // require(), so install a minimal scoped shim for "path" only, then
    // remove it: the factory captures the path functions in its FS closure
    // at call time and never consults require() again.
    const g = globalThis as any;
    const prevRequire = g.require;
    g.require = (id: string) => {
      if (id === "path") return pathPosix;
      throw new Error(`[sherpa] require("${id}") is not available in the renderer`);
    };
    try {
      this.Module = await createSherpaModule({ wasmBinary });
    } finally {
      if (prevRequire === undefined) delete g.require;
      else g.require = prevRequire;
    }
    const FS = this.Module.FS;
    try {
      FS.mkdir("/kws");
    } catch {
      /* already exists */
    }
    FS.writeFile("/kws/encoder.onnx", encoder);
    FS.writeFile("/kws/decoder.onnx", decoder);
    FS.writeFile("/kws/joiner.onnx", joiner);
    FS.writeFile("/kws/tokens.txt", new TextEncoder().encode(tokens));
    wakeDiag(`sherpa engine: WASM ready in ${Date.now() - t0}ms, model written to virtual FS`);

    const t1 = Date.now();
    this.kws = createKws(this.Module, {
      featConfig: { samplingRate: SAMPLE_RATE, featureDim: 80 },
      modelConfig: {
        transducer: {
          encoder: "/kws/encoder.onnx",
          decoder: "/kws/decoder.onnx",
          joiner: "/kws/joiner.onnx",
        },
        tokens: "/kws/tokens.txt",
        provider: "cpu",
        modelType: "",
        numThreads: 1,
        debug: 0,
        modelingUnit: "bpe",
      },
      maxActivePaths: 4,
      numTrailingBlanks: 1,
      keywordsScore: 1.0,
      keywordsThreshold: this.threshold(),
      keywords: keywords,
    });
    this.stream = this.kws.createStream();
    wakeDiag(`sherpa engine: KWS created in ${Date.now() - t1}ms`);
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
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
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

    this.processorNode.onaudioprocess = (e) => {
      if (!this.intended) return;
      const data = e.inputBuffer.getChannelData(0);
      this.pump(new Float32Array(data));
    };
    wakeDiag(`sherpa engine: microphone acquired, pump running`);
  }

  /** Feed one audio chunk through the keyword spotter. */
  private pump(samples: Float32Array): void {
    if (!this.kws || !this.stream || !this.intended) return;
    try {
      this.stream.acceptWaveform(SAMPLE_RATE, samples);
      while (this.kws.isReady(this.stream)) {
        this.kws.decode(this.stream);
      }
      const result = this.kws.getResult(this.stream);
      if (result && typeof result.keyword === "string" && result.keyword.length > 0) {
        wakeDiag(`sherpa KWS detected keyword: "${result.keyword}"`);
        try {
          this.kws.reset(this.stream);
        } catch {}
        this.fire();
      }
    } catch (err: any) {
      wakeDiag(`sherpa engine: pump error: ${err?.message || err}`);
    }
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
    if (this.stream) {
      try {
        this.stream.free();
      } catch {}
      this.stream = null;
    }
    if (this.kws) {
      try {
        this.kws.free();
      } catch {}
      this.kws = null;
    }
    this.Module = null;
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
