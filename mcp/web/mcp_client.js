// SPDX-License-Identifier: GPL-2.0-or-later
/* Tab half of the MCP bridge: relay socket <-> the wasm filesystem mailbox.
 *
 * The tab is the only place where the two ends can meet. It holds a WebSocket
 * to the relay (mcp/src/relay.js) -- an outbound connection, because a tab
 * cannot listen -- and it holds the Emscripten `Module`, which is the only
 * handle anything has on the filesystem the in-page Blender sees.
 *
 * Per request: drop a JSON file into the bridge's input directory, poll the
 * output directory for a file of the same name, read it, delete it, send it
 * back. That is the `queue` transport in mcp/blender/mcp_bridge.py
 * (serve_queue), drained on Blender's own thread from a bpy.app.timers
 * callback -- which is the whole point of the file mailbox: nothing here ever
 * touches bpy, so nothing here can corrupt Blender's state by running on the
 * wrong thread.
 *
 * Write ordering matches the bridge's, mirrored. The bridge writes its answer
 * to "<name>.part" and os.replace()s it into place so a reader never sees half
 * an answer; this file writes its request to "<name>.part" and renames it, so
 * the drain -- which only picks up "*.json" -- never parses half a request.
 *
 * ---------------------------------------------------------------------------
 * THREADING: THE PART THAT IS NOT PROVEN
 *
 * This module runs on the page's main thread. Blender runs on a pthread
 * (-sPROXY_TO_PTHREAD), and the filesystem is WASMFS (-sWASMFS). The claim
 * this file rests on is that a WASMFS write from the main thread is visible to
 * the Blender thread, because WASMFS keeps its file table in the shared linear
 * memory (a SharedArrayBuffer under -pthread) behind its own locks, and the JS
 * `FS` shim is a thin wrapper over the same wasm entry points every thread
 * calls. demo/provider_backend.cpp states the same model from the C side ("the
 * FS syscall runs on the calling worker and holds WasmFS's locks throughout"),
 * and demo/src/main.js already does main-thread FS work against files the
 * Blender thread produced (__blenderSaveDownload reads and unlinks a .blend
 * that Blender wrote; onRuntimeInitialized mkdir's the mount root).
 *
 * That is an argument, not a test. It has NOT been run end to end. Verify at
 * runtime, and read `selfTest()` below -- it exists to answer exactly this.
 *
 * One consequence is load-bearing rather than incidental: the mailbox
 * directories MUST live on an in-memory WASMFS directory (/tmp/... by
 * default). Do not point them at /opfs, at a provider mount (/assets) or at a
 * localdir mount. Those backends proxy their I/O to another thread and BLOCK
 * the caller until it finishes; the main thread is not allowed to block, so a
 * mailbox on one of them deadlocks the page instead of failing.
 * ---------------------------------------------------------------------------
 */

const DEFAULTS = {
  url: "ws://127.0.0.1:8787",
  session: "default",
  token: "",
  /* Must match the bridge's `--transport=queue --in=... --out=...`. These are
   * mcp_bridge.main()'s own defaults. /tmp is a plain in-memory WASMFS
   * directory; see the threading note above for why that matters. */
  inDir: "/tmp/mcp/in",
  outDir: "/tmp/mcp/out",
  /* Answer polling. The bridge's timer ticks every 100 ms, so a tight first
   * phase catches cheap ops in about one tick, and the slow phase keeps a
   * 40-second render from costing thousands of wakeups. */
  pollFastMs: 20,
  pollSlowMs: 100,
  pollFastForMs: 1000,
  /* Deliberately longer than the relay's per-request timeout (30 s). The relay
   * decides what the controller waits for; this only decides when the tab
   * stops believing Blender will ever answer, and a render legitimately takes
   * longer than the relay is willing to wait. */
  requestTimeoutMs: 180_000,
  /* Reconnect backoff, doubling with jitter. */
  reconnectMinMs: 500,
  reconnectMaxMs: 15_000,
  /* Blender is single-threaded from the mailbox's point of view -- the drain
   * runs one request after another on one timer -- so flooding it buys nothing
   * and makes every answer late. The cap is a guard, not a scheduler. */
  maxInFlight: 8,
  module: null, // defaults to globalThis.Module
  log: null,
};

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const utf8 = new TextEncoder();
const fromUtf8 = new TextDecoder("utf-8");

/* --- filesystem helpers -----------------------------------------------------
 *
 * All of these go through Module.FS, which is in EXPORTED_RUNTIME_METHODS (see
 * scripts/link_blender_release.sh). WASMFS implements a subset of the legacy
 * FS API, so anything beyond read/write/unlink/mkdir is probed rather than
 * assumed.
 */

function fsOf(module) {
  const m = module || globalThis.Module;
  const FS = m && m.FS;
  if (!FS || typeof FS.writeFile !== "function" || typeof FS.readFile !== "function") return null;
  return FS;
}

async function waitForFs(module, log, timeoutMs = 120_000) {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const FS = fsOf(module);
    if (FS) return FS;
    if (Date.now() > deadline) throw new Error("Module.FS never appeared (is blender.js loaded?)");
    await sleep(200);
  }
}

function mkdirTree(FS, path) {
  if (typeof FS.mkdirTree === "function") {
    try {
      FS.mkdirTree(path);
      return;
    } catch {
      /* fall through to the manual walk */
    }
  }
  let acc = "";
  for (const part of path.split("/")) {
    if (!part) continue;
    acc += "/" + part;
    try {
      FS.mkdir(acc);
    } catch {
      /* already exists, or the parent is not a directory -- the first real
       * write will report that far more precisely than we could here */
    }
  }
}

/* Write, then rename into place. If FS.rename is missing (WASMFS does not
 * implement the whole legacy FS surface), fall back to a direct write and say
 * so once: the bridge would then have a small window in which it can read a
 * partly-written request. It survives that -- json.load fails, it answers
 * {"ok": false, "error": "bad request: ..."} and deletes the file -- so the
 * failure mode is a spurious error, not a hang or a corrupt scene. */
let renameWarned = false;
function writeAtomic(FS, dir, name, text, log) {
  const finalPath = `${dir}/${name}`;
  const bytes = utf8.encode(text);
  if (typeof FS.rename === "function") {
    /* ".part" is invisible to the drain, which filters on ".json". */
    const tmp = `${finalPath}.part`;
    FS.writeFile(tmp, bytes);
    FS.rename(tmp, finalPath);
    return;
  }
  if (!renameWarned) {
    renameWarned = true;
    log("FS.rename unavailable — requests are written non-atomically");
  }
  FS.writeFile(finalPath, bytes);
}

/* Absent file vs. real error. Emscripten raises ErrnoError; ENOENT is 44 in
 * the current errno table and 2 in the legacy one, and the message wording has
 * moved around between releases, so match generously. Anything unrecognised is
 * reported rather than silently retried forever. */
function isNotFound(err) {
  if (!err) return false;
  if (err.errno === 44 || err.errno === 2 || err.code === "ENOENT") return true;
  return /no such file|not found|ENOENT/i.test(String(err.message || err));
}

function readJsonIfPresent(FS, path) {
  let bytes;
  try {
    bytes = FS.readFile(path);
  } catch (err) {
    if (isNotFound(err)) return undefined;
    throw err;
  }
  /* The bridge renames its answer into place, so a file that exists is a whole
   * file. A zero-length read would mean the rename is not atomic on this
   * backend after all -- worth retrying rather than throwing. */
  if (!bytes || bytes.length === 0) return undefined;
  return JSON.parse(fromUtf8.decode(bytes));
}

function tryUnlink(FS, path) {
  try {
    FS.unlink(path);
  } catch {
    /* already gone */
  }
}

/* --- the client -------------------------------------------------------------
 */

export function startMcpClient(options = {}) {
  const cfg = { ...DEFAULTS, ...options };
  const log =
    cfg.log ||
    ((...parts) => console.info("[mcp-client]", ...parts));

  let ws = null;
  let stopped = false;
  let registered = false;
  let attempt = 0;
  let reconnectTimer = null;
  let FS = null;
  let seq = 0;
  let inFlight = 0;
  const state = { connected: false, registered: false, inFlight: 0, served: 0, failed: 0 };

  /* Unique per request AND per session: the relay's ids restart at 1 whenever
   * the relay restarts, and a stale answer file left over from a previous run
   * under the same name would be picked up as this request's answer. */
  const runTag = Math.random().toString(36).slice(2, 8);
  const fileName = (id) => `${String(seq++).padStart(6, "0")}-${runTag}-${sanitize(id)}.json`;

  function sanitize(id) {
    return String(id).replace(/[^A-Za-z0-9_-]/g, "_").slice(0, 32);
  }

  function reply(obj) {
    if (ws && ws.readyState === 1 /* OPEN */) {
      try {
        ws.send(JSON.stringify(obj));
      } catch (err) {
        log("send failed:", err && err.message);
      }
    }
    /* If the socket died mid-request the answer is simply lost. That is the
     * right outcome: the relay already rejected the in-flight request when the
     * tab dropped, and the controller has been told. */
  }

  async function awaitAnswer(path, deadline) {
    const fastUntil = Date.now() + cfg.pollFastForMs;
    for (;;) {
      const answer = readJsonIfPresent(FS, path);
      if (answer !== undefined) return answer;
      if (Date.now() > deadline) return undefined;
      await sleep(Date.now() < fastUntil ? cfg.pollFastMs : cfg.pollSlowMs);
    }
  }

  /* `sink` is where the answer goes. It is the relay socket for real traffic
   * and a local promise for selfTest(), which is the only reason this is a
   * parameter rather than a hard-wired reply(). */
  async function handleRequest(message, sink = reply) {
    const id = message.id;
    if (inFlight >= cfg.maxInFlight) {
      sink({ id, ok: false, error: `tab: too many requests in flight (${cfg.maxInFlight})` });
      return;
    }
    inFlight += 1;
    state.inFlight = inFlight;

    const name = fileName(id);
    const inPath = `${cfg.inDir}/${name}`;
    const outPath = `${cfg.outDir}/${name}`;
    try {
      writeAtomic(FS, cfg.inDir, name, JSON.stringify(message), log);
      const answer = await awaitAnswer(outPath, Date.now() + cfg.requestTimeoutMs);
      if (answer === undefined) {
        /* Nothing came back. Either the bridge is not armed on the Blender
         * side, or -- the open question this whole file carries -- the write
         * never became visible to Blender's thread. Clean up both sides so a
         * later drain does not answer a request nobody is waiting for. */
        tryUnlink(FS, inPath);
        tryUnlink(FS, `${inPath}.part`);
        state.failed += 1;
        sink({
          id,
          ok: false,
          error:
            `tab: no answer in ${cfg.requestTimeoutMs} ms via ${cfg.inDir} -> ${cfg.outDir}. ` +
            "Check that mcp_bridge.py is running with --transport=queue and the same directories.",
        });
        return;
      }
      tryUnlink(FS, outPath);
      state.served += 1;
      /* The bridge echoes the id it was given, but trust the one we routed on:
       * the relay correlates strictly by id and a mismatch would strand the
       * controller. */
      sink({ ...answer, id });
    } catch (err) {
      state.failed += 1;
      tryUnlink(FS, outPath);
      sink({ id, ok: false, error: `tab: ${err && err.message ? err.message : String(err)}` });
    } finally {
      inFlight -= 1;
      state.inFlight = inFlight;
    }
  }

  function onMessage(ev) {
    let message;
    try {
      message = JSON.parse(typeof ev.data === "string" ? ev.data : String(ev.data));
    } catch (err) {
      log("bad JSON from relay:", err && err.message);
      return;
    }
    /* Relay housekeeping ({"type":"relay",...}) never carries an id and is
     * never a request. */
    if (message && message.type === "relay") {
      if (message.event === "registered") {
        registered = true;
        state.registered = true;
        attempt = 0; // a completed handshake is what resets the backoff, not a TCP connect
        log(`registered as tab on session ${JSON.stringify(cfg.session)}`);
      }
      return;
    }
    if (!message || typeof message !== "object" || message.id === undefined) {
      log("ignoring a frame with no id");
      return;
    }
    void handleRequest(message);
  }

  function scheduleReconnect() {
    if (stopped || reconnectTimer) return;
    /* Exponential with a ceiling, plus jitter so a relay restart does not get
     * every open tab back at the same millisecond. */
    const base = Math.min(cfg.reconnectMaxMs, cfg.reconnectMinMs * 2 ** attempt);
    const delay = Math.round(base * (0.5 + Math.random() * 0.5));
    attempt += 1;
    log(`reconnecting in ${delay} ms`);
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      connect();
    }, delay);
  }

  function connect() {
    if (stopped) return;
    try {
      ws = new WebSocket(cfg.url);
    } catch (err) {
      log("connect failed:", err && err.message);
      scheduleReconnect();
      return;
    }

    ws.onopen = () => {
      state.connected = true;
      const hello = { role: "tab", session: cfg.session };
      if (cfg.token) hello.token = cfg.token;
      ws.send(JSON.stringify(hello));
    };
    ws.onmessage = onMessage;
    ws.onerror = () => {
      /* Browsers give no detail here on purpose; 'close' carries the code. */
    };
    ws.onclose = (ev) => {
      state.connected = false;
      state.registered = false;
      registered = false;
      ws = null;
      /* 4001 is the relay's "unauthorized". Reconnecting cannot fix a wrong
       * token, so stop rather than hammer the port forever. */
      if (ev && ev.code === 4001) {
        stopped = true;
        log("relay rejected this tab: unauthorized (check the token)");
        return;
      }
      log(`socket closed (${ev && ev.code})`);
      scheduleReconnect();
    };
  }

  const ready = (async () => {
    FS = await waitForFs(cfg.module, log);
    /* The bridge mkdir's these too, but it may not be armed yet, and the first
     * request must not race that. */
    mkdirTree(FS, cfg.inDir);
    mkdirTree(FS, cfg.outDir);
    connect();
  })().catch((err) => {
    log("startup failed:", err && err.message);
    throw err;
  });

  return {
    ready,
    get state() {
      return { ...state };
    },
    stop() {
      stopped = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      reconnectTimer = null;
      if (ws) {
        try {
          ws.close(1000, "client stopped");
        } catch {
          /* already closing */
        }
      }
      ws = null;
    },
    /* Bypass the relay and exercise only the mailbox. This is the intended way
     * to settle the open threading question: if this resolves, a main-thread
     * WASMFS write did reach Blender's thread and came back. If it times out,
     * the file path is not viable in this build and the request queue needs a
     * C-side export instead (see the report / module header). */
    async selfTest(op = "capabilities", args = {}) {
      await ready;
      return new Promise((resolve) => {
        handleRequest({ id: `selftest-${Date.now()}`, op, args }, resolve);
      });
    },
  };
}

export default startMcpClient;
