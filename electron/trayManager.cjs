/**
 * Mamba Desktop Shell — Tray Manager
 *
 * Manages the Windows notification area (system tray) icon and context menu.
 * Purely a presentation and window lifecycle boundary.
 */

const { Tray, Menu, nativeImage } = require("electron");
const path = require("path");
const fs = require("fs");

class TrayManager {
  constructor(options = {}) {
    this.iconPath = options.iconPath || path.join(__dirname, "assets", "icon.png");
    this.onShow = options.onShow || (() => {});
    this.onHide = options.onHide || (() => {});
    this.onQuit = options.onQuit || (() => {});
    this.tray = null;
  }

  create() {
    if (this.tray && !this.tray.isDestroyed()) {
      return this.tray;
    }

    let icon = null;
    if (fs.existsSync(this.iconPath)) {
      icon = nativeImage.createFromPath(this.iconPath);
    }

    if (!icon || icon.isEmpty()) {
      console.warn(`[TrayManager] Warning: Tray icon at "${this.iconPath}" not found or empty. Using empty fallback.`);
      icon = nativeImage.createEmpty();
    }

    this.tray = new Tray(icon);
    this.tray.setToolTip("Mamba AI");

    const contextMenu = Menu.buildFromTemplate([
      {
        label: "Show Mamba",
        click: () => this.onShow(),
      },
      {
        label: "Hide Mamba",
        click: () => this.onHide(),
      },
      { type: "separator" },
      {
        label: "Quit Mamba",
        click: () => this.onQuit(),
      },
    ]);

    this.tray.setContextMenu(contextMenu);

    // Left-click restores / shows window
    this.tray.on("click", () => {
      this.onShow();
    });

    // Double-click restores / shows window
    this.tray.on("double-click", () => {
      this.onShow();
    });

    console.log("[TrayManager] System tray icon and context menu initialized successfully.");
    return this.tray;
  }

  destroy() {
    if (this.tray && !this.tray.isDestroyed()) {
      this.tray.destroy();
      this.tray = null;
      console.log("[TrayManager] System tray destroyed cleanly.");
    }
  }
}

module.exports = { TrayManager };
