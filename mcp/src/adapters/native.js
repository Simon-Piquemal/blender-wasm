// Adapter: native Blender on a server (or your workstation).
//
// Blender runs headless with mcp_bridge.py, which owns a blocking socket loop
// and answers line-delimited JSON. We either spawn that process ourselves and
// keep it for the life of the MCP server, or attach to one that is already
// running (MCP_BLENDER_ATTACH=1) — useful when you want to watch the session in
// a real Blender window, or when Blender is managed by systemd on the VPS.

import net from "node:net";
import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const BRIDGE = path.resolve(HERE, "../../blender/mcp_bridge.py");

const DEFAULTS = {
  blender: process.env.MCP_BLENDER_BIN || "blender",
  host: process.env.MCP_BLENDER_HOST || "127.0.0.1",
  port: Number(process.env.MCP_BLENDER_PORT || 9876),
  attach: process.env.MCP_BLENDER_ATTACH === "1",
  blendFile: process.env.MCP_BLENDER_FILE || null,
  timeoutMs: Number(process.env.MCP_TIMEOUT_MS || 120000),
};

export function createNativeAdapter(options = {}) {
  const cfg = { ...DEFAULTS, ...options };

  let child = null;
  let socket = null;
  let connecting = null;
  let buffer = "";
  let seq = 0;
  const pending = new Map();

  const failAllPending = (reason) => {
    for (const [, entry] of pending) {
      clearTimeout(entry.timer);
      entry.reject(new Error(reason));
    }
    pending.clear();
  };

  async function spawnBlender() {
    if (cfg.attach) return;
    const args = ["--background"];
    // A .blend to start from: without one Blender opens its startup file, which
    // is usually what you want for generation but never for editing an asset.
    if (cfg.blendFile) args.push(cfg.blendFile);
    args.push("--python", BRIDGE, "--", "--transport=socket", `--host=${cfg.host}`, `--port=${cfg.port}`);

    child = spawn(cfg.blender, args, { stdio: ["ignore", "pipe", "pipe"] });
    child.on("exit", (code, signal) => {
      child = null;
      socket = null;
      failAllPending(`blender exited (code=${code} signal=${signal})`);
    });
    // Blender is chatty on stdout; only its stderr is worth surfacing, and it
    // must go to OUR stderr because stdout is the MCP stdio transport.
    child.stderr.on("data", (chunk) => process.stderr.write(`[blender] ${chunk}`));
    child.stdout.on("data", () => {});

    // The bridge prints "listening on ..." when ready, but waiting on a log line
    // is brittle; retrying the connect is both simpler and more honest.
    await waitForPort(cfg.host, cfg.port, 30000);
  }

  async function connect() {
    if (socket && !socket.destroyed) return socket;
    if (connecting) return connecting;

    connecting = (async () => {
      if (!cfg.attach && !child) await spawnBlender();
      else await waitForPort(cfg.host, cfg.port, 5000);

      const sock = net.connect(cfg.port, cfg.host);
      await new Promise((resolve, reject) => {
        sock.once("connect", resolve);
        sock.once("error", reject);
      });
      sock.setNoDelay(true);

      sock.on("data", (chunk) => {
        buffer += chunk.toString("utf8");
        let nl;
        while ((nl = buffer.indexOf("\n")) >= 0) {
          const line = buffer.slice(0, nl);
          buffer = buffer.slice(nl + 1);
          if (!line.trim()) continue;
          let message;
          try {
            message = JSON.parse(line);
          } catch {
            continue;
          }
          const entry = pending.get(message.id);
          if (!entry) continue;
          pending.delete(message.id);
          clearTimeout(entry.timer);
          entry.resolve(message);
        }
      });
      // The bridge closes the connection when it goes away; that surfaces here
      // as either 'close' or ECONNRESET and means the same thing to us.
      sock.on("error", () => {});
      sock.on("close", () => {
        if (socket === sock) socket = null;
        failAllPending("connection to blender closed");
      });

      socket = sock;
      return sock;
    })();

    try {
      return await connecting;
    } finally {
      connecting = null;
    }
  }

  return {
    name: "native",

    async call(op, args) {
      const sock = await connect();
      const id = ++seq;
      const message = JSON.stringify({ id, op, args: args ?? {} }) + "\n";

      return new Promise((resolve, reject) => {
        const timer = setTimeout(() => {
          pending.delete(id);
          reject(new Error(`blender did not answer '${op}' within ${cfg.timeoutMs} ms`));
        }, cfg.timeoutMs);
        pending.set(id, { resolve, reject, timer });
        sock.write(message, (err) => {
          if (!err) return;
          pending.delete(id);
          clearTimeout(timer);
          reject(err);
        });
      });
    },

    async close() {
      failAllPending("adapter closed");
      socket?.destroy();
      socket = null;
      if (child) {
        child.kill();
        child = null;
      }
    },
  };
}

function waitForPort(host, port, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  return new Promise((resolve, reject) => {
    const attempt = () => {
      const probe = net.connect(port, host);
      probe.once("connect", () => {
        probe.destroy();
        resolve();
      });
      probe.once("error", () => {
        probe.destroy();
        if (Date.now() > deadline) {
          reject(new Error(`nothing listening on ${host}:${port} after ${timeoutMs} ms`));
        } else {
          setTimeout(attempt, 250);
        }
      });
    };
    attempt();
  });
}
