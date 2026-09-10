# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Solid ground for the web build.

The grid at Z=0 reads as a floor, but nothing in Blender treats it as one: the
startup cube straddles it (half of it is underground) and any object can be
dragged below. This module makes the plane behave the way the picture promises
-- objects rest ON it and cannot be pushed under it -- without adding a physics
simulation the user would have to bake or play.

It is additive: no upstream file is edited, and deleting this file restores
stock behaviour. It ships inside assets.tar.zst, so changes need no wasm
rebuild -- run `bash scripts/restage_assets.sh` and reload.

Why a clamp and not a rigid body: a passive-collision floor plus active bodies
would only resolve while the timeline plays, would need a bake to survive a
reload, and would let objects tumble. What is wanted here is a hard constraint
that holds at all times, including mid-drag.
"""

import bpy
from bpy.app.handlers import persistent
from mathutils import Vector

# --- toggles -----------------------------------------------------------------

ENABLED = True

# World Z of the floor plane. The overlay grid is drawn at 0.
FLOOR_Z = 0.0

# Types that get clamped by their bounding box. Anything else (cameras, lights,
# empties) is clamped by its origin instead -- a light has a bound_box, but it
# is a placeholder cube that would push the lamp metres into the air.
_BBOX_TYPES = {
    'MESH', 'CURVE', 'SURFACE', 'META', 'FONT', 'VOLUME',
    'POINTCLOUD', 'CURVES', 'GREASEPENCIL',
}

# Objects whose origin is meant to live below the floor (a camera looking up, a
# light rig) can opt out by setting a custom property: obj["webapp_no_floor"].
_OPT_OUT = "webapp_no_floor"

# Anything under this is treated as "already resting" -- without it, float error
# in the matrix multiply retriggers a write on every depsgraph update.
_EPSILON = 1e-5


def _log(msg):
    print("[webapp_floor] {}".format(msg))


# --- clamp -------------------------------------------------------------------

def _lowest_point(obj):
    """World-space Z of the object's lowest corner (or of its origin)."""
    if obj.type in _BBOX_TYPES:
        matrix = obj.matrix_world
        return min((matrix @ Vector(corner)).z for corner in obj.bound_box)
    return obj.matrix_world.translation.z


def _clamp(obj):
    """Lift `obj` so nothing of it sits below the floor. True if it moved."""
    lowest = _lowest_point(obj)
    lift = FLOOR_Z - lowest
    if lift <= _EPSILON:
        return False
    # Move in world space: the object may be parented or rotated, in which case
    # writing obj.location.z (parent space) would lift it by the wrong amount.
    obj.matrix_world.translation.z += lift
    return True


# Re-entrancy guard. Writing to an object inside depsgraph_update_post queues
# another update, which calls this handler again; without the flag the second
# pass would clamp against a matrix Blender has not finished flushing.
_busy = False


def _clamp_objects(objects):
    global _busy
    if _busy:
        return
    _busy = True
    try:
        for obj in objects:
            if obj is None or obj.library is not None:
                continue
            if obj.get(_OPT_OUT):
                continue
            _clamp(obj)
    except Exception as ex:
        _log("clamp failed: {}".format(ex))
    finally:
        _busy = False


@persistent
def _on_depsgraph_update(scene, depsgraph):
    """Runs after every dependency-graph evaluation, which includes each mouse
    step of a modal grab -- so the object is held at the floor while dragging,
    not snapped back once the operator ends."""
    if not ENABLED or _busy:
        return
    # Only look at what actually changed. Iterating the whole scene here would
    # cost a full pass per mouse move.
    touched = []
    for update in depsgraph.updates:
        obj_id = update.id
        if isinstance(obj_id, bpy.types.Object) and update.is_updated_transform:
            # The update carries an evaluated copy; clamping that would be
            # thrown away on the next evaluation. Reach the original datablock.
            touched.append(obj_id.original)
    if touched:
        _clamp_objects(touched)


@persistent
def _on_load(_dummy):
    """Settle the whole scene once, so the startup cube rests on the grid
    instead of straddling it."""
    if not ENABLED:
        return
    try:
        _clamp_objects(list(bpy.data.objects))
    except Exception as ex:
        _log("initial settle failed: {}".format(ex))


def _settle_now():
    if _data_ready():
        _on_load(None)
    return None


def _data_ready():
    """bpy.data is a restricted stub while startup scripts register."""
    try:
        bpy.data.objects
        return True
    except AttributeError:
        return False


# --- registration ------------------------------------------------------------

def register():
    if not ENABLED:
        return
    if _on_depsgraph_update not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph_update)
    if _on_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load)
    # The startup file is already loaded here, but the operator context is not
    # settled; a timer keeps this out of the registration pass.
    bpy.app.timers.register(_settle_now, first_interval=0.4)
    _log("floor collision enabled at Z={}".format(FLOOR_Z))


def unregister():
    if _on_depsgraph_update in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_on_depsgraph_update)
    if _on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load)
