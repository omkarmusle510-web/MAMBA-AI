/**
 * Frontend Lifecycle and URL Resolver for Electron Shell
 *
 * Responsibilities:
 * - Detects running Vite dev server (http://localhost:5173 or http://127.0.0.1:5173)
 * - Supports dev mode by either reusing existing Vite instance or spawning one
 * - In production/preview mode, starts lightweight StaticServer for dist/
 * - Cleans up any child Vite process or static server when Electron exits
 */

const http = require("http");
const path = require("path");
const fs = require("fs");
const { spawn, execSync } = require("child_process");
const { StaticServer } = require("./staticServer.cjs");

class FrontendManager {
  constructor(options = {}) {
    this.rootDir = options.rootDir || path.resolve(__dirname, "..");
    this.distDir = path.join(this.rootDir, "dist");
    this.backendPort = options.backendPort || 8000;
    this.vitePort = 5173;
    this.viteProcess = null;
    this.staticServer = null;
  }

  /**
   * Check if Vite dev server is running on a given host.
   */
  async checkHost(host, timeoutMs = 800) {
    return new Promise((resolve) => {
      const req = http.get(`http://${host}:${this.vitePort}`, { timeout: timeoutMs }, (res) => {
        resolve(res.statusCode >= 200 && res.statusCode < 500);
      });
      req.on("error", () => resolve(false));
      req.on("timeout", () => {
        req.destroy();
        resolve(false);
      });
    });
  }

  /**
   * Check if Vite dev server is already running and reachable.
   */
  async getActiveViteUrl() {
    if (await this.checkHost("localhost")) return `http://localhost:${this.vitePort}`;
    if (await this.checkHost("127.0.0.1")) return `http://127.0.0.1:${this.vitePort}`;
    return null;
  }

  /**
   * Resolve appropriate frontend URL:
   * 1. If existing Vite dev server is running -> use it
   * 2. If isDev requested and not running -> spawn Vite dev server
   * 3. Otherwise -> serve dist/ via StaticServer
   */
  async resolveUrl(isDev = false) {
    // 1. Check if Vite dev server is already running (e.g. from 'npm run dev' in another terminal)
    const existingViteUrl = await this.getActiveViteUrl();
    if (existingViteUrl) {
      console.log(`[FrontendManager] Connected to active Vite dev server at ${existingViteUrl}`);
      return { url: existingViteUrl, mode: "vite-existing" };
    }

    // 2. If explicitly requested dev mode, spawn Vite dev server
    if (isDev) {
      console.log("[FrontendManager] Spawning Vite dev server for desktop dev mode...");
      const npxCmd = process.platform === "win32" ? "npx.cmd" : "npx";
      try {
        this.viteProcess = spawn(npxCmd, ["vite", "--port", this.vitePort.toString()], {
          cwd: this.rootDir,
          env: { ...process.env },
          stdio: ["ignore", "pipe", "pipe"],
          // .cmd shims require a shell on Windows (Node >= 20 spawn EINVAL).
          shell: process.platform === "win32",
        });
      } catch (err) {
        console.warn(`[FrontendManager] Vite spawn failed (${(err && err.message) || err}). Falling back to built static assets.`);
        this.viteProcess = null;
      }

      if (this.viteProcess) {
        this.viteProcess.stdout.on("data", (data) => {
          const text = data.toString().trim();
          if (text) console.log(`[Vite] ${text}`);
        });
        this.viteProcess.stderr.on("data", (data) => {
          const text = data.toString().trim();
          if (text) console.error(`[Vite Error] ${text}`);
        });
      }

      // Poll until ready
      const start = Date.now();
      while (Date.now() - start < 15000) {
        const url = await this.getActiveViteUrl();
        if (url) {
          console.log(`[FrontendManager] Vite dev server ready at ${url}`);
          return { url, mode: "vite-spawned" };
        }
        await new Promise((r) => setTimeout(r, 300));
      }
      console.warn("[FrontendManager] Vite spawn timed out. Falling back to built static assets.");
    }

    // 3. Fallback to dist/ StaticServer
    const indexHtml = path.join(this.distDir, "index.html");
    if (!fs.existsSync(indexHtml)) {
      console.log("[FrontendManager] dist/index.html not found. Building frontend...");
      const npmCmd = process.platform === "win32" ? "npm.cmd" : "npm";
      execSync(`${npmCmd} run build`, { cwd: this.rootDir, stdio: "inherit" });
    }

    this.staticServer = new StaticServer(this.distDir, this.backendPort);
    const staticUrl = await this.staticServer.start();
    return { url: staticUrl, mode: "static-server" };
  }

  stop() {
    if (this.viteProcess) {
      const pid = this.viteProcess.pid;
      console.log(`[FrontendManager] Terminating spawned Vite process PID: ${pid}`);
      try {
        if (process.platform === "win32") {
          execSync(`taskkill /pid ${pid} /T /F`, { stdio: "ignore" });
        } else {
          this.viteProcess.kill("SIGTERM");
        }
      } catch {}
      this.viteProcess = null;
    }

    if (this.staticServer) {
      console.log("[FrontendManager] Stopping static server.");
      this.staticServer.stop();
      this.staticServer = null;
    }
  }
}

module.exports = { FrontendManager };
