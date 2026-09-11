# SPDX-License-Identifier: GPL-2.0-or-later

"""Open the model a `?model=` link asked for.

The tab is opened from another application (Phasen opens one per product), so
what it shows must be decided by the URL, not by the startup file. The page
fetches the model, exposes it through an in-memory FsProvider, and names it
here:

    BLENDER_WEB_MODEL          absolute path inside the wasm filesystem
    BLENDER_WEB_MODEL_FORMAT   "blend" or "glb"

The format is passed explicitly rather than sniffed from the extension because
the URLs carry generated names (``model-<jobId>-ultra-<uuid8>.glb``) and the
caller has to choose between "open a scene" and "import a mesh" before anything
is downloaded. Trusting the extension would put that decision in a filename.

Timing: the provider is mounted from JS around runtime init, on a different
thread from the one running this. Rather than assume an order, wait for the
path to appear -- bounded, and loud if it never does. A tab that quietly shows
the default cube instead of the product is the failure mode that makes this
impossible to diagnose from a bug report, so every giving-up path prints
``BLENDER_WEB_MODEL_ERROR`` and the page turns that into a visible message.

Without BLENDER_WEB_MODEL this module does nothing at all.
"""

import os as _os

import bpy

_PATH = _os.environ.get("BLENDER_WEB_MODEL", "")
_FORMAT = _os.environ.get("BLENDER_WEB_MODEL_FORMAT", "").lower()

# The mount races us; poll rather than assume. ~15s at 0.25s is far longer than
# the observed gap and still terminates.
_POLL_SECONDS = 0.25
_MAX_TRIES = 60

_tries = 0


def _fail(message):
    """Report loudly enough that the page can surface it."""
    print("BLENDER_WEB_MODEL_ERROR " + message, flush=True)


def _frame_imported(before_names):
    """Select what the import added and frame it, so the product fills the view.

    An imported product can be millimetres or metres across; dropping it into a
    factory scene without framing shows a speck or an interior. Nothing here is
    load-bearing, so a failure must not sink the import that just succeeded.
    """
    try:
        added = [o for o in bpy.data.objects if o.name not in before_names]
        if not added:
            return
        bpy.ops.object.select_all(action="DESELECT")
        for ob in added:
            ob.select_set(True)
        bpy.context.view_layer.objects.active = added[0]
        for area in bpy.context.screen.areas:
            if area.type != "VIEW_3D":
                continue
            for region in area.regions:
                if region.type == "WINDOW":
                    with bpy.context.temp_override(area=area, region=region):
                        bpy.ops.view3d.view_selected()
                    return
    except Exception as ex:  # noqa: BLE001 - framing is cosmetic, never fatal
        print("[webapp_model] framing skipped: %r" % (ex,), flush=True)


def _import_gltf():
    # io_scene_gltf2 ships in assets.tar; enable it if it is not already on.
    if not hasattr(bpy.ops.import_scene, "gltf"):
        try:
            import addon_utils
            addon_utils.enable("io_scene_gltf2", default_set=False, persistent=True)
        except Exception as ex:  # noqa: BLE001
            _fail("glTF importer unavailable: %r" % (ex,))
            return False
    if not hasattr(bpy.ops.import_scene, "gltf"):
        _fail("glTF importer unavailable after enabling io_scene_gltf2")
        return False

    before = {o.name for o in bpy.data.objects}
    bpy.ops.import_scene.gltf(filepath=_PATH)
    _frame_imported(before)
    return True


def _open():
    """Returns True when done (success or definitive failure)."""
    if _FORMAT == "blend":
        bpy.ops.wm.open_mainfile(filepath=_PATH)
    elif _FORMAT == "glb":
        if not _import_gltf():
            return True
    else:
        _fail("unknown format %r (expected 'blend' or 'glb')" % (_FORMAT,))
        return True
    print("[webapp_model] loaded %s as %s" % (_PATH, _FORMAT), flush=True)
    return True


def _tick():
    global _tries
    _tries += 1
    if not _os.path.exists(_PATH):
        if _tries >= _MAX_TRIES:
            _fail("%s never appeared (mount did not complete)" % (_PATH,))
            return None
        return _POLL_SECONDS
    try:
        _open()
    except Exception as ex:  # noqa: BLE001 - the page must hear about any of these
        _fail("%s: %r" % (_PATH, ex))
    return None


def register():
    if not _PATH:
        return
    bpy.app.timers.register(_tick, first_interval=_POLL_SECONDS)


def unregister():
    pass
