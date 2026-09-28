/**
 * Mamba Desktop Shell — Lifecycle Manager
 *
 * Implements explicit shell lifecycle state machine:
 *   DORMANT -> STARTING -> ACTIVE -> IDLE -> SHUTTING_DOWN -> DORMANT
 *
 * Invariants:
 * - Shell acts strictly as lifecycle & presentation boundary.
 * - Mamba Core / Brain does NOT know about Windows lifecycle or inactivity timers.
 * - On normal startup, shell enters DORMANT without spawning Python backend.
 * - Activation (Ctrl+Space or Tray) transitions DORMANT -> STARTING -> ACTIVE.
 * - Inactivity transitions ACTIVE -> IDLE -> SHUTTING_DOWN -> DORMANT.
 * - Active tasks / awaiting permissions prevent dormant shutdown.
 * - Backend ownership is preserved (only terminates owned processes).
 */

const EventEmitter = require("events");

const LifecycleState = {
  DORMANT: "DORMANT",
  STARTING: "STARTING",
  ACTIVE: "ACTIVE",
  IDLE: "IDLE",
  SHUTTING_DOWN: "SHUTTING_DOWN",
};

const DEFAULT_IDLE_TIMEOUT_MS = 10 * 60 * 1000; // 10 minutes production default
const DEFAULT_IDLE_SHUTDOWN_TIMEOUT_MS = 5 * 60 * 1000; // 5 minutes idle grace period

class LifecycleManager extends EventEmitter {
  constructor(options = {}) {
    super();
    this.backendManager = options.backendManager;
    this.frontendManager = options.frontendManager;
    this.getMainWindow = options.getMainWindow || (() => null);
    this.showWindow = options.showWindow || (() => {});
    this.hideWindow = options.hideWindow || (() => {});
    this.isDev = Boolean(options.isDev);

    this.idleTimeoutMs =
      options.idleTimeoutMs ||
      (process.env.MAMBA_IDLE_TIMEOUT_MS
        ? parseInt(process.env.MAMBA_IDLE_TIMEOUT_MS, 10)
        : DEFAULT_IDLE_TIMEOUT_MS);

    this.idleShutdownTimeoutMs =
      options.idleShutdownTimeoutMs ||
      (process.env.MAMBA_IDLE_SHUTDOWN_TIMEOUT_MS
        ? parseInt(process.env.MAMBA_IDLE_SHUTDOWN_TIMEOUT_MS, 10)
        : DEFAULT_IDLE_SHUTDOWN_TIMEOUT_MS);

    this.state = LifecycleState.DORMANT;
    this.activeTaskCount = 0;
    this.isAwaitingPermission = false;
    this.isVoiceActive = false;

    this.idleTimer = null;
    this.idleShutdownTimer = null;
    this._activationPromise = null;
  }

  getState() {
    return this.state;
  }

  isBusy() {
    return this.activeTaskCount > 0 || this.isAwaitingPermission || this.isVoiceActive;
  }

  transitionTo(newState) {
    if (this.state === newState) return;
    const oldState = this.state;
    this.state = newState;
    console.log(`[LifecycleManager] State transition: ${oldState} -> ${newState}`);

    const win = this.getMainWindow();
    if (win && !win.isDestroyed()) {
      try {
        win.webContents.send("mamba:lifecycle-state", newState);
      } catch {}
    }

    this.emit("state-change", { oldState, newState });
  }

  /**
   * Request session activation (e.g. Ctrl + Space or Tray Show).
   */
  async requestActivation(source = "unknown") {
    console.log(`[LifecycleManager] Activation requested via ${source} (Current state: ${this.state})`);

    // 1. If already starting, wait for the pending startup to complete
    if (this.state === LifecycleState.STARTING && this._activationPromise) {
      await this._activationPromise;
      this.showWindow();
      this.resetIdleTimer();
      return;
    }

    // 2. If ACTIVE or IDLE, restore window and reset timer
    if (this.state === LifecycleState.ACTIVE || this.state === LifecycleState.IDLE) {
      if (this.state === LifecycleState.IDLE) {
        this.transitionTo(LifecycleState.ACTIVE);
      }
      this.showWindow();
      this.resetIdleTimer();
      return;
    }

    // 3. If SHUTTING_DOWN, wait for shutdown to complete then activate
    if (this.state === LifecycleState.SHUTTING_DOWN) {
      console.log("[LifecycleManager] Activation received during shutdown; deferring...");
      await new Promise((r) => setTimeout(r, 800));
      return this.requestActivation(source);
    }

    // 4. If DORMANT, initiate backend startup
    if (this.state === LifecycleState.DORMANT) {
      this._activationPromise = this._startSession();
      try {
        await this._activationPromise;
      } finally {
        this._activationPromise = null;
      }
    }
  }

  async _startSession() {
    this.transitionTo(LifecycleState.STARTING);
    const win = this.getMainWindow();

    try {
      console.log("[LifecycleManager] Starting Mamba backend transport adapter...");
      const backendResult = await this.backendManager.start();
      console.log("[LifecycleManager] Backend confirmed ready:", backendResult);

      // Resolve frontend URL
      const { url } = await this.frontendManager.resolveUrl(this.isDev);

      if (win && !win.isDestroyed()) {
        const currentUrl = win.webContents.getURL();
        if (!currentUrl || currentUrl.startsWith("data:") || currentUrl === "about:blank") {
          console.log(`[LifecycleManager] Loading frontend URL into window: ${url}`);
          await win.loadURL(url);
        }
      }

      this.transitionTo(LifecycleState.ACTIVE);
      this.showWindow();
      this.resetIdleTimer();
    } catch (err) {
      console.error("[LifecycleManager] Failed to start backend session:", err);
      this.transitionTo(LifecycleState.DORMANT);
      throw err;
    }
  }

  /**
   * Record meaningful user or system activity.
   */
  recordActivity(type = "generic") {
    // Meaningful activity brings IDLE back to ACTIVE
    if (this.state === LifecycleState.IDLE) {
      console.log(`[LifecycleManager] Activity (${type}) while IDLE -> transitioning to ACTIVE`);
      this.transitionTo(LifecycleState.ACTIVE);
    }

    if (this.state === LifecycleState.ACTIVE) {
      this.resetIdleTimer();
    }
  }

  setTaskActive(isActive) {
    if (isActive) {
      this.activeTaskCount++;
      this.recordActivity("task-started");
    } else {
      this.activeTaskCount = Math.max(0, this.activeTaskCount - 1);
      this.recordActivity("task-completed");
    }
    console.log(`[LifecycleManager] Active task count: ${this.activeTaskCount}`);
  }

  setAwaitingPermission(isAwaiting) {
    this.isAwaitingPermission = Boolean(isAwaiting);
    console.log(`[LifecycleManager] Awaiting permission: ${this.isAwaitingPermission}`);
    this.recordActivity(isAwaiting ? "awaiting-permission" : "permission-cleared");
  }

  setVoiceActive(isActive) {
    this.isVoiceActive = Boolean(isActive);
    console.log(`[LifecycleManager] Voice active: ${this.isVoiceActive}`);
    this.recordActivity(isActive ? "voice-started" : "voice-stopped");
  }

  resetIdleTimer() {
    this._clearTimers();

    if (this.state !== LifecycleState.ACTIVE) return;

    this.idleTimer = setTimeout(() => {
      this._onIdleTimeout();
    }, this.idleTimeoutMs);
  }

  _onIdleTimeout() {
    if (this.state !== LifecycleState.ACTIVE) return;

    if (this.isBusy()) {
      console.log(
        `[LifecycleManager] Idle timeout reached but work in progress (tasks: ${this.activeTaskCount}, permission: ${this.isAwaitingPermission}, voice: ${this.isVoiceActive}). Deferring.`
      );
      this.resetIdleTimer();
      return;
    }

    console.log(`[LifecycleManager] Inactivity reached (${this.idleTimeoutMs / 1000}s). Transitioning ACTIVE -> IDLE.`);
    this.transitionTo(LifecycleState.IDLE);
    this._startIdleShutdownTimer();
  }

  _startIdleShutdownTimer() {
    this._clearTimers();

    this.idleShutdownTimer = setTimeout(() => {
      this._onIdleShutdownTimeout();
    }, this.idleShutdownTimeoutMs);
  }

  _onIdleShutdownTimeout() {
    if (this.state !== LifecycleState.IDLE) return;

    if (this.isBusy()) {
      console.log("[LifecycleManager] Idle shutdown deferred due to active task.");
      this.transitionTo(LifecycleState.ACTIVE);
      this.resetIdleTimer();
      return;
    }

    console.log(`[LifecycleManager] Idle shutdown period expired (${this.idleShutdownTimeoutMs / 1000}s). Returning to DORMANT.`);
    this.enterDormant();
  }

  /**
   * Safely shut down owned backend and transition to DORMANT.
   */
  async enterDormant() {
    if (this.state === LifecycleState.DORMANT || this.state === LifecycleState.SHUTTING_DOWN) {
      return;
    }

    this._clearTimers();
    this.transitionTo(LifecycleState.SHUTTING_DOWN);

    try {
      this.hideWindow();
      console.log("[LifecycleManager] Terminating backend for DORMANT state...");
      this.backendManager.stop();
    } catch (err) {
      console.error("[LifecycleManager] Error stopping backend during dormant transition:", err);
    } finally {
      this.transitionTo(LifecycleState.DORMANT);
      console.log("[LifecycleManager] Mamba Shell is now DORMANT. Waiting for user activation.");
    }
  }

  _clearTimers() {
    if (this.idleTimer) {
      clearTimeout(this.idleTimer);
      this.idleTimer = null;
    }
    if (this.idleShutdownTimer) {
      clearTimeout(this.idleShutdownTimer);
      this.idleShutdownTimer = null;
    }
  }

  /**
   * Final teardown when exiting application.
   */
  teardown() {
    this._clearTimers();
    this.backendManager.stop();
  }
}

module.exports = { LifecycleManager, LifecycleState };
