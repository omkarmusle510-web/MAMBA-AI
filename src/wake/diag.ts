/**
 * TEMPORARY DIAGNOSTIC ONLY — wake-word debugging on Windows.
 *
 * Forwards wake diagnostics from the Orb renderer to the Electron main
 * process so they appear in the terminal. Falls back to the renderer
 * console when the bridge is unavailable. Delete this file (and its call
 * sites) once the wake-word failure is diagnosed.
 */
export function wakeDiag(message: string): void {
  const line = `[WakeDiag] ${message}`;
  try {
    const bridge = (window as any)?.mambaDesktop;
    if (bridge?.reportWakeDiag) {
      bridge.reportWakeDiag(line);
      return;
    }
  } catch {
    /* fall through to console */
  }
  // eslint-disable-next-line no-console
  console.log(line);
}
