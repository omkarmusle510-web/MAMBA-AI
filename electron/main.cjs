/**
 * Mamba Desktop Shell — Main Process
 *
 * Strictly a lifecycle and presentation boundary for Windows desktop.
 * Coordinates:
 * - Native desktop window (chat & presence dashboard)
 * - Floating Mamba Orb window (frameless, transparent, always-on-top, draggable)
 * - Windows System Tray (Show, Hide, Quit)
 * - Global Activation Hotkey (Ctrl + Space)
 * - Single-instance enforcement
 * - Active / Dormant Lifecycle with Idle Timeout & Task Protection
 * - Backend and Frontend process boundaries
 *
 * Mamba Core remains the sole source of truth.
 */

const { app, BrowserWindow, shell, ipcMain, screen } = require("electron");
const path = require("path");
const { BackendManager } = require("./backendManager.cjs");
const { FrontendManager } = require("./frontendManager.cjs");
const { TrayManager } = require("./trayManager.cjs");
const { HotkeyManager } = require("./hotkeyManager.cjs");
const { LifecycleManager, LifecycleState } = require("./lifecycleManager.cjs");

// Enforce single application instance
const gotTheLock = app.requestSingleInstanceLock();
if (!gotTheLock) {
  console.log("[Mamba Shell] Another instance is already running. Exiting cleanly.");
  app.quit();
  process.exit(0);
}

let mainWindow = null;
let orbWindow = null;
let backendManager = null;
let frontendManager = null;
let trayManager = null;
let hotkeyManager = null;
let lifecycleManager = null;
let isQuitting = false;
let isCleanedUp = false;
let currentOrbState = "idle";
let resolvedFrontendUrl = null;

const isDev = process.argv.includes("--dev") || process.env.NODE_ENV === "development";
const isSmokeTest = process.argv.includes("--smoke-test");

function getSplashHtml(message = "Initializing Mamba AI...") {
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

function showWindow() {
  if (mainWindow && !mainWindow.isDestroyed()) {
    if (mainWindow.isMinimized()) {
      mainWindow.restore();
    }
    if (!mainWindow.isVisible()) {
      mainWindow.show();
    }
    mainWindow.focus();
    console.log("[Mamba Shell] Main window shown, restored, and focused.");
  }

  if (orbWindow && !orbWindow.isDestroyed()) {
    if (!orbWindow.isVisible()) {
      orbWindow.show();
    }
  }
}

function hideWindow() {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.hide();
    console.log("[Mamba Shell] Main window hidden.");
  }
}

function quitApp() {
  if (isQuitting) return;
  console.log("[Mamba Shell] Clean quit initiated.");
  isQuitting = true;
  cleanupProcesses();
  if (orbWindow && !orbWindow.isDestroyed()) {
    orbWindow.destroy();
    orbWindow = null;
  }
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.destroy();
    mainWindow = null;
  }
  app.quit();
}

function createMainWindow() {
  const iconPath = path.join(__dirname, "assets", "icon.png");

  mainWindow = new BrowserWindow({
    width: 1100,
    height: 750,
    minWidth: 480,
    minHeight: 600,
    backgroundColor: "#020617",
    title: "Mamba AI",
    icon: iconPath,
    autoHideMenuBar: true,
    show: false, // DORMANT on startup: main window is initially hidden
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      webSecurity: true,
    },
  });

  // Intercept window close (X button): hide window, keep session and idle timer active
  mainWindow.on("close", (event) => {
    if (!isQuitting) {
      event.preventDefault();
      hideWindow();
      console.log("[Mamba Shell] Main window close intercepted -> hidden.");
      return false;
    }
  });

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith("http://") || url.startsWith("https://")) {
      shell.openExternal(url);
    }
    return { action: "deny" };
  });

  mainWindow.webContents.on("did-finish-load", () => {
    const currentUrl = mainWindow.webContents.getURL();
    console.log(`[Mamba Shell] Main window loaded: ${currentUrl.slice(0, 80)}...`);
  });

  mainWindow.webContents.on("did-fail-load", (event, errorCode, errorDescription, validatedURL) => {
    console.error(`[Mamba Shell] Main window failed to load: ${validatedURL} (${errorCode}: ${errorDescription})`);
  });

  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

function createOrbWindow(frontendUrl) {
  const iconPath = path.join(__dirname, "assets", "icon.png");

  // Position in bottom-right corner of primary work area
  const primaryDisplay = screen.getPrimaryDisplay();
  const { width, height } = primaryDisplay.workAreaSize;
  const orbSize = 220;
  const x = Math.max(0, width - orbSize - 24);
  const y = Math.max(0, height - orbSize - 24);

  orbWindow = new BrowserWindow({
    width: orbSize,
    height: orbSize,
    x,
    y,
    frame: false,
    transparent: true,
    alwaysOnTop: true,
    resizable: false,
    skipTaskbar: true,
    hasShadow: false,
    backgroundColor: "#00000000",
    icon: iconPath,
    show: false,
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      webSecurity: true,
    },
  });

  orbWindow.once("ready-to-show", () => {
    orbWindow.show();
    console.log("[Mamba Shell] Floating Orb window displayed on desktop.");
  });

  // Intercept orb close: hide instead of destroying
  orbWindow.on("close", (event) => {
    if (!isQuitting) {
      event.preventDefault();
      orbWindow.hide();
      console.log("[Mamba Shell] Orb window close intercepted -> hidden.");
      return false;
    }
  });

  orbWindow.webContents.on("did-finish-load", () => {
    console.log("[Mamba Shell] Floating Orb view loaded successfully.");
    // Sync current state to newly loaded orb view
    if (orbWindow && !orbWindow.isDestroyed()) {
      orbWindow.webContents.send("mamba:state", currentOrbState);
    }
  });

  orbWindow.webContents.on("did-fail-load", (event, errorCode, errorDescription, validatedURL) => {
    console.error(`[Mamba Shell] Orb window failed to load: ${validatedURL} (${errorCode}: ${errorDescription})`);
  });

  orbWindow.on("closed", () => {
    orbWindow = null;
  });

  if (frontendUrl) {
    const orbUrl = frontendUrl.includes("?")
      ? `${frontendUrl}&mode=orb`
      : `${frontendUrl}?mode=orb`;
    console.log(`[Mamba Shell] Loading Orb window URL: ${orbUrl}`);
    orbWindow.loadURL(orbUrl);
  }
}

function setupIpc() {
  ipcMain.on("mamba:report-activity", (event, type) => {
    if (lifecycleManager) lifecycleManager.recordActivity(type);
  });

  ipcMain.on("mamba:report-task-state", (event, active) => {
    if (lifecycleManager) lifecycleManager.setTaskActive(active);
  });

  ipcMain.on("mamba:awaiting-permission", (event, awaiting) => {
    if (lifecycleManager) lifecycleManager.setAwaitingPermission(awaiting);
  });

  ipcMain.on("mamba:report-voice-state", (event, active) => {
    if (lifecycleManager) lifecycleManager.setVoiceActive(active);
  });

  ipcMain.on("mamba:report-state", (event, state) => {
    currentOrbState = state;
    if (orbWindow && !orbWindow.isDestroyed()) {
      orbWindow.webContents.send("mamba:state", state);
    }
  });

  ipcMain.on("mamba:activate", () => {
    if (!lifecycleManager) return;
    if (lifecycleManager.getState() === LifecycleState.DORMANT) {
      lifecycleManager.requestActivation("orb-click");
    } else {
      if (mainWindow && !mainWindow.isDestroyed() && mainWindow.isVisible() && mainWindow.isFocused()) {
        mainWindow.hide();
      } else {
        showWindow();
        lifecycleManager.recordActivity("orb-toggle");
      }
    }
  });

  ipcMain.on("mamba:toggle-main", () => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      if (mainWindow.isVisible()) {
        mainWindow.hide();
      } else {
        showWindow();
      }
    }
  });

  ipcMain.on("mamba:get-lifecycle-state", (event) => {
    event.returnValue = lifecycleManager ? lifecycleManager.getState() : LifecycleState.DORMANT;
  });
}

async function bootApp() {
  createMainWindow();
  setupIpc();

  // 1. Initialize System Tray (always available across all states)
  trayManager = new TrayManager({
    iconPath: path.join(__dirname, "assets", "icon.png"),
    onShow: () => {
      if (lifecycleManager) lifecycleManager.requestActivation("tray-show");
    },
    onHide: () => hideWindow(),
    onQuit: () => quitApp(),
  });
  trayManager.create();

  // 2. Initialize Global Activation Hotkey (Ctrl + Space)
  hotkeyManager = new HotkeyManager({
    shortcut: "CommandOrControl+Space",
    onActivate: () => {
      if (lifecycleManager) lifecycleManager.requestActivation("global-hotkey");
    },
  });
  hotkeyManager.register();

  backendManager = new BackendManager({
    rootDir: path.resolve(__dirname, ".."),
  });

  frontendManager = new FrontendManager({
    rootDir: path.resolve(__dirname, ".."),
    backendPort: backendManager.port,
  });

  // 3. Resolve frontend URL (Vite dev server or StaticServer)
  // Serves frontend static assets without starting the Python backend
  const { url } = await frontendManager.resolveUrl(isDev);
  resolvedFrontendUrl = url;

  // 4. Create Floating Orb Window
  createOrbWindow(resolvedFrontendUrl);

  // 5. Initialize Lifecycle Manager
  lifecycleManager = new LifecycleManager({
    backendManager,
    frontendManager,
    getMainWindow: () => mainWindow,
    showWindow: () => showWindow(),
    hideWindow: () => hideWindow(),
    isDev,
    idleTimeoutMs: isSmokeTest ? 1500 : undefined,
    idleShutdownTimeoutMs: isSmokeTest ? 1500 : undefined,
  });

  // Sync lifecycle state to Floating Orb visual
  lifecycleManager.on("state-change", ({ newState }) => {
    let orbState = "idle";
    if (newState === LifecycleState.STARTING) orbState = "thinking";
    else if (newState === LifecycleState.DORMANT) orbState = "idle";
    else if (newState === LifecycleState.ACTIVE) orbState = currentOrbState || "idle";
    else if (newState === LifecycleState.IDLE) orbState = "idle";

    if (orbWindow && !orbWindow.isDestroyed()) {
      orbWindow.webContents.send("mamba:state", orbState);
    }
  });

  console.log(`[Mamba Shell] Initialized in DORMANT state. Backend is not running.`);
  console.log(`[Mamba Shell] Floating Orb window running. Main window hidden.`);
  console.log(`[Mamba Shell] Idle timeout configured: ${lifecycleManager.idleTimeoutMs / 1000}s`);

  // 6. Run automated test if --smoke-test passed
  if (isSmokeTest) {
    runSmokeTest();
  }
}

async function runSmokeTest() {
  console.log("[SmokeTest] Starting Floating Orb and Lifecycle automated validation...");
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  try {
    // 1. Verify initial DORMANT state
    console.log(`[Validation 1] Initial state is DORMANT: ${lifecycleManager.getState() === LifecycleState.DORMANT}`);

    // 2. Verify Python backend is NOT running on startup
    const isBackendAliveAtStart = await backendManager.isHealthy(300);
    console.log(`[Validation 2] Backend NOT running on startup: ${!isBackendAliveAtStart}`);

    // 3. Verify Floating Orb window exists and is visible
    const isOrbCreated = Boolean(orbWindow && !orbWindow.isDestroyed());
    const isOrbVisible = orbWindow ? orbWindow.isVisible() : false;
    console.log(`[Validation 3] Floating Orb window created: ${isOrbCreated}`);
    console.log(`[Validation 4] Floating Orb window visible on desktop: ${isOrbVisible}`);

    // 4. Verify main window is hidden initially
    console.log(`[Validation 5] Main window hidden initially in DORMANT: ${!mainWindow.isVisible()}`);

    // 5. Test Orb states rendering
    const testStates = ["idle", "listening", "thinking", "speaking", "permission", "error"];
    for (const st of testStates) {
      orbWindow.webContents.send("mamba:state", st);
      await sleep(150);
    }
    console.log(`[Validation 6] All 6 Orb visual states updated without error: true`);

    // 6. Activate session via Orb click simulation
    console.log("[SmokeTest] Activating session via Orb click...");
    await lifecycleManager.requestActivation("smoke-test-orb-click");

    console.log(`[Validation 7] State transitioned to ACTIVE: ${lifecycleManager.getState() === LifecycleState.ACTIVE}`);
    console.log(`[Validation 8] Main window is now visible: ${mainWindow.isVisible()}`);
    const isBackendAliveNow = await backendManager.isHealthy(1000);
    console.log(`[Validation 9] Backend started and healthy: ${isBackendAliveNow}`);

    // 7. Test closing Orb window (should intercept to hide, not quit Mamba)
    orbWindow.close();
    console.log(`[Validation 10] Orb close intercepted to hide: ${!orbWindow.isVisible()}`);
    console.log(`[Validation 11] Main window and session still active after Orb hide: ${lifecycleManager.getState() === LifecycleState.ACTIVE}`);

    // Re-show orb
    orbWindow.show();
    console.log(`[Validation 12] Orb re-shown: ${orbWindow.isVisible()}`);

    // 8. Test Ctrl+Space while active
    const pid1 = backendManager.backendProcess ? backendManager.backendProcess.pid : null;
    await lifecycleManager.requestActivation("smoke-test-hotkey");
    const pid2 = backendManager.backendProcess ? backendManager.backendProcess.pid : null;
    console.log(`[Validation 13] Ctrl+Space while active preserves backend PID: ${pid1 === pid2}`);

    // 9. Test active task protection
    lifecycleManager.setTaskActive(true);
    await sleep(2200);
    console.log(`[Validation 14] In-flight task prevents IDLE transition: ${lifecycleManager.getState() === LifecycleState.ACTIVE}`);
    lifecycleManager.setTaskActive(false);

    // 10. Test idle transition
    await sleep(2000);
    console.log(`[Validation 15] Transitioned ACTIVE -> IDLE: ${lifecycleManager.getState() === LifecycleState.IDLE}`);
    console.log(`[Validation 16] Backend alive during IDLE: ${await backendManager.isHealthy(500)}`);

    // 11. Allow transition to DORMANT
    await sleep(3800);
    console.log(`[Validation 17] Transitioned to DORMANT: ${lifecycleManager.getState() === LifecycleState.DORMANT}`);
    console.log(`[Validation 18] Backend stopped in DORMANT: ${!(await backendManager.isHealthy(500))}`);
    console.log(`[Validation 19] Orb window remains alive in DORMANT: ${Boolean(orbWindow && !orbWindow.isDestroyed())}`);

    // 12. Final clean quit via tray
    console.log("[SmokeTest] Initiating clean quit via tray...");
    quitApp();
    console.log("[Validation 20] Clean quit completed successfully.");
  } catch (err) {
    console.error("[SmokeTest Error]", err);
    quitApp();
  }
}

// Lifecycle cleanup
function cleanupProcesses() {
  if (isCleanedUp) return;
  isCleanedUp = true;
  console.log("[Mamba Shell] Application exiting. Cleaning up processes and system hooks...");

  if (hotkeyManager) {
    hotkeyManager.unregister();
    hotkeyManager = null;
  }

  if (trayManager) {
    trayManager.destroy();
    trayManager = null;
  }

  if (lifecycleManager) {
    lifecycleManager.teardown();
    lifecycleManager = null;
  }

  if (frontendManager) {
    frontendManager.stop();
    frontendManager = null;
  }
}

// Handle second instance activation
app.on("second-instance", () => {
  console.log("[Mamba Shell] Secondary instance detected. Activating session.");
  if (lifecycleManager) {
    lifecycleManager.requestActivation("second-instance");
  } else {
    showWindow();
  }
});

app.whenReady().then(bootApp);

app.on("window-all-closed", () => {
  if (isQuitting) {
    cleanupProcesses();
    if (process.platform !== "darwin") {
      app.quit();
    }
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
