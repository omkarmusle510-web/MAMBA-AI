/**
 * Electron Launcher
 *
 * Ensures clean environment variables (clearing ELECTRON_RUN_AS_NODE if set in the host shell)
 * and spawns the Electron binary with inherited stdio.
 */

const { spawn } = require("child_process");
const electronPath = require("electron");

const env = { ...process.env };
delete env.ELECTRON_RUN_AS_NODE;

const child = spawn(electronPath, process.argv.slice(2), {
  stdio: "inherit",
  windowsHide: false,
  env,
});

child.on("close", (code, signal) => {
  if (code === null && signal) {
    process.exit(1);
  }
  process.exit(code ?? 0);
});

child.on("error", (err) => {
  console.error("[Electron Launcher] Failed to spawn Electron:", err);
  process.exit(1);
});
