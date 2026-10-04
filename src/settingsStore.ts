/**
 * ELYSIA Settings Store — persistent user preferences (V2).
 *
 * Establishes the persistence pattern for ELYSIA: settings are mirrored to
 * localStorage (instant local read) AND synced to the backend (settings.json)
 * so auto-start / wake-word preferences survive across browsers and the
 * Python desktop agent can read them too.
 *
 * Pattern follows the existing codebase conventions: plain state + ref mirrors.
 * No Context/Zustand — this is deliberately lightweight to match audio.ts/memoryTypes.ts.
 */

export interface MambaSettings {
  /** Launch Mamba silently on system login. */
  autoStart: boolean;
  /** Enable the always-listening wake-word detector. */
  wakeWordEnabled: boolean;
  /** Phrase that activates Mamba (case-insensitive substring match). */
  wakePhrase: string;
  /** Wake-word sensitivity: 0 (strict) .. 100 (loose). Affects debounce window. */
  sensitivity: number;
  /** Master toggle for UI animations. */
  animations: boolean;
}

export const DEFAULT_SETTINGS: MambaSettings = {
  autoStart: false,
  wakeWordEnabled: true,
  wakePhrase: "hey mamba",
  sensitivity: 60,
  animations: true,
};

const STORAGE_KEY = "mamba.settings.v1";

/** Settings keys that the browser should never persist (security). */
const NEVER_PERSIST: ReadonlySet<keyof MambaSettings> = new Set([]);

const KNOWN_KEYS = Object.keys(DEFAULT_SETTINGS) as (keyof MambaSettings)[];

/**
 * Merge over defaults keeping only the keys Mamba actually reads, so a payload
 * written by an older build cannot resurrect a setting that has been removed.
 */
function merge(overrides: Partial<MambaSettings> | null): MambaSettings {
  const next: MambaSettings = { ...DEFAULT_SETTINGS };
  if (!overrides) return next;
  KNOWN_KEYS.forEach((key) => {
    const value = overrides[key];
    if (value !== undefined && value !== null) {
      (next as unknown as Record<string, unknown>)[key] = value;
    }
  });
  return next;
}

/**
 * Load settings from localStorage, merged over defaults so new keys always
 * have a sane value even when an older payload is present.
 */
export function loadSettings(): MambaSettings {
  if (typeof window === "undefined") return { ...DEFAULT_SETTINGS };
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return { ...DEFAULT_SETTINGS };
    return merge(JSON.parse(raw) as Partial<MambaSettings>);
  } catch {
    return { ...DEFAULT_SETTINGS };
  }
}

/**
 * Persist a full or partial settings update to localStorage.
 * Returns the fully merged settings object.
 */
export function saveSettings(patch: Partial<MambaSettings>): MambaSettings {
  const current = loadSettings();
  const next: MambaSettings = merge({ ...current, ...patch });
  if (typeof window !== "undefined") {
    try {
      // Strip any sensitive keys before writing to localStorage.
      const safe: Record<string, unknown> = {};
      (Object.keys(next) as (keyof MambaSettings)[]).forEach((k) => {
        if (!NEVER_PERSIST.has(k)) safe[k] = next[k];
      });
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(safe));
    } catch {
      /* localStorage may be unavailable (private mode) — fail silently. */
    }
  }
  // Best-effort sync to backend so the Python agent can read auto-start state.
  void syncSettingsToBackend(next).catch(() => {});
  return next;
}

/** Push settings to the backend (server.ts persists to settings.json). */
async function syncSettingsToBackend(settings: MambaSettings): Promise<void> {
  try {
    await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(settings),
    });
  } catch {
    /* Backend may be briefly unavailable during boot — non-fatal. */
  }
}