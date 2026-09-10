# Blender MCP bridge

One tool contract for AI agents, two interchangeable targets:

| target | what it drives | transport |
|---|---|---|
| `native` | headless Blender on a VPS or your workstation | TCP socket |
| `web` | the wasm build in a browser tab | WebSocket relay → filesystem mailbox |

The agent cannot tell them apart: same tool names, same arguments, same result
shapes. That is the point — develop against `native` (fast, scriptable, testable
in CI), switch to `web` when a human needs to watch the viewport, and leave the
harness above untouched.

```
Agent ──MCP/stdio──► src/server.js ─┬─ adapters/native.js ─socket─► blender --background + mcp_bridge.py
                                    └─ adapters/web.js ──ws──► src/relay.js ──ws──► tab ──FS──► mcp_bridge.py
```

## Why not the existing Blender MCP addons

The community addons ([blender-mcp](https://github.com/ahujasid/blender-mcp) and
its forks) open a **TCP socket server inside Blender**. That cannot work in a
browser: the wasm build has no sockets at all, and a tab cannot listen on a
port. It also cannot install addons at runtime here — `bl_pkg` is broken in this
build (`ModuleNotFoundError: multiprocessing`).

So the web side inverts the shape: the tab is a *client* of a relay, and the
relay is what the MCP server talks to. `mcp_bridge.py` keeps a single set of
operations and simply exposes two transports, which is why both targets stay in
step by construction.

## Running it

### Native (start here)

```bash
cd mcp
npm install
MCP_BLENDER_BIN="/path/to/blender" npm run smoke   # 15 checks, end to end
MCP_BLENDER_BIN="/path/to/blender" node test/mcp.mjs
```

Wire it into an MCP client (Claude Desktop, Claude Code, Cursor…):

```json
{
  "mcpServers": {
    "blender": {
      "command": "node",
      "args": ["/absolute/path/to/blender-wasm/mcp/src/server.js"],
      "env": {
        "MCP_TARGET": "native",
        "MCP_BLENDER_BIN": "/path/to/blender"
      }
    }
  }
}
```

### Web (the browser build)

```bash
node src/relay.js                       # rendezvous point, default port 8787
MCP_TARGET=web node src/server.js       # MCP server talks to the relay
```

Blender inside the tab needs the mailbox turned on, which is a URL switch:

```
http://localhost:8081/?env=BLENDER_WEB_MCP=1
```

`?env=KEY=VALUE` is repeatable and accepts the `BLENDER_WEB_`, `WGPU_` and
`IMB_` prefixes only (see `demo/src/main.js`). Without it the bridge is inert:
a mailbox that lets a page drive Blender has no business existing in a session
nobody asked for it in.

The tab loads `web/mcp_client.js` and registers itself with the same session id.
Blender inside the tab must be started with the queue transport, i.e. the bridge
running `serve_queue()` — see that function's docstring for why it drains from a
`bpy.app.timers` callback rather than answering inline.

## The tools

`capabilities` `scene_graph` `object_info` `add_primitive` `set_transform`
`add_modifier` `apply_modifier` `duplicate_object` `delete_object` `render`
`export` — plus `run_python`, which is **hidden unless `MCP_ALLOW_PYTHON=1`**.

Three of them carry most of the value for a harness:

- **`capabilities`** — call it first, put the answer in the agent's context. The
  web build is trimmed (no Cycles, no OpenVDB so no Voxel remesh, no QuadriFlow,
  no Freestyle, GPU subdivision off) and the server build is not. A script
  written against the wrong feature set is the single most common failure, and
  this removes the whole class.

  It reports what actually **runs**, not what appears in `bpy.ops`, which is not
  the same thing: `bpy.ops` hands back a callable for operators that were never
  compiled in, so a plain `hasattr` claimed USD and Alembic on a build with
  neither. It also checks that an exporter's Python dependencies import, which
  is how the glTF and FBX gap was caught before numpy was linked in. Both
  targets now report `gltf`, `fbx`, `obj`, `stl` and `ply` true; USD and Alembic
  are genuinely absent from the web build.
- **`scene_graph`** — structured state (transforms, dimensions, modifier stacks,
  counts) instead of a `repr()` dump. An agent reasons far better over 20
  records than over a wall of text, and it costs a fraction of the tokens.
- **`render`** — returned as MCP *image* content, so a vision-capable agent can
  look at its own work. Images catch the mistakes numbers hide. Keep the
  resolution small; this is verification, not delivery.

  The scene is rendered once and then encoded as PNG, falling back to JPEG if
  the PNG encoder refuses it; the text half of the answer names the container
  only when it was not the first choice. That ladder is not decoration — PNG
  writing was broken outright in the browser build (libpng rejected metadata
  OIIO left in the header, see `blender/LOCAL_CHANGES.md`), and an encoder
  regression should cost an agent image quality, not its only way of seeing.

## Configuration

| variable | default | meaning |
|---|---|---|
| `MCP_TARGET` | `native` | `native` or `web` |
| `MCP_ALLOW_PYTHON` | unset | `1` exposes `run_python` |
| `MCP_INLINE_EXPORT` | unset | `1` returns export bytes inline instead of a summary |
| `MCP_BLENDER_BIN` | `blender` | Blender executable for the native adapter |
| `MCP_BLENDER_ATTACH` | unset | `1` attaches to an already-running bridge instead of spawning |
| `MCP_BLENDER_FILE` | unset | `.blend` to open instead of the startup file |
| `MCP_BLENDER_PORT` | `9876` | bridge socket port |
| `MCP_RELAY_URL` | `ws://127.0.0.1:8787` | relay, for the web adapter |
| `MCP_RELAY_TOKEN` | unset | shared secret; when set, clients must present it |
| `MCP_SESSION` | `default` | which tab this controller drives |
| `MCP_TIMEOUT_MS` | `120000` | per-request timeout, controller side |
| `MCP_RELAY_TIMEOUT_MS` | `MCP_TIMEOUT_MS`, else `120000` | per-request timeout, relay side |

Keep the relay's deadline **at least** as long as the controller's. A relay that
gives up first reports "the tab never answered" about a tab that was answering
fine, which sends you debugging the wrong component.

The first render in a fresh tab is the call that finds a short deadline: it
translates the EEVEE shaders through Tint before it draws anything, and takes
far longer than every render after it. Give the first one room.

## Security

`run_python` is arbitrary code execution inside the user's Blender session, and
in `web` mode that session belongs to whoever has the tab open. It is off by
default and the named tools exist precisely so you rarely need it.

Two things to decide before this faces anything untrusted:

1. **Set `MCP_RELAY_TOKEN`.** Without it, anyone who can reach the relay port
   can drive a connected tab.
2. **Keep `MCP_ALLOW_PYTHON` off in production.** If an agent genuinely needs an
   operation the tools do not cover, add it to `OPS` in `mcp_bridge.py` — that
   is one function, and it gets you validation, replayability and a log entry
   that `exec` never will.

## Extending

Add an operation in `mcp/blender/mcp_bridge.py` (a function plus one line in
`OPS`), then describe it in `mcp/src/tools.js`. Both transports and both targets
pick it up at once. The bridge validates arguments and owns the error messages;
the schema in `tools.js` only shapes what the agent is told.
