/**
 * Electron Preload Script
 *
 * Exposes minimal platform lifecycle bridge to the renderer.
 * Strictly maintains contextIsolation and does NOT expose Node.js internals or arbitrary execution.
 */

const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("mambaDesktop", {
  isDesktop: true,
  platform: process.platform,
  version: "1.0.0",
  getLifecycleState: () => ipcRenderer.sendSync("mamba:get-lifecycle-state"),
  getAutoStart: () => ipcRenderer.sendSync("mamba:get-autostart") === true,
  setAutoStart: (enabled) => ipcRenderer.sendSync("mamba:set-autostart", enabled === true) === true,
  onLifecycleState: (callback) => {
    if (typeof callback !== "function") return () => {};
    const handler = (_event, state) => callback(state);
    ipcRenderer.on("mamba:lifecycle-state", handler);
    return () => ipcRenderer.removeListener("mamba:lifecycle-state", handler);
  },
  reportActivity: (type) => ipcRenderer.send("mamba:report-activity", type),
  reportTaskState: (active) => ipcRenderer.send("mamba:report-task-state", active),
  reportAwaitingPermission: (awaiting) => ipcRenderer.send("mamba:awaiting-permission", awaiting),
  reportVoiceState: (active) => ipcRenderer.send("mamba:report-voice-state", active),
  reportState: (state) => ipcRenderer.send("mamba:report-state", state),
  onState: (callback) => {
    if (typeof callback !== "function") return () => {};
    const handler = (_event, state) => callback(state);
    ipcRenderer.on("mamba:state", handler);
    return () => ipcRenderer.removeListener("mamba:state", handler);
  },
  activate: () => ipcRenderer.send("mamba:activate"),
  toggleMainWindow: () => ipcRenderer.send("mamba:toggle-main"),
});

// Non-invasive activity and task-state observation via WebSocket bridge
try {
  window.addEventListener("DOMContentLoaded", () => {
    const OriginalWebSocket = window.WebSocket;
    if (OriginalWebSocket) {
      window.WebSocket = function (...args) {
        const ws = new OriginalWebSocket(...args);
        const url = args[0] ? String(args[0]) : "";

        if (url.includes("/live")) {
          ws.addEventListener("message", (event) => {
            try {
              const data = JSON.parse(event.data);
              if (data.type === "transcription" && data.role === "user") {
                ipcRenderer.send("mamba:report-activity", "user-transcription");
                ipcRenderer.send("mamba:report-task-state", true);
              } else if (data.type === "status") {
                if (data.status === "thinking" || data.status === "speaking") {
                  ipcRenderer.send("mamba:report-activity", `status-${data.status}`);
                  ipcRenderer.send("mamba:report-task-state", true);
                } else if (data.status === "listening" || data.status === "idle") {
                  ipcRenderer.send("mamba:report-task-state", false);
                } else if (data.status === "permission") {
                  ipcRenderer.send("mamba:awaiting-permission", true);
                }
              } else if (data.type === "turnComplete") {
                ipcRenderer.send("mamba:report-activity", "turn-complete");
                ipcRenderer.send("mamba:report-task-state", false);
                ipcRenderer.send("mamba:awaiting-permission", false);
              } else if (data.type === "permission_request") {
                ipcRenderer.send("mamba:awaiting-permission", true);
              }
            } catch {}
          });

          const originalSend = ws.send;
          ws.send = function (data) {
            ipcRenderer.send("mamba:report-activity", "ws-send");
            try {
              const parsed = JSON.parse(data);
              if (parsed.type === "text" || parsed.type === "audio") {
                ipcRenderer.send("mamba:report-task-state", true);
              } else if (parsed.type === "permission_response") {
                ipcRenderer.send("mamba:awaiting-permission", false);
              }
            } catch {}
            return originalSend.apply(this, arguments);
          };
        }
        return ws;
      };
      window.WebSocket.prototype = OriginalWebSocket.prototype;
    }
  });
} catch (err) {
  console.warn("[Preload] Could not hook WebSocket:", err);
}
