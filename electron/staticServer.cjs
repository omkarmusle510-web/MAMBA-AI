/**
 * Static file server & local gateway for production / built frontend.
 *
 * Serves compiled assets from dist/ and proxies /api and /live (WebSocket)
 * directly to the Python Mamba transport adapter (127.0.0.1:8000).
 *
 * This allows the Electron desktop window to load a standard origin (http://127.0.0.1:<port>)
 * preserving the existing React interface and WebSocket protocol without any React modifications.
 */

const http = require("http");
const net = require("net");
const path = require("path");
const fs = require("fs");

const MIME_TYPES = {
  ".html": "text/html; charset=utf-8",
  ".js": "application/javascript; charset=utf-8",
  ".mjs": "application/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".gif": "image/gif",
  ".svg": "image/svg+xml",
  ".ico": "image/x-icon",
  ".wav": "audio/wav",
  ".mp3": "audio/mpeg",
  ".woff": "font/woff",
  ".woff2": "font/woff2",
  ".ttf": "font/ttf",
  ".wasm": "application/wasm",
};

class StaticServer {
  constructor(distDir, backendPort = 8000) {
    this.distDir = distDir;
    this.backendPort = backendPort;
    this.server = null;
    this.port = null;
    this.sockets = new Set();
  }

  start() {
    return new Promise((resolve, reject) => {
      this.server = http.createServer((req, res) => this._handleHttp(req, res));

      // Track active connections for fast teardown
      this.server.on("connection", (socket) => {
        this.sockets.add(socket);
        socket.on("close", () => this.sockets.delete(socket));
      });

      // Handle WebSocket upgrade tunneling
      this.server.on("upgrade", (req, socket, head) => this._handleUpgrade(req, socket, head));

      this.server.on("error", (err) => {
        reject(err);
      });

      // Listen on loopback, automatic free port
      this.server.listen(0, "127.0.0.1", () => {
        this.port = this.server.address().port;
        console.log(`[StaticServer] Serving dist/ on http://127.0.0.1:${this.port}`);
        resolve(`http://127.0.0.1:${this.port}`);
      });
    });
  }

  _handleHttp(req, res) {
    const parsedUrl = new URL(req.url, `http://127.0.0.1:${this.port}`);
    const pathname = parsedUrl.pathname;

    // 1. Proxy /api requests to Python backend
    if (pathname.startsWith("/api/") || pathname === "/health") {
      const proxyReq = http.request(
        {
          hostname: "127.0.0.1",
          port: this.backendPort,
          path: req.url,
          method: req.method,
          headers: {
            ...req.headers,
            host: `127.0.0.1:${this.backendPort}`,
          },
        },
        (proxyRes) => {
          res.writeHead(proxyRes.statusCode, proxyRes.headers);
          proxyRes.pipe(res);
        }
      );

      proxyReq.on("error", (err) => {
        res.writeHead(502, { "Content-Type": "text/plain" });
        res.end(`Bad Gateway (Python Backend): ${err.message}`);
      });

      req.pipe(proxyReq);
      return;
    }

    // 2. Serve static files from dist/
    let safePath = path.normalize(decodeURIComponent(pathname)).replace(/^(\.\.[\/\\])+/, "");
    let filePath = path.join(this.distDir, safePath);

    // If path is a directory or root, serve index.html
    if (fs.existsSync(filePath) && fs.statSync(filePath).isDirectory()) {
      filePath = path.join(filePath, "index.html");
    }

    // SPA fallback: if file does not exist, serve index.html
    if (!fs.existsSync(filePath)) {
      filePath = path.join(this.distDir, "index.html");
    }

    if (!fs.existsSync(filePath)) {
      res.writeHead(404, { "Content-Type": "text/plain" });
      res.end("Not Found (dist/index.html missing. Run 'npm run build' first.)");
      return;
    }

    const ext = path.extname(filePath).toLowerCase();
    const contentType = MIME_TYPES[ext] || "application/octet-stream";

    try {
      const stat = fs.statSync(filePath);
      res.writeHead(200, {
        "Content-Type": contentType,
        "Content-Length": stat.size,
      });
      const stream = fs.createReadStream(filePath);
      stream.pipe(res);
    } catch (err) {
      res.writeHead(500, { "Content-Type": "text/plain" });
      res.end(`Internal Server Error: ${err.message}`);
    }
  }

  _handleUpgrade(req, clientSocket, head) {
    // Only forward /live websocket endpoint
    if (!req.url.startsWith("/live")) {
      clientSocket.destroy();
      return;
    }

    const backendSocket = net.connect(this.backendPort, "127.0.0.1", () => {
      // Reconstruct initial HTTP Upgrade handshake
      let rawHeaders = `${req.method} ${req.url} HTTP/${req.httpVersion}\r\n`;
      for (let i = 0; i < req.rawHeaders.length; i += 2) {
        const key = req.rawHeaders[i];
        const val = req.rawHeaders[i + 1];
        if (key.toLowerCase() === "host") {
          rawHeaders += `Host: 127.0.0.1:${this.backendPort}\r\n`;
        } else {
          rawHeaders += `${key}: ${val}\r\n`;
        }
      }
      rawHeaders += "\r\n";

      backendSocket.write(rawHeaders);
      if (head && head.length > 0) {
        backendSocket.write(head);
      }

      // Bi-directional tunnel
      clientSocket.pipe(backendSocket);
      backendSocket.pipe(clientSocket);
    });

    backendSocket.on("error", () => {
      clientSocket.destroy();
    });

    clientSocket.on("error", () => {
      backendSocket.destroy();
    });
  }

  stop() {
    for (const socket of this.sockets) {
      socket.destroy();
    }
    this.sockets.clear();
    if (this.server) {
      try {
        this.server.close();
      } catch {}
      this.server = null;
    }
  }
}

module.exports = { StaticServer };
