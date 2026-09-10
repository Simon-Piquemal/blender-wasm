// Adapter: the wasm build running in a browser tab.
//
// A tab cannot listen on a port, so it cannot be a server. The shape is
// therefore inverted compared to the native adapter: a relay (src/relay.js) is
// the rendezvous point, the tab connects to it as a "tab" client, and this
// adapter connects as a "controller". The relay routes by request id.
//
// Everything past that point is identical to the native adapter, because the
// message format is the same one mcp_bridge.py speaks.

import WebSocket from "ws";

const DEFAULTS = {
  url: process.env.MCP_RELAY_URL || "ws://127.0.0.1:8787",
  session: process.env.MCP_SESSION || "default",
  token: process.env.MCP_RELAY_TOKEN || null,
  timeoutMs: Number(process.env.MCP_TIMEOUT_MS || 120000),
};

export function createWebAdapter(options = {}) {
  const cfg = { ...DEFAULTS, ...options };

  let ws = null;
  let connecting = null;
  let seq = 0;
  const pending = new Map();

  const failAllPending = (reason) => {
    for (const [, entry] of pending) {
      clearTimeout(entry.timer);
      entry.reject(new Error(reason));
    }
    pending.clear();
  };

  async function connect() {
    if (ws && ws.readyState === WebSocket.OPEN) return ws;
    if (connecting) return connecting;

    connecting = new Promise((resolve, reject) => {
      const sock = new WebSocket(cfg.url);

      sock.on("open", () => {
        sock.send(
          JSON.stringify({ role: "controller", session: cfg.session, token: cfg.token ?? undefined }),
        );
        ws = sock;
        resolve(sock);
      });

      sock.on("message", (data) => {
        let message;
        try {
          message = JSON.parse(data.toString("utf8"));
        } catch {
          return;
        }
        const entry = pending.get(message.id);
        if (!entry) return;
        pending.delete(message.id);
        clearTimeout(entry.timer);
        entry.resolve(message);
      });

      sock.on("error", (err) => {
        if (ws !== sock) reject(err);
      });
      sock.on("close", () => {
        if (ws === sock) ws = null;
        failAllPending("relay connection closed");
      });
    });

    try {
      return await connecting;
    } finally {
      connecting = null;
    }
  }

  return {
    name: "web",

    async call(op, args) {
      const sock = await connect();
      const id = ++seq;

      return new Promise((resolve, reject) => {
        const timer = setTimeout(() => {
          pending.delete(id);
          reject(
            new Error(
              `the tab did not answer '${op}' within ${cfg.timeoutMs} ms ` +
                `(is a tab connected to session '${cfg.session}'?)`,
            ),
          );
        }, cfg.timeoutMs);
        pending.set(id, { resolve, reject, timer });
        sock.send(JSON.stringify({ id, op, args: args ?? {} }), (err) => {
          if (!err) return;
          pending.delete(id);
          clearTimeout(timer);
          reject(err);
        });
      });
    },

    async close() {
      failAllPending("adapter closed");
      ws?.close();
      ws = null;
    },
  };
}
