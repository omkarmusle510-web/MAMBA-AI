/**
 * Compatibility shim — prefer `src/wake/` for new code.
 *
 * The wake listener is a desktop interaction service (see src/wake/); this
 * module only re-exports the interim engine under its historical names.
 */
export { WebSpeechWakeEngine as MambaWakeWordDetector } from "./wake/webSpeechEngine";
export { WebSpeechWakeEngine as ElysiaWakeWordDetector } from "./wake/webSpeechEngine";
export type { WakeEngineState as WakeWordState } from "./wake/types";
export type { WakeEngineOptions as WakeWordOptions } from "./wake/types";
