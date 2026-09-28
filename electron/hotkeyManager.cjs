/**
 * Mamba Desktop Shell — Global Hotkey Manager
 *
 * Registers system-wide activation shortcut (Ctrl + Space).
 * Purely brings Mamba desktop window to foreground.
 * Does NOT execute tasks, call MambaRuntime, or manipulate external applications.
 */

const { globalShortcut } = require("electron");

class HotkeyManager {
  constructor(options = {}) {
    this.shortcut = options.shortcut || "CommandOrControl+Space";
    this.onActivate = options.onActivate || (() => {});
    this.isRegistered = false;
  }

  register() {
    if (this.isRegistered) return true;

    try {
      this.isRegistered = globalShortcut.register(this.shortcut, () => {
        console.log(`[HotkeyManager] Global shortcut '${this.shortcut}' pressed.`);
        this.onActivate();
      });

      if (!this.isRegistered) {
        console.warn(`[HotkeyManager] Failed to register global shortcut '${this.shortcut}'. Key may be reserved.`);
      } else {
        console.log(`[HotkeyManager] Global shortcut '${this.shortcut}' registered successfully.`);
      }
      return this.isRegistered;
    } catch (err) {
      console.error(`[HotkeyManager] Error registering global shortcut: ${err.message}`);
      return false;
    }
  }

  unregister() {
    if (this.isRegistered) {
      try {
        globalShortcut.unregister(this.shortcut);
        console.log(`[HotkeyManager] Global shortcut '${this.shortcut}' unregistered.`);
      } catch {}
      this.isRegistered = false;
    }
  }
}

module.exports = { HotkeyManager };
