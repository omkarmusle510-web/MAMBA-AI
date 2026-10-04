/**
 * Motion policy — the single source of truth for whether anything may animate.
 *
 * Two inputs decide: the OS-level `prefers-reduced-motion` preference and the
 * in-app master toggle (`settings.animations`). CSS has its own floor for the
 * OS preference (see the reduced-motion block in `src/index.css`); this module
 * exists so JS-driven motion (Three.js orb, layout transitions, auto-grow) can
 * honour the same decision instead of inventing its own.
 */

const reduceQuery = "(prefers-reduced-motion: reduce)";

export function prefersReducedMotion(): boolean {
  if (typeof window === "undefined" || !window.matchMedia) return false;
  return window.matchMedia(reduceQuery).matches;
}

/** In-app master toggle layered over the OS preference. */
export function motionEnabled(settingsAnimations: boolean): boolean {
  return settingsAnimations !== false && !prefersReducedMotion();
}
