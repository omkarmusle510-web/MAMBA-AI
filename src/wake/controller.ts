/**
 * Desktop Voice Activation — wake listener controller.
 *
 * This is a desktop interaction service, NOT Orb visual logic. It is hosted
 * by the orb renderer only because that renderer is alive while DORMANT;
 * the Orb component itself merely displays state.
 *
 * Phase A (one-turn): on wake, the controller stops the engine (releasing
 * any capture it holds), plays the engine's activation cue, and notifies
 * the shell via window.mambaDesktop.notifyWakeDetected(). The shell
 * activates the session; the main-window voice turn then owns the
 * microphone for command capture. Re-arm via rearm().
 */
import type { WakeEngine, WakeEngineState } from "./types";
import { SherpaOnnxWakeEngine } from "./sherpaOnnxEngine";
import { wakeDiag } from "./diag";

export interface WakeControllerOptions {
  phrase?: string;
  sensitivity?: number;
  /** Called when the wake phrase is detected (after the engine stops). */
  onWake?: () => void;
  /** State updates for the hosting UI (mic indicator). */
  onState?: (state: WakeEngineState) => void;
}

export class WakeController {
  private engine: WakeEngine;
  private opts: WakeControllerOptions;
  private running = false;

  constructor(opts: WakeControllerOptions = {}, engine?: WakeEngine) {
    this.opts = opts;
    // Local sherpa-onnx keyword spotter (WASM, offline). The Web Speech
    // backend proved unreliable inside Electron and is NOT used as a
    // fallback: if sherpa fails to initialize, the engine reports "error".
    this.engine = engine ?? new SherpaOnnxWakeEngine();
  }

  /** Whether a wake engine can run in this environment at all. */
  isSupported(): boolean {
    return this.engine.isSupported();
  }

  get isRunning(): boolean {
    return this.running;
  }

  /** Start listening for the wake phrase. Safe to call repeatedly. */
  start(): boolean {
    // TEMP DIAG (2)+(3): is start() called, and does the engine report supported?
    wakeDiag(`WakeController.start() called (already running=${this.running})`);
    if (this.running) return true;
    const supported = this.engine.isSupported();
    wakeDiag(`engine.isSupported() = ${supported}`);
    const ok = this.engine.start({
      phrase: this.opts.phrase || "hey mamba",
      sensitivity: this.opts.sensitivity ?? 60,
      onTriggered: () => this.handleTrigger(),
      onState: (s) => {
        try {
          this.opts.onState?.(s);
        } catch {
          /* ignore */
        }
      },
    });
    // TEMP DIAG (4): engine.start() outcome.
    wakeDiag(`engine.start() returned ${ok}`);
    // start() may return a promise in future engines; treat truthy as ok.
    if (ok === false) {
      this.running = false;
      return false;
    }
    this.running = true;
    return true;
  }

  /** Stop listening and release all capture resources. */
  stop(): void {
    wakeDiag(`WakeController.stop() called`);
    this.running = false;
    try {
      this.engine.stop();
    } catch {
      /* ignore */
    }
  }

  /** Re-arm after a voice turn completes. */
  rearm(): void {
    wakeDiag(`WakeController.rearm() called`);
    this.stop();
    this.start();
  }

  setPhrase(phrase: string): void {
    try {
      this.engine.setPhrase(phrase);
    } catch {
      /* ignore */
    }
  }

  /**
   * Apply new options from the running app (Settings panel) without a restart.
   * `start()` re-reads them, so a re-arm is enough while listening.
   */
  updateOptions(opts: Partial<WakeControllerOptions>): void {
    this.opts = { ...this.opts, ...opts };
    if (this.running) this.rearm();
  }

  private handleTrigger(): void {
    // TEMP DIAG (6): trigger callback reached in the controller.
    wakeDiag(`TRIGGER callback reached in controller — notifying shell`);
    // Release the detector's capture immediately so the main-window voice
    // turn can acquire the microphone without contention.
    this.stop();
    try {
      this.opts.onWake?.();
    } catch {
      /* never let a handler error break re-arming */
    }
  }
}
