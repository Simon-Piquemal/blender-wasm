// SPDX-License-Identifier: GPL-2.0-or-later
/* WebSocket rendezvous between the MCP server and a browser tab.
 *
 * Why this process exists at all: a tab cannot listen on a port. The native
 * target lets the MCP server dial Blender's socket directly (see
 * mcp/blender/mcp_bridge.py, serve_socket); the web target cannot, so both
 * halves dial *out* to a meeting point instead. That meeting point is this
 * file. It owns no Blender state and understands no operation -- it moves
 * whole JSON messages between two sockets that agreed on a session name.
 *
 * Wire protocol, one JSON object per WebSocket frame.
 *
 *   registration (first frame from either side, mandatory)
 *     -> {"role": "tab"|"controller", "session": "<id>", "token": "<optional>"}
 *     <- {"type": "relay", "event": "registered", "session": ..., "role": ...}
 *
 *   request   controller -> relay -> tab      {"id": 7, "op": ..., "args": {}}
 *   response  tab -> relay -> controller      {"id": 7, "ok": true, ...}
 *
 * The relay also emits unsolicited notices to controllers, always shaped
 * {"type": "relay", "event": ..., ...} and never carrying an "id". A client
 * that correlates strictly on "id" can ignore every frame that has a "type"
 * field; that is the intended filter.
 *
 * Request ids are REWRITTEN on the way through. Two controllers on the same
 * session would otherwise both send id 1 and the relay could not tell the two
 * answers apart. The tab sees a relay-assigned id; the original is restored
 * before the answer goes back out, so neither end has to care.
 */

import { createServer } from "node:http";
import { timingSafeEqual } from "node:crypto";
import { WebSocketServer } from "ws";

const PORT = Number(process.env.MCP_RELAY_PORT || 8787);
const HOST = process.env.MCP_RELAY_HOST || "127.0.0.1";

/* Shared secret. OPTIONAL, and that is a deliberate default for localhost
 * development -- but understand what this relay is before exposing it:
 * anything that registers as a `controller` on a session can drive the Blender
 * on the other end, and the operation set includes `run_python`, i.e.
 * ARBITRARY CODE EXECUTION inside a user's browser session (and, on the native
 * target, on their machine). It is also a way to exfiltrate whatever that
 * Blender can read -- op_export returns file bytes inline. Treat an unbound
 * relay exactly like an open remote shell: bind it to loopback, or set
 * MCP_RELAY_TOKEN and keep the value out of URLs and logs. There is no
 * per-role privilege here on purpose; a token holder is fully trusted. */
const TOKEN = process.env.MCP_RELAY_TOKEN || "";

/* Per-request deadline. The tab may sit behind a Blender that is mid-render,
 * so this is generous; it exists so a controller is never wedged forever, not
 * as a latency budget.
 *
 * It must not be SHORTER than the controller's own MCP_TIMEOUT_MS (120 s by
 * default, see adapters/web.js). If it is, every slow call fails here first and
 * the agent is told "the tab never answered" about a tab that was answering
 * perfectly well -- a wrong diagnosis pointed at the wrong component. The first
 * render in a fresh tab is the case that finds this: it compiles the EEVEE
 * shaders through Tint before it draws a single pixel, and takes far longer
 * than any render after it. Hence the default matches the controller, and
 * MCP_TIMEOUT_MS is honoured when the relay shares an environment with it. */
const TIMEOUT_MS = Number(
  process.env.MCP_RELAY_TIMEOUT_MS || process.env.MCP_TIMEOUT_MS || 120_000,
);

/* Dead-socket reaper. A tab that is force-quit or suspended does not always
 * produce a close frame, and a stale entry in `sessions` would swallow every
 * subsequent request into a socket nobody is reading. */
const HEARTBEAT_MS = Number(process.env.MCP_RELAY_HEARTBEAT_MS || 30_000);

const MAX_PAYLOAD = Number(process.env.MCP_RELAY_MAX_PAYLOAD || 64 * 1024 * 1024);

/* Close codes in the private range (4000-4999). */
const CLOSE_UNAUTHORIZED = 4001;
const CLOSE_BAD_REGISTRATION = 4002;
const CLOSE_REPLACED = 4003;

const log = (...parts) => console.error("[relay]", ...parts);

/* session id -> { tab, controllers:Set } */
const sessions = new Map();
/* relay id -> { controller, originalId, session, timer } */
const pending = new Map();
let nextRelayId = 1;

function session(id) {
  let s = sessions.get(id);
  if (!s) {
    s = { id, tab: null, controllers: new Set() };
    sessions.set(id, s);
  }
  return s;
}

function dropSessionIfIdle(s) {
  if (!s.tab && s.controllers.size === 0) sessions.delete(s.id);
}

function send(ws, obj) {
  if (!ws || ws.readyState !== ws.OPEN) return false;
  try {
    ws.send(JSON.stringify(obj));
    return true;
  } catch (err) {
    log("send failed:", err && err.message);
    return false;
  }
}

function notify(s, event, extra = {}) {
  for (const c of s.controllers) send(c, { type: "relay", event, session: s.id, ...extra });
}

/* Constant-time token check. Length is not secret (it leaks through the
 * comparison anyway), so an early length exit is fine; what matters is not
 * short-circuiting on the first differing byte of an equal-length guess. */
function tokenOk(given) {
  if (!TOKEN) return true;
  const a = Buffer.from(String(given ?? ""), "utf8");
  const b = Buffer.from(TOKEN, "utf8");
  if (a.length !== b.length) return false;
  return timingSafeEqual(a, b);
}

/* --- request lifecycle ------------------------------------------------------
 *
 * settle() is the single exit for a pending request: timeout, tab death and a
 * real answer all funnel through it, so the timer is always cleared and the
 * controller always gets exactly one frame.
 */
function settle(relayId, reply) {
  const entry = pending.get(relayId);
  if (!entry) return false; // already settled -- a late answer after a timeout
  pending.delete(relayId);
  clearTimeout(entry.timer);
  send(entry.controller, { ...reply, id: entry.originalId });
  return true;
}

function failAll(predicate, error) {
  for (const [relayId, entry] of [...pending]) {
    if (predicate(entry)) settle(relayId, { ok: false, error });
  }
}

function forwardRequest(controller, s, message) {
  const originalId = message.id;
  if (originalId === undefined || originalId === null) {
    send(controller, {
      id: null,
      ok: false,
      error: "relay: request has no 'id'; the relay cannot correlate an answer without one",
    });
    return;
  }

  /* Answer instead of hanging. A controller that gets no frame back has no way
   * to tell "no tab attached" from "Blender is thinking", and would sit on its
   * own timeout for every call. */
  if (!s.tab || s.tab.readyState !== s.tab.OPEN) {
    send(controller, {
      id: originalId,
      ok: false,
      error: `relay: no tab attached to session ${JSON.stringify(s.id)}`,
    });
    return;
  }

  const relayId = nextRelayId++;
  const timer = setTimeout(() => {
    settle(relayId, {
      ok: false,
      error: `relay: timed out after ${TIMEOUT_MS} ms waiting for the tab`,
    });
  }, TIMEOUT_MS);
  /* Do not hold the process open for an in-flight request. */
  if (typeof timer.unref === "function") timer.unref();

  pending.set(relayId, { controller, originalId, session: s.id, timer });

  if (!send(s.tab, { ...message, id: relayId })) {
    settle(relayId, { ok: false, error: "relay: tab socket refused the request" });
  }
}

function forwardResponse(s, message) {
  const relayId = message.id;
  if (!pending.has(relayId)) {
    /* Late answer to something already timed out, or a stray frame. Dropping
     * is correct -- the controller has moved on -- but it is worth a line,
     * because a relay that times out while the tab still answers means
     * MCP_RELAY_TIMEOUT_MS is set below what this Blender actually needs. */
    log(`session ${s.id}: dropped answer for unknown id ${JSON.stringify(relayId)}`);
    return;
  }
  const { id: _ignored, ...rest } = message;
  settle(relayId, rest);
}

/* --- server -----------------------------------------------------------------
 */

/* An explicit http server (rather than `new WebSocketServer({ port })`) so a
 * plain GET answers something legible: the first thing anyone does when a tab
 * will not connect is open the URL in a browser. */
const http = createServer((req, res) => {
  if (req.url === "/health") {
    const body = JSON.stringify({
      ok: true,
      sessions: [...sessions.values()].map((s) => ({
        session: s.id,
        tab: Boolean(s.tab),
        controllers: s.controllers.size,
      })),
      pending: pending.size,
      auth: TOKEN ? "token" : "none",
    });
    res.writeHead(200, { "content-type": "application/json" });
    res.end(body);
    return;
  }
  res.writeHead(426, { "content-type": "text/plain" });
  res.end("blender-mcp relay: WebSocket only. Try /health.\n");
});

const wss = new WebSocketServer({ server: http, maxPayload: MAX_PAYLOAD });

wss.on("connection", (ws, req) => {
  const peer = req.socket.remoteAddress;
  ws.isAlive = true;
  ws.on("pong", () => {
    ws.isAlive = true;
  });

  /* Unregistered sockets are anonymous and hold no session. They get one
   * frame's worth of patience and a short deadline; an unauthenticated socket
   * that never speaks must not be able to accumulate. */
  let state = null; // { role, session }
  const registerDeadline = setTimeout(() => {
    if (!state) ws.close(CLOSE_BAD_REGISTRATION, "no registration");
  }, 10_000);
  if (typeof registerDeadline.unref === "function") registerDeadline.unref();

  ws.on("message", (data) => {
    let message;
    try {
      message = JSON.parse(typeof data === "string" ? data : data.toString("utf8"));
    } catch (err) {
      if (state) send(ws, { id: null, ok: false, error: `relay: bad JSON: ${err.message}` });
      else ws.close(CLOSE_BAD_REGISTRATION, "bad JSON");
      return;
    }
    if (message === null || typeof message !== "object" || Array.isArray(message)) {
      if (state) send(ws, { id: null, ok: false, error: "relay: expected a JSON object" });
      else ws.close(CLOSE_BAD_REGISTRATION, "expected a JSON object");
      return;
    }

    /* --- registration (exactly the first frame) --- */
    if (!state) {
      clearTimeout(registerDeadline);
      const role = message.role;
      const id = message.session;
      if (role !== "tab" && role !== "controller") {
        ws.close(CLOSE_BAD_REGISTRATION, "role must be 'tab' or 'controller'");
        return;
      }
      if (typeof id !== "string" || id.length === 0 || id.length > 128) {
        ws.close(CLOSE_BAD_REGISTRATION, "session must be a non-empty string");
        return;
      }
      if (!tokenOk(message.token)) {
        /* No detail in the close reason, and the peer address only at debug
         * volume: a wrong token is either a typo or a probe. */
        log(`rejected ${role} for session ${id} from ${peer}: bad token`);
        ws.close(CLOSE_UNAUTHORIZED, "unauthorized");
        return;
      }

      const s = session(id);
      state = { role, session: id };

      if (role === "tab") {
        if (s.tab && s.tab !== ws) {
          /* Newest wins. The overwhelmingly common cause of a second tab on
           * one session is the page being reloaded before the old socket's
           * close reached us; locking the fresh tab out would strand the
           * session until a TCP timeout. The cost is that a genuine second
           * tab evicts the first, loudly. */
          log(`session ${id}: replacing an existing tab`);
          const old = s.tab;
          s.tab = null;
          old.close(CLOSE_REPLACED, "replaced by a newer tab");
        }
        s.tab = ws;
        /* In-flight requests belonged to the previous tab's Blender. The new
         * tab has a fresh filesystem mailbox and never saw them. */
        failAll((e) => e.session === id, "relay: tab reconnected while the request was in flight");
        notify(s, "tab_online");
      } else {
        s.controllers.add(ws);
        send(ws, {
          type: "relay",
          event: "tab_" + (s.tab ? "online" : "offline"),
          session: id,
        });
      }

      send(ws, { type: "relay", event: "registered", session: id, role, timeout_ms: TIMEOUT_MS });
      log(`session ${id}: ${role} attached (${peer})`);
      return;
    }

    /* --- steady state --- */
    const s = session(state.session);
    if (state.role === "controller") forwardRequest(ws, s, message);
    else forwardResponse(s, message);
  });

  ws.on("close", (code, reason) => {
    clearTimeout(registerDeadline);
    if (!state) return;
    const s = sessions.get(state.session);
    if (!s) return;
    if (state.role === "tab") {
      if (s.tab === ws) {
        s.tab = null;
        /* Every request sitting in that tab's mailbox is unanswerable now.
         * Rejecting is strictly better than letting them ride out the
         * timeout: same outcome, 30 s sooner, with a truthful reason. */
        failAll((e) => e.session === s.id, "relay: tab disconnected while the request was in flight");
        notify(s, "tab_offline");
        log(`session ${s.id}: tab detached (${code} ${reason || ""})`.trimEnd());
      }
    } else {
      s.controllers.delete(ws);
      /* Answers for a gone controller have nowhere to go; drop the entries so
       * the timers and the map do not leak. */
      for (const [relayId, entry] of [...pending]) {
        if (entry.controller === ws) {
          pending.delete(relayId);
          clearTimeout(entry.timer);
        }
      }
      log(`session ${s.id}: controller detached`);
    }
    dropSessionIfIdle(s);
  });

  ws.on("error", (err) => log("socket error:", err && err.message));
});

const heartbeat = setInterval(() => {
  for (const ws of wss.clients) {
    if (ws.isAlive === false) {
      ws.terminate(); // fires 'close', which unwinds the session above
      continue;
    }
    ws.isAlive = false;
    try {
      ws.ping();
    } catch {
      /* terminate() on the next sweep */
    }
  }
}, HEARTBEAT_MS);
if (typeof heartbeat.unref === "function") heartbeat.unref();

function shutdown(signal) {
  log(`${signal}: closing`);
  clearInterval(heartbeat);
  failAll(() => true, "relay: shutting down");
  for (const ws of wss.clients) ws.close(1001, "relay shutting down");
  wss.close(() => http.close(() => process.exit(0)));
  setTimeout(() => process.exit(0), 2000).unref();
}
process.on("SIGINT", () => shutdown("SIGINT"));
process.on("SIGTERM", () => shutdown("SIGTERM"));

http.listen(PORT, HOST, () => {
  log(`listening on ws://${HOST}:${PORT} (auth: ${TOKEN ? "token" : "NONE"}, timeout ${TIMEOUT_MS} ms)`);
  if (!TOKEN && HOST !== "127.0.0.1" && HOST !== "localhost" && HOST !== "::1") {
    /* Loud, because this combination hands remote code execution to anyone who
     * can reach the port. See the MCP_RELAY_TOKEN comment at the top. */
    log("WARNING: bound off-loopback with no MCP_RELAY_TOKEN — anyone who can reach");
    log("WARNING: this port can run arbitrary Python in the attached Blender.");
  }
});
