/**
 * Desktop Voice Activation — wake-word engine contracts.
 *
 * The wake listener is a desktop interaction service owned by the Electron
 * shell's renderers; it is NOT part of the Orb's visual rendering logic.
 * The Orb merely hosts it (it is the only renderer alive while DORMANT)
 * and displays its state.
 *
 * Engine implementations are swappable. See webSpeechEngine.ts for the
 * interim backend and the evaluation gate before adopting a local model.
 */

export type WakeEngineState = "stopped" | "loading" | "listening" | "triggered" | "error";

export interface WakeEngineOptions {
  /** Phrase to match (case-insensitive substring). */
  phrase: string;
  /** 0 (strict) .. 100 (loose). */
  sensitivity?: number;
  /** Fired once when the phrase is detected. */
  onTriggered?: () => void;
  /** Fired whenever the detector state changes. */
  onState?: (state: WakeEngineState) => void;
}

/**
 * A wake-word detection engine. Implementations must be self-contained:
 * start() begins listening, stop() fully releases any capture resources.
 */
export interface WakeEngine {
  /** Begin listening. Returns false when unsupported in this environment. */
  start(opts: WakeEngineOptions): boolean | Promise<boolean>;
  /** Fully stop listening and release resources. */
  stop(): void;
  /** Change the wake phrase without a full restart. */
  setPhrase(phrase: string): void;
  /** Change sensitivity (0..100) without a full restart. */
  setSensitivity(value: number): void;
  /** Whether the underlying detector is currently active. */
  readonly isActive: boolean;
  /** Whether this engine can run in the current environment. */
  isSupported(): boolean;
}
