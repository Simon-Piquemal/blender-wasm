# SPDX-License-Identifier: GPL-2.0-or-later

"""Repaint the interface once a window resize has settled.

**This is a mitigation, not a fix.** The underlying defect is real and is
documented in `blender/LOCAL_CHANGES.md`: after a resize, one text run can be
missing from a region -- the interface asks for it, the string survives
clipping, the glyphs are queued and the draw is issued, the widget's background
lands, and its text does not. The failure sits between that draw being issued
and the pixels reaching the region's offscreen, which is WebGPU backend
territory and needs per-draw instrumentation to pin down.

What IS known, and measured, is the repair: **a full redraw always restores it**,
every single time, in every campaign run against it. That is also what the
original report described -- hovering the button brought the label back, because
hovering redraws that widget.

So this waits for the resize to stop, then asks for one full redraw. One per
gesture, not one per resize event: a drag emits dozens of events and redrawing
on each would be the expensive, useless version of this. The user sees the
window settle and then, a frame later, a correct interface.

Cost: a single extra redraw at the end of a resize. Delete this file and the
defect becomes visible again; nothing else changes.
"""

import os as _os

import bpy

# How long the size has to hold still before this counts as "the drag stopped".
# Long enough not to fire mid-gesture -- a drag emits events every ~16 ms, far
# faster than this -- and short enough that the corrected frame lands inside the
# ~180 ms it takes the eye to register a change, so the stale frame is never
# something the user sees settle and then move.
SETTLE_SECONDS = 0.12

# Poll interval. Reading two integers off the window is not worth optimising,
# and a coarser tick would make the repair visibly late.
POLL_SECONDS = 0.06

_last_size = None
_pending = False


def _log(msg):
    print("[webapp_resize] {}".format(msg))


def _window_size():
    """Current window size, or None if there is no window to ask."""
    wm = bpy.data.window_managers[0] if bpy.data.window_managers else None
    if wm is None or not wm.windows:
        return None
    win = wm.windows[0]
    return (win.width, win.height)


def _redraw_everything():
    count = 0
    for wm in bpy.data.window_managers:
        for win in wm.windows:
            screen = win.screen
            if screen is None:
                continue
            for area in screen.areas:
                area.tag_redraw()
                count += 1
    return count


def _tick():
    global _last_size, _pending

    size = _window_size()
    if size is None:
        return POLL_SECONDS

    if _last_size is None:
        _last_size = size
        return POLL_SECONDS

    if size != _last_size:
        # Still moving: remember the new size and wait for it to hold.
        _last_size = size
        _pending = True
        return POLL_SECONDS

    if _pending:
        # Held still for a full tick after changing: the gesture is over.
        _pending = False
        _redraw_everything()

    return POLL_SECONDS


def register():
    global _last_size, _pending
    # A lever so the mitigation can be measured against its own absence in the
    # same build; that A/B is the only reason to believe it does anything.
    if _os.environ.get("BLENDER_WEB_NO_RESIZE_REPAIR"):
        _log("disabled by BLENDER_WEB_NO_RESIZE_REPAIR")
        return
    _last_size = None
    _pending = False
    if not bpy.app.timers.is_registered(_tick):
        bpy.app.timers.register(_tick, first_interval=SETTLE_SECONDS, persistent=True)


def unregister():
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
