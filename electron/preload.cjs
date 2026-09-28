/**
 * Electron Preload Script
 *
 * Exposes minimal platform lifecycle flags to the renderer.
 * Strictly maintains contextIsolation and does NOT expose Node.js internals or arbitrary execution.
 */

const { contextBridge } = require("electron");

contextBridge.exposeInMainWorld("mambaDesktop", {
  isDesktop: true,
  platform: process.platform,
  version: "1.0.0",
});
