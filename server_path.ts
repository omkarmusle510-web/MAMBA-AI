/**
 * ELYSIA — path & secret resolution.
 *
 * Separates read-only *code/asset* locations (shipped with the app) from the
 * writable *data* location (per-user, survives reinstalls). In development both
 * collapse to the project root, so existing behaviour is unchanged. When the
 * packaged Electron app launches the backend it sets ELYSIA_DATA_DIR to a
 * writable folder under %APPDATA%\ELYSIA, because the install directory
 * (Program Files) is read-only.
 *
 * The Gemini API key is NOT shipped with the app. Each user supplies their own
 * on first run; it is stored here in the per-user data dir (never returned to
 * the frontend).
 */

import fs from "fs";
import path from "path";

/** Writable per-user data directory. Defaults to .mamba in the workspace root. */
export const DATA_DIR: string =
  process.env.MAMBA_DATA_DIR || path.join(process.cwd(), ".mamba");

try {
  fs.mkdirSync(DATA_DIR, { recursive: true });
} catch {
  /* already exists / best-effort */
}

/** Absolute path to a file inside the writable data directory. */
export function dataFile(name: string): string {
  return path.join(DATA_DIR, name);
}

// ---------------------------------------------------------------------------
// Environment & API Keys — Mamba uses standard environment variables (.env)
// ---------------------------------------------------------------------------

/**
 * Resolve the active Gemini / Google API key from environment.
 */
export function getGeminiApiKey(): string | undefined {
  return (
    process.env.GEMINI_API_KEY?.trim() ||
    process.env.GOOGLE_API_KEY?.trim() ||
    undefined
  );
}

/** Whether any usable key is configured. */
export function hasGeminiApiKey(): boolean {
  return Boolean(getGeminiApiKey());
}

/** Persist a user-supplied key to the per-user secrets file. */
export function setGeminiApiKey(key: string): void {
  const trimmed = (key || "").trim();
  if (!trimmed) throw new Error("API key must not be empty.");
  const current = readSecrets();
  current.geminiApiKey = trimmed;
  fs.writeFileSync(SECRETS_FILE, JSON.stringify(current, null, 2), "utf-8");
  try {
    fs.chmodSync(SECRETS_FILE, 0o600); // owner-only where supported
  } catch {
    /* Windows ACLs differ; best-effort */
  }
}

/** Remove the stored key (used by "reset"/sign-out flows). */
export function clearGeminiApiKey(): void {
  const current = readSecrets();
  delete current.geminiApiKey;
  try {
    fs.writeFileSync(SECRETS_FILE, JSON.stringify(current, null, 2), "utf-8");
  } catch {
    /* best-effort */
  }
}