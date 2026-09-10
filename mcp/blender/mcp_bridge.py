# SPDX-License-Identifier: GPL-2.0-or-later
"""In-Blender half of the MCP bridge.

One set of operations, two transports:

    socket  native Blender on a server (`blender --background --python this.py`)
    queue   the wasm build in a browser tab, where sockets do not exist -- the
            page drops request files into a directory and reads answers back

Everything an agent can do goes through OPS below. Adding a capability is one
function plus one line in OPS; both transports and both targets get it at once.

Why operations and not just "run this Python": arbitrary exec is the escape
hatch (`run_python`), not the interface. Named operations are replayable, they
validate their arguments, they cannot silently half-apply, and they give the
harness something to log and diff. The escape hatch stays because no fixed tool
set survives contact with a real modelling session.

Protocol, one JSON object per message:

    -> {"id": 7, "op": "add_primitive", "args": {"kind": "cube"}}
    <- {"id": 7, "ok": true,  "result": {...}}
    <- {"id": 7, "ok": false, "error": "...", "traceback": "..."}
"""

import base64
import json
import os
import sys
import tempfile
import traceback

import bpy
import mathutils

PROTOCOL_VERSION = 1

# --- small helpers -----------------------------------------------------------


def _log(msg):
    print("[mcp_bridge] {}".format(msg), file=sys.stderr, flush=True)


def _vec(v):
    """mathutils vector -> plain list, rounded. Agents do not need 7 decimals of
    float noise, and it costs tokens on every scene dump."""
    return [round(float(c), 5) for c in v]


def _object_summary(ob):
    """The compact per-object record used by scene_graph.

    Deliberately NOT everything: transform, size and the modifier stack are what
    an agent needs to decide the next step. Vertex-level detail is object_info's
    job, on request.
    """
    rec = {
        "name": ob.name,
        "type": ob.type,
        "location": _vec(ob.location),
        "rotation_euler": _vec(ob.rotation_euler),
        "scale": _vec(ob.scale),
        "dimensions": _vec(ob.dimensions),
        "visible": not ob.hide_viewport,
        "parent": ob.parent.name if ob.parent else None,
    }
    if ob.modifiers:
        rec["modifiers"] = [{"name": m.name, "type": m.type} for m in ob.modifiers]
    if ob.type == 'MESH' and ob.data:
        rec["counts"] = {
            "verts": len(ob.data.vertices),
            "edges": len(ob.data.edges),
            "faces": len(ob.data.polygons),
        }
    if ob.material_slots:
        rec["materials"] = [s.material.name for s in ob.material_slots if s.material]
    return rec


def _world_bounds(ob):
    corners = [ob.matrix_world @ mathutils.Vector(c) for c in ob.bound_box]
    return {
        "min": [round(min(c[i] for c in corners), 5) for i in range(3)],
        "max": [round(max(c[i] for c in corners), 5) for i in range(3)],
    }


def _require_object(name):
    ob = bpy.data.objects.get(name)
    if ob is None:
        raise KeyError("no object named {!r}".format(name))
    return ob


def _select_only(ob):
    for other in bpy.context.selected_objects:
        other.select_set(False)
    ob.select_set(True)
    bpy.context.view_layer.objects.active = ob


def _b64_file(path):
    with open(path, "rb") as fh:
        return base64.b64encode(fh.read()).decode("ascii")


# --- operations --------------------------------------------------------------


# An operator existing in `bpy.ops` says nothing about whether it RUNS. The glTF
# and FBX exporters are Python add-ons that import numpy at module scope, and the
# wasm CPython has no numpy, so both fail on their first line while `bpy.ops`
# advertises them happily. Reporting them as available is worse than not having
# them: an agent picks glTF, writes a whole pipeline around it, and only finds
# out at the end. So capabilities probes the real requirement.
_EXPORTER_REQUIREMENTS = {
    "gltf": ("numpy",),
    "fbx": ("numpy",),
}


def _operator_exists(group, name):
    """Is this operator really registered?

    `hasattr(bpy.ops.wm, "usd_export")` answers True in a build with no USD at
    all: bpy.ops hands back a callable wrapper for any name and only complains
    when you call it. `dir()` on the submodule lists what is actually
    registered, so that is what the capability report is built from. Getting
    this wrong made capabilities claim USD and Alembic on a build that has
    neither.
    """
    submodule = getattr(bpy.ops, group, None)
    if submodule is None:
        return False
    try:
        return name in dir(submodule)
    except Exception:
        return False


def _exporter_blocker(label):
    """Return the name of the first missing module an exporter needs, or None."""
    import importlib.util

    for module in _EXPORTER_REQUIREMENTS.get(label, ()):
        try:
            found = importlib.util.find_spec(module) is not None
        except (ImportError, ValueError):
            found = False
        if not found:
            return module
    return None


def op_capabilities(_args):
    """What this particular Blender can do.

    The harness is meant to call this first and put the answer in the agent's
    context. It is what stops an agent from writing a script against Cycles, or
    Voxel remesh, or an exporter, that this build simply does not have -- the
    web build is trimmed and the server build is not, and a script that only
    works on one of them is the most common way this goes wrong.
    """
    engines = []
    try:
        engines = [
            item.identifier
            for item in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items
        ]
    except Exception:
        pass

    exporters = {}
    for label, path in (
        ("gltf", "export_scene.gltf"),
        ("fbx", "export_scene.fbx"),
        ("obj", "wm.obj_export"),
        ("stl", "wm.stl_export"),
        ("ply", "wm.ply_export"),
        ("usd", "wm.usd_export"),
        ("alembic", "wm.alembic_export"),
    ):
        group, _, name = path.partition(".")
        exporters[label] = (
            _operator_exists(group, name) and _exporter_blocker(label) is None
        )

    modifiers = []
    try:
        modifiers = [
            item.identifier
            for item in bpy.types.Modifier.bl_rna.properties["type"].enum_items
        ]
    except Exception:
        pass

    return {
        "protocol": PROTOCOL_VERSION,
        "blender": bpy.app.version_string,
        "target": TARGET,
        "background": bool(bpy.app.background),
        "engines": engines,
        "exporters": exporters,
        "modifiers": modifiers,
        "ops": sorted(OPS),
    }


def op_scene_graph(args):
    """Compact structured state of the scene -- the harness's context primitive.

    Structured, not a Python dump: an agent reasons far better over 20 records
    with transforms and modifier stacks than over a wall of repr().
    """
    scene = bpy.context.scene
    objects = [_object_summary(ob) for ob in scene.objects]
    if args.get("selected_only"):
        selected = {ob.name for ob in bpy.context.selected_objects}
        objects = [rec for rec in objects if rec["name"] in selected]
    active = bpy.context.view_layer.objects.active
    return {
        "scene": scene.name,
        "engine": scene.render.engine,
        "frame": scene.frame_current,
        "unit_scale": round(scene.unit_settings.scale_length, 6),
        "active": active.name if active else None,
        "selected": [ob.name for ob in bpy.context.selected_objects],
        "object_count": len(objects),
        "objects": objects,
    }


def op_object_info(args):
    """Everything about one object, including world-space bounds and modifier
    parameters -- the detail scene_graph deliberately leaves out."""
    ob = _require_object(args["name"])
    info = _object_summary(ob)
    info["matrix_world"] = [_vec(row) for row in ob.matrix_world]
    info["bounds_world"] = _world_bounds(ob)
    info["modifiers"] = []
    for mod in ob.modifiers:
        params = {}
        for prop in mod.bl_rna.properties:
            if prop.identifier in {"rna_type", "name", "type"} or prop.is_readonly:
                continue
            try:
                value = getattr(mod, prop.identifier)
            except Exception:
                continue
            if isinstance(value, (int, float, bool, str)):
                params[prop.identifier] = value
            elif hasattr(value, "__len__") and not isinstance(value, bytes):
                try:
                    params[prop.identifier] = [round(float(x), 5) for x in value]
                except Exception:
                    pass
            elif hasattr(value, "name"):
                params[prop.identifier] = value.name
        info["modifiers"].append({"name": mod.name, "type": mod.type, "params": params})
    return info


_PRIMITIVES = {
    "cube": "mesh.primitive_cube_add",
    "plane": "mesh.primitive_plane_add",
    "circle": "mesh.primitive_circle_add",
    "uv_sphere": "mesh.primitive_uv_sphere_add",
    "ico_sphere": "mesh.primitive_ico_sphere_add",
    "cylinder": "mesh.primitive_cylinder_add",
    "cone": "mesh.primitive_cone_add",
    "torus": "mesh.primitive_torus_add",
    "monkey": "mesh.primitive_monkey_add",
    "empty": "object.empty_add",
    "camera": "object.camera_add",
    "light": "object.light_add",
}


def op_add_primitive(args):
    kind = args.get("kind", "cube")
    if kind not in _PRIMITIVES:
        raise ValueError(
            "unknown primitive {!r}; known: {}".format(kind, ", ".join(sorted(_PRIMITIVES)))
        )
    group, _, name = _PRIMITIVES[kind].partition(".")
    call = getattr(getattr(bpy.ops, group), name)

    kwargs = dict(args.get("params") or {})
    kwargs["location"] = tuple(args.get("location") or (0.0, 0.0, 0.0))
    if args.get("rotation"):
        kwargs["rotation"] = tuple(args["rotation"])

    before = set(bpy.data.objects.keys())
    call(**kwargs)
    created = set(bpy.data.objects.keys()) - before
    ob = bpy.data.objects[created.pop()] if created else bpy.context.active_object

    if args.get("name"):
        ob.name = args["name"]
    if args.get("scale"):
        ob.scale = tuple(args["scale"])
    return _object_summary(ob)


def op_set_transform(args):
    ob = _require_object(args["name"])
    if args.get("location") is not None:
        ob.location = tuple(args["location"])
    if args.get("rotation") is not None:
        ob.rotation_euler = tuple(args["rotation"])
    if args.get("scale") is not None:
        ob.scale = tuple(args["scale"])
    bpy.context.view_layer.update()
    return _object_summary(ob)


def op_add_modifier(args):
    ob = _require_object(args["object"])
    mod = ob.modifiers.new(name=args.get("name") or args["type"].title(), type=args["type"])
    for key, value in (args.get("params") or {}).items():
        if not hasattr(mod, key):
            raise AttributeError("{} modifier has no {!r}".format(mod.type, key))
        setattr(mod, key, value)
    bpy.context.view_layer.update()
    return {"object": ob.name, "modifier": mod.name, "type": mod.type}


def op_apply_modifier(args):
    ob = _require_object(args["object"])
    _select_only(ob)
    name = args.get("modifier") or (ob.modifiers[-1].name if ob.modifiers else None)
    if name is None:
        raise KeyError("{} has no modifiers".format(ob.name))
    bpy.ops.object.modifier_apply(modifier=name)
    return _object_summary(ob)


def op_delete_object(args):
    ob = _require_object(args["name"])
    bpy.data.objects.remove(ob, do_unlink=True)
    return {"deleted": args["name"]}


def op_duplicate_object(args):
    ob = _require_object(args["name"])
    copy = ob.copy()
    if ob.data:
        # Copy the data too: without this the duplicate shares the source mesh,
        # so editing one silently edits the other.
        copy.data = ob.data.copy()
    if args.get("new_name"):
        copy.name = args["new_name"]
    if args.get("location"):
        copy.location = tuple(args["location"])
    bpy.context.collection.objects.link(copy)
    return _object_summary(copy)


def op_run_python(args):
    """The escape hatch.

    Kept because no fixed tool set survives a real modelling session, but it is
    the fallback: it is unreplayable, unvalidated, and it is arbitrary code
    execution -- the MCP server is expected to gate it behind an explicit
    opt-in, never to expose it to an untrusted caller.

    stdout is captured and returned so `print()` works the way an agent expects,
    and a `result` variable (or the value of the last expression) comes back as
    JSON when it can be serialised.
    """
    import contextlib
    import io

    code = args["code"]
    env = {"bpy": bpy, "mathutils": mathutils, "result": None}
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        exec(compile(code, "<mcp>", "exec"), env)
    result = env.get("result")
    try:
        json.dumps(result)
    except (TypeError, ValueError):
        result = repr(result)
    return {"stdout": out.getvalue(), "result": result}


def _ensure_camera(scene):
    """Renders need a camera, and this build's startup scene deliberately has
    none (see webapp_ui.py). Frame everything and remember it was ours, so the
    scene the agent is building does not silently accumulate cameras."""
    if scene.camera is not None:
        return scene.camera, False
    cam_data = bpy.data.cameras.new("MCP_Camera")
    cam = bpy.data.objects.new("MCP_Camera", cam_data)
    scene.collection.objects.link(cam)
    scene.camera = cam
    cam.location = (7.0, -7.0, 5.0)
    cam.rotation_euler = (1.109, 0.0, 0.785)
    return cam, True


# Ordered by preference. The browser build cannot always write every format,
# so `render` tries them in turn rather than failing outright -- see op_render.
_RENDER_FORMATS = (
    ("PNG", ".png", "image/png"),
    ("JPEG", ".jpg", "image/jpeg"),
)


def op_render(args):
    """Render to an image and return it inline (base64).

    Images are how an agent checks its own work: far cheaper than describing
    geometry in words, and it catches the mistakes numbers hide. Keep the
    resolution small -- this is verification, not delivery.

    The render runs ONCE and the result is then written from `Render Result`,
    so falling back to another container costs an encode, never a re-render.
    The fallback exists because PNG writing was broken outright in the wasm
    build (libpng rejected the resolution metadata OIIO left in the header;
    fixed in imbuf's openimageio_support.cc). Keeping the ladder means a future
    encoder regression degrades the image format instead of removing the single
    tool an agent has for looking at its own work.
    """
    scene = bpy.context.scene
    width = int(args.get("width", 512))
    height = int(args.get("height", 512))
    wanted = str(args.get("format", "")).upper()
    cam, temporary = _ensure_camera(scene)

    prev = (
        scene.render.resolution_x,
        scene.render.resolution_y,
        scene.render.resolution_percentage,
        scene.render.filepath,
        scene.render.image_settings.file_format,
    )
    candidates = [f for f in _RENDER_FORMATS if not wanted or f[0] == wanted]
    if not candidates:
        raise ValueError(
            "unknown render format {!r}; known: {}".format(
                wanted, ", ".join(f[0] for f in _RENDER_FORMATS)
            )
        )

    try:
        scene.render.resolution_x = width
        scene.render.resolution_y = height
        scene.render.resolution_percentage = 100
        bpy.ops.render.render(write_still=False)

        rendered = bpy.data.images.get("Render Result")
        if rendered is None:
            raise RuntimeError("the render produced no Render Result")

        failures = []
        for name, ext, mime in candidates:
            out_path = os.path.join(tempfile.gettempdir(), "mcp_render" + ext)
            try:
                scene.render.image_settings.file_format = name
                rendered.save_render(out_path, scene=scene)
                payload = _b64_file(out_path)
            except Exception as ex:  # noqa: BLE001 - report, then try the next one
                failures.append("{}: {}".format(name, ex))
                continue
            return {
                "format": name.lower(),
                "mime": mime,
                "width": width,
                "height": height,
                "camera": cam.name,
                "fallback_from": [f.split(":")[0] for f in failures] or None,
                "base64": payload,
            }

        raise RuntimeError(
            "the render succeeded but no image encoder accepted it -- "
            + "; ".join(failures)
        )
    finally:
        (
            scene.render.resolution_x,
            scene.render.resolution_y,
            scene.render.resolution_percentage,
            scene.render.filepath,
            scene.render.image_settings.file_format,
        ) = prev
        if temporary and args.get("keep_camera") is not True:
            bpy.data.objects.remove(cam, do_unlink=True)


_EXPORTERS = {
    "gltf": ("export_scene", "gltf", ".glb"),
    "fbx": ("export_scene", "fbx", ".fbx"),
    "obj": ("wm", "obj_export", ".obj"),
    "stl": ("wm", "stl_export", ".stl"),
    "ply": ("wm", "ply_export", ".ply"),
}


def op_export(args):
    """Export the scene (or the selection) and return the bytes inline.

    Inline base64 rather than a path because the two targets do not share a
    filesystem with the caller: on the server the MCP process may be elsewhere,
    and in the browser there is no filesystem to share at all.
    """
    fmt = args.get("format", "gltf")
    if fmt not in _EXPORTERS:
        raise ValueError(
            "unknown format {!r}; known: {}".format(fmt, ", ".join(sorted(_EXPORTERS)))
        )
    group, name, ext = _EXPORTERS[fmt]
    if not _operator_exists(group, name):
        raise RuntimeError(
            "{} exporter is not available in this build (check capabilities)".format(fmt)
        )
    call = getattr(getattr(bpy.ops, group), name)
    blocker = _exporter_blocker(fmt)
    if blocker is not None:
        # Say it plainly here rather than letting the add-on raise a ten-frame
        # ImportError traceback that buries the one word that matters.
        raise RuntimeError(
            "the {} exporter needs the '{}' module, which this build of Python does "
            "not have. Working formats: {}. Check `capabilities` before choosing a "
            "format.".format(
                fmt,
                blocker,
                ", ".join(
                    sorted(f for f in _EXPORTERS if _exporter_blocker(f) is None)
                ),
            )
        )

    out_path = os.path.join(tempfile.gettempdir(), "mcp_export" + ext)
    kwargs = {"filepath": out_path}
    if args.get("selected_only"):
        kwargs["use_selection" if fmt in {"gltf", "fbx"} else "export_selected_objects"] = True
    call(**kwargs)

    payload = _b64_file(out_path)
    return {
        "format": fmt,
        "filename": os.path.basename(out_path),
        "bytes": os.path.getsize(out_path),
        "base64": payload,
    }


OPS = {
    "capabilities": op_capabilities,
    "scene_graph": op_scene_graph,
    "object_info": op_object_info,
    "add_primitive": op_add_primitive,
    "set_transform": op_set_transform,
    "add_modifier": op_add_modifier,
    "apply_modifier": op_apply_modifier,
    "delete_object": op_delete_object,
    "duplicate_object": op_duplicate_object,
    "run_python": op_run_python,
    "render": op_render,
    "export": op_export,
}


def dispatch(message):
    """Run one request. Never raises: an agent needs the error text far more
    than the process needs to die."""
    req_id = message.get("id")
    op_name = message.get("op")
    try:
        op = OPS.get(op_name)
        if op is None:
            raise KeyError("unknown op {!r}; known: {}".format(op_name, ", ".join(sorted(OPS))))
        return {"id": req_id, "ok": True, "result": op(message.get("args") or {})}
    except Exception as ex:
        return {
            "id": req_id,
            "ok": False,
            "error": "{}: {}".format(type(ex).__name__, ex),
            "traceback": traceback.format_exc(limit=6),
        }


# --- transport: socket (native / server) -------------------------------------


def serve_socket(host, port):
    """Blocking line-delimited JSON server. Background Blender has no event loop
    to hook into, so owning the loop here is the simple, correct thing."""
    import socket

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    srv.listen(1)
    _log("listening on {}:{} (blender {})".format(host, port, bpy.app.version_string))

    while True:
        conn, addr = srv.accept()
        _log("client {}".format(addr))
        buf = b""
        try:
            with conn:
                conn_file = conn.makefile("rwb")
                while True:
                    line = conn_file.readline()
                    if not line:
                        break
                    try:
                        message = json.loads(line.decode("utf-8"))
                    except ValueError as ex:
                        reply = {"id": None, "ok": False, "error": "bad JSON: {}".format(ex)}
                    else:
                        reply = dispatch(message)
                    conn_file.write((json.dumps(reply) + "\n").encode("utf-8"))
                    conn_file.flush()
        except (ConnectionError, OSError) as ex:
            _log("client dropped: {}".format(ex))
        del buf


# --- transport: queue (browser tab) ------------------------------------------


def serve_queue(in_dir, out_dir, interval=0.1):
    """Filesystem mailbox, drained from a timer.

    The wasm build has no sockets, and -- just as importantly -- anything
    touching bpy must run on Blender's own thread. A bpy.app.timers callback is
    exactly that thread, which is the same reason the desktop socket addons
    drain their queue from a timer instead of answering on the socket thread.
    """
    os.makedirs(in_dir, exist_ok=True)
    os.makedirs(out_dir, exist_ok=True)

    def drain():
        try:
            for entry in sorted(os.listdir(in_dir)):
                if not entry.endswith(".json"):
                    continue
                path = os.path.join(in_dir, entry)
                try:
                    with open(path, "r", encoding="utf-8") as fh:
                        message = json.load(fh)
                except Exception as ex:
                    message = None
                    reply = {"id": None, "ok": False, "error": "bad request: {}".format(ex)}
                finally:
                    try:
                        os.remove(path)
                    except OSError:
                        pass
                if message is not None:
                    reply = dispatch(message)
                tmp = os.path.join(out_dir, entry + ".part")
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump(reply, fh)
                # Rename last: the reader must never see a half-written answer.
                os.replace(tmp, os.path.join(out_dir, entry))
        except Exception as ex:
            _log("drain failed: {}".format(ex))
        return interval

    bpy.app.timers.register(drain, first_interval=interval, persistent=True)
    _log("queue transport armed ({} -> {})".format(in_dir, out_dir))


# --- entry point -------------------------------------------------------------

TARGET = "native"


def main():
    global TARGET
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    opts = {}
    for arg in argv:
        key, _, value = arg.partition("=")
        opts[key.lstrip("-")] = value

    transport = opts.get("transport", "socket")
    TARGET = "web" if transport == "queue" else "native"

    if transport == "queue":
        serve_queue(opts.get("in", "/tmp/mcp/in"), opts.get("out", "/tmp/mcp/out"))
    else:
        serve_socket(opts.get("host", "127.0.0.1"), int(opts.get("port", "9876")))


if __name__ == "__main__":
    main()
