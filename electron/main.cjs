/**
 * Mamba Desktop Shell — Main Process (Phase 1)
 *
 * Strictly a lifecycle and presentation boundary for Windows desktop.
 * Does NOT contain runtime, planning, permissions, memory, or tool logic.
 * Mamba Core remains the sole source of truth.
 */

const { app, BrowserWindow, shell, ipcMain } = require("electron");
const path = require("path");
const { BackendManager } = require("./backendManager.cjs");
const { FrontendManager } = require("./frontendManager.cjs");

// Enforce single application instance
const gotTheLock = app.requestSingleInstanceLock();
if (!gotTheLock) {
  console.log("[Mamba Shell] Another instance is already running. Exiting.");
  app.quit();
  process.exit(0);
}

let mainWindow = null;
let backendManager = null;
let frontendManager = null;
let isQuitting = false;

const isDev = process.argv.includes("--dev") || process.env.NODE_ENV === "development";

function getSplashHtml(message = "Starting Mamba Core...") {
  return `<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <style>
    body {
      margin: 0;
      padding: 0;
      background: #020617;
      color: #94a3b8;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, monospace;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      height: 100vh;
      user-select: none;
    }
    .orb {
      width: 48px;
      height: 48px;
      border-radius: 50%;
      background: #22d3ee;
      box-shadow: 0 0 30px #22d3ee;
      animation: pulse 1.8s infinite ease-in-out;
      margin-bottom: 24px;
    }
    @keyframes pulse {
      0%, 100% { transform: scale(0.9); opacity: 0.6; }
      50% { transform: scale(1.1); opacity: 1; }
    }
    .title {
      font-size: 14px;
      letter-spacing: 0.2em;
      font-weight: bold;
      color: #e2e8f0;
      margin-bottom: 8px;
    }
    .status {
      font-size: 12px;
      color: #64748b;
    }
  </style>
</head>
<body>
  <div class="orb"></div>
  <div class="title">MAMBA AI</div>
  <div class="status" id="status-msg">${message}</div>
</body>
</html>`;
}

function getErrorHtml(errorMessage) {
  const safeError = errorMessage
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");

  return `<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <style>
    body {
      margin: 0;
      padding: 32px;
      background: #020617;
      color: #f87171;
      font-family: monospace;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      height: 100vh;
      box-sizing: border-box;
    }
    h2 { color: #ef4444; margin-bottom: 12px; font-size: 18px; }
    pre {
      background: #0f172a;
      border: 1px solid #334155;
      padding: 16px;
      border-radius: 8px;
      max-width: 90%;
      max-height: 40vh;
      overflow-y: auto;
      color: #cbd5e1;
      font-size: 12px;
      white-space: pre-wrap;
      word-break: break-all;
    }
    .btn {
      margin-top: 24px;
      padding: 8px 20px;
      background: #334155;
      color: #f8fafc;
      border: none;
      border-radius: 6px;
      cursor: pointer;
      font-family: sans-serif;
      font-size: 13px;
    }
    .btn:hover { background: #475569; }
  </style>
</head>
<body>
  <h2>Backend Initialization Error</h2>
  <p style="color: #94a3b8; font-size: 13px; margin-bottom: 16px;">
    Mamba Core transport adapter failed to start or verify health.
  </p>
  <pre>${safeError}</pre>
  <button class="btn" onclick="location.reload()">Retry Launch</button>
</body>
</html>`;
}

function createMainWindow() {
  mainWindow = new BrowserWindow({
    width: 1100,
    height: 750,
    minWidth: 480,
    minHeight: 600,
    backgroundColor: "#020617",
    title: "Mamba AI",
    autoHideMenuBar: true,
    show: false,
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      webSecurity: true,
    },
  });

  mainWindow.once("ready-to-show", () => {
    mainWindow.show();
  });

  mainWindow.webContents.on("did-finish-load", () => {
    const currentUrl = mainWindow.webContents.getURL();
    console.log(`[Mamba Shell] Page loaded: ${currentUrl.slice(0, 80)}...`);
    if (currentUrl.startsWith("http://") || currentUrl.startsWith("https://")) {
      console.log(`[Mamba Shell] React interface fully loaded into Electron window: ${currentUrl}`);
      if (process.argv.includes("--smoke-test")) {
        console.log("[Mamba Shell] Smoke test: React interface loaded. Quitting for automated verification in 3s...");
        setTimeout(() => {
          app.quit();
        }, 3000);
      }
    }
  });

  mainWindow.webContents.on("did-fail-load", (event, errorCode, errorDescription, validatedURL) => {
    console.error(`[Mamba Shell] Page failed to load: ${validatedURL} (${errorCode}: ${errorDescription})`);
  });

  // Handle external link clicks
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith("http://") || url.startsWith("https://")) {
      shell.openExternal(url);
    }
    return { action: "deny" };
  });

  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

async function bootApp() {
  createMainWindow();

  // 1. Show splash loader while backend is starting
  mainWindow.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(getSplashHtml())}`);

  backendManager = new BackendManager({
    rootDir: path.resolve(__dirname, ".."),
  });

  frontendManager = new FrontendManager({
    rootDir: path.resolve(__dirname, ".."),
    backendPort: backendManager.port,
  });

  try {
    // 2. Start or connect to Mamba backend
    console.log("[Mamba Shell] Initializing backend lifecycle...");
    const backendResult = await backendManager.start();
    console.log("[Mamba Shell] Backend ready:", backendResult);

    // 3. Resolve frontend URL (Vite dev server or static server)
    console.log(`[Mamba Shell] Resolving frontend (isDev: ${isDev})...`);
    const { url } = await frontendManager.resolveUrl(isDev);
    console.log(`[Mamba Shell] Loading frontend URL: ${url}`);

    // 4. Expose React interface
    if (mainWindow && !mainWindow.isDestroyed()) {
      await mainWindow.loadURL(url);
    }
  } catch (err) {
    console.error("[Mamba Shell] Initialization failed:", err);
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.loadURL(
        `data:text/html;charset=utf-8,${encodeURIComponent(getErrorHtml(err.message || String(err)))}`
      );
    }
  }
}

// Lifecycle cleanup
function cleanupProcesses() {
  if (isQuitting) return;
  isQuitting = true;
  console.log("[Mamba Shell] Application exiting. Cleaning up processes...");

  if (frontendManager) {
    frontendManager.stop();
    frontendManager = null;
  }

  if (backendManager) {
    backendManager.stop();
    backendManager = null;
  }
}

// Handle second instance activation
app.on("second-instance", () => {
  if (mainWindow) {
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.focus();
  }
});

app.whenReady().then(bootApp);

app.on("window-all-closed", () => {
  cleanupProcesses();
  if (process.platform !== "darwin") {
    app.quit();
  }
});

app.on("before-quit", () => {
  cleanupProcesses();
});

app.on("will-quit", () => {
  cleanupProcesses();
});

process.on("SIGINT", () => {
  cleanupProcesses();
  process.exit(0);
});

process.on("SIGTERM", () => {
  cleanupProcesses();
  process.exit(0);
});
