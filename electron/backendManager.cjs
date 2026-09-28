/**
 * Mamba Backend Lifecycle Manager
 *
 * Strictly manages the lifecycle of the Python transport server:
 *   python app.py --server
 *
 * Responsibilities:
 * - Detects already-running backend on target port
 * - Spawns backend using workspace .venv/Scripts/python.exe or system python
 * - Verifies backend health via GET /health
 * - Terminates only the process it owns upon exit
 * - Leaves externally-started backends untouched
 */

const { spawn, execSync } = require("child_process");
const http = require("http");
const path = require("path");
const fs = require("fs");

class BackendManager {
  constructor(options = {}) {
    this.rootDir = options.rootDir || path.resolve(__dirname, "..");
    this.port = parseInt(process.env.MAMBA_PORT || "8000", 10);
    this.host = "127.0.0.1";
    this.healthUrl = `http://${this.host}:${this.port}/health`;
    this.backendProcess = null;
    this.backendOwned = false;
    this.recentLogs = [];
    this.maxLogs = 100;
  }

  /**
   * Check if backend is reachable and healthy at /health.
   */
  async isHealthy(timeoutMs = 1500) {
    return new Promise((resolve) => {
      const req = http.get(this.healthUrl, { timeout: timeoutMs }, (res) => {
        if (res.statusCode !== 200) {
          resolve(false);
          return;
        }
        let data = "";
        res.on("data", (chunk) => {
          data += chunk;
        });
        res.on("end", () => {
          try {
            const json = JSON.parse(data);
            resolve(json.status === "ok");
          } catch {
            resolve(false);
          }
        });
      });

      req.on("error", () => resolve(false));
      req.on("timeout", () => {
        req.destroy();
        resolve(false);
      });
    });
  }

  /**
   * Locate appropriate Python executable.
   * Prefers local .venv if available on Windows.
   */
  resolvePython() {
    if (process.env.PYTHON_PATH && fs.existsSync(process.env.PYTHON_PATH)) {
      return process.env.PYTHON_PATH;
    }

    const venvPythonWin = path.join(this.rootDir, ".venv", "Scripts", "python.exe");
    if (fs.existsSync(venvPythonWin)) {
      return venvPythonWin;
    }

    const venvPythonPosix = path.join(this.rootDir, ".venv", "bin", "python");
    if (fs.existsSync(venvPythonPosix)) {
      return venvPythonPosix;
    }

    return "python";
  }

  _appendLog(line) {
    const text = line.toString().trim();
    if (!text) return;
    this.recentLogs.push(text);
    if (this.recentLogs.length > this.maxLogs) {
      this.recentLogs.shift();
    }
    console.log(`[Mamba Backend] ${text}`);
  }

  /**
   * Start or attach to Mamba backend.
   * Returns { success: boolean, owned: boolean, url: string }.
   */
  async start(options = {}) {
    const maxWaitMs = options.maxWaitMs || 25000;
    const pollIntervalMs = options.pollIntervalMs || 250;

    // 1. Check if backend is already running
    const alreadyRunning = await this.isHealthy(1000);
    if (alreadyRunning) {
      console.log(`[Mamba Backend] Existing backend detected at ${this.healthUrl}. Reusing.`);
      this.backendOwned = false;
      this.backendProcess = null;
      return {
        ready: true,
        owned: false,
        url: `http://${this.host}:${this.port}`,
      };
    }

    // 2. Spawn python app.py --server
    const pythonExe = this.resolvePython();
    const appPy = path.join(this.rootDir, "app.py");

    console.log(`[Mamba Backend] Spawning: "${pythonExe}" app.py --server`);
    this.backendOwned = true;

    try {
      this.backendProcess = spawn(pythonExe, ["app.py", "--server"], {
        cwd: this.rootDir,
        env: {
          ...process.env,
          PYTHONUNBUFFERED: "1",
          MAMBA_PORT: this.port.toString(),
        },
        stdio: ["ignore", "pipe", "pipe"],
      });
    } catch (spawnErr) {
      this.backendOwned = false;
      this.backendProcess = null;
      throw new Error(`Failed to spawn Python process: ${spawnErr.message}`);
    }

    let earlyExitError = null;

    this.backendProcess.stdout.on("data", (data) => this._appendLog(data));
    this.backendProcess.stderr.on("data", (data) => this._appendLog(data));

    this.backendProcess.on("error", (err) => {
      earlyExitError = err;
      console.error("[Mamba Backend] Process error:", err);
    });

    this.backendProcess.on("exit", (code, signal) => {
      console.log(`[Mamba Backend] Process exited with code ${code}, signal ${signal}`);
      if (this.backendOwned && !this._isStopping) {
        earlyExitError = new Error(
          `Backend process terminated unexpectedly (code: ${code}, signal: ${signal}). Logs:\n${this.recentLogs.slice(-10).join("\n")}`
        );
      }
    });

    // 3. Poll /health until ready or timeout
    const startTime = Date.now();
    while (Date.now() - startTime < maxWaitMs) {
      if (earlyExitError) {
        this.stop();
        throw earlyExitError;
      }

      const healthy = await this.isHealthy(800);
      if (healthy) {
        console.log(`[Mamba Backend] Backend is ready and responding at ${this.healthUrl}`);
        return {
          ready: true,
          owned: true,
          url: `http://${this.host}:${this.port}`,
        };
      }

      await new Promise((r) => setTimeout(r, pollIntervalMs));
    }

    // Timed out waiting
    this.stop();
    const errorDetails = this.recentLogs.slice(-15).join("\n");
    throw new Error(
      `Backend failed to become healthy within ${maxWaitMs / 1000}s.\nRecent output:\n${errorDetails}`
    );
  }

  /**
   * Stop the backend process cleanly, if owned.
   */
  stop() {
    if (!this.backendOwned || !this.backendProcess) {
      console.log("[Mamba Backend] No owned backend process to terminate.");
      return;
    }

    this._isStopping = true;
    const pid = this.backendProcess.pid;
    console.log(`[Mamba Backend] Terminating owned backend process PID: ${pid}`);

    try {
      if (process.platform === "win32") {
        // Use taskkill to cleanly eliminate the process tree for this specific PID
        execSync(`taskkill /pid ${pid} /T /F`, { stdio: "ignore" });
      } else {
        this.backendProcess.kill("SIGTERM");
      }
    } catch (err) {
      // Process may already be dead
    }

    this.backendProcess = null;
    this.backendOwned = false;
    this._isStopping = false;
  }
}

module.exports = { BackendManager };
