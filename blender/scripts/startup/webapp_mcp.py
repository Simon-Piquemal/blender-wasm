# SPDX-License-Identifier: GPL-2.0-or-later

"""Start the MCP bridge inside the browser build.

Off unless ENV.BLENDER_WEB_MCP=1 (the demo copies `window.__CAPENV` into the
environment before boot), because a mailbox that lets the page run operations
inside Blender has no business existing in a session nobody asked for it in.

The bridge itself lives in mcp/blender/mcp_bridge.py and is staged into
`scripts/modules/` by scripts/trim_assets.sh -- one source of truth shared with
the native/server target, so the two cannot drift apart.

Additive, like webapp_ui.py: delete this file and the build has no MCP surface.
"""

import os as _os

import bpy

# Where the page and Blender exchange messages. Must be an in-memory WASMFS
# directory: the OPFS and localdir backends proxy their I/O to another thread
# and block the caller, and the page's main thread is not allowed to block.
IN_DIR = _os.environ.get("BLENDER_WEB_MCP_IN", "/tmp/mcp/in")
OUT_DIR = _os.environ.get("BLENDER_WEB_MCP_OUT", "/tmp/mcp/out")


def _log(msg):
    print("[webapp_mcp] {}".format(msg))


def _start():
    try:
        import mcp_bridge
    except ImportError as ex:
        _log("bridge not staged ({}) -- check scripts/trim_assets.sh".format(ex))
        return None

    try:
        mcp_bridge.TARGET = "web"
        mcp_bridge.serve_queue(IN_DIR, OUT_DIR)
        _log("bridge listening on {} -> {} ({} ops)".format(
            IN_DIR, OUT_DIR, len(mcp_bridge.OPS)))
    except Exception as ex:
        _log("bridge failed to start: {}".format(ex))
    return None


def register():
    if _os.environ.get("BLENDER_WEB_MCP") != "1":
        return
    # Deferred: serve_queue registers a timer, and the timer system is happier
    # once the window manager has settled -- same reason webapp_ui.py defers.
    bpy.app.timers.register(_start, first_interval=0.5)


def unregister():
    pass
