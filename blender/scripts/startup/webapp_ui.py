# SPDX-License-Identifier: GPL-2.0-or-later
"""Web-app UI lifting.

Everything that strips Blender's interface down for the browser build lives in
THIS ONE FILE, on purpose: `scripts/startup/` is auto-imported at startup, so a
single additive module can unregister upstream UI without editing any of the 80
files in `bl_ui/`. That keeps the vendored tree close to the fork and makes the
whole lifting revertible by deleting one file.

Ships inside assets.tar.zst, so changes here need no wasm rebuild -- rebuild the
asset tar with `python3 scripts/restage_assets.py` and reload the page.

Not everything can be done from Python; those parts are noted inline and live as
source changes in blender/ instead.
"""

import sys

import os as _os

import bpy
from bpy.app.handlers import persistent

# --- what to hide ------------------------------------------------------------

# The top bar: Blender logo, File/Edit/Render/Window/Help, the workspace tabs
# (Layout, Modeling, Sculpting, ...) and the Scene / ViewLayer selectors.
HIDE_TOPBAR = True

# The timeline strip along the bottom of the default layout.
HIDE_TIMELINE = True

# Sculpt mode's menus and panels.
HIDE_SCULPT_UI = True

# The bar along the very bottom carrying the version number and stats.
HIDE_STATUSBAR = True

# Properties editor (right panel) tabs to hide. Names are the RNA booleans on
# SpaceProperties, minus the "show_properties_" prefix -- the full set is:
#   tool scene render output view_layer world collection object constraints
#   modifiers data bone bone_constraints material texture particles physics
#   effects strip strip_modifier
# Colour management view transform. Blender's startup file asks for "AgX", but
# scripts/trim_ocio.py drops the AgX and Filmic views to save ~7.8 MB of LUTs --
# leaving the scene naming a view that no longer exists. Force a view that does.
# Set to None if you keep the AgX/Filmic LUTs (DROP_AGX_FILMIC in trim_ocio.py).
VIEW_TRANSFORM = 'Standard'

# Modifiers: nothing to switch off here. The Add Modifier menu's asset-backed
# entries are dead in this build because <datafiles>/assets is never staged --
# that lifting is WEBAPP_MODIFIER_ASSETS at the top of
# scripts/startup/bl_ui/properties_data_modifier.py, where the offending calls
# are inline in the menus' draw() and so cannot be reached from here (same
# reason as WEBAPP_HEADER in bl_ui/space_view3d.py).

# Viewport shading the 3D view opens in: WIREFRAME, SOLID, MATERIAL, RENDERED.
# None leaves Blender's default (SOLID / Workbench). MATERIAL and RENDERED go
# through EEVEE, whose WebGPU port is incomplete -- check the result.
# Viewport shading mode applied on load. ENV.BLENDER_WEB_SHADING overrides it
# ('SOLID', 'MATERIAL', 'WIREFRAME', 'RENDERED') -- handy for A/B testing a
# render bug between the workbench and EEVEE paths without restaging.
DEFAULT_SHADING = _os.environ.get("BLENDER_WEB_SHADING", "MATERIAL")

# --- viewport look -----------------------------------------------------------
# Purely cosmetic, and all of it is plain RNA: theme colours and per-space
# settings, so it restages without a wasm rebuild. Set any of these to None to
# keep Blender's own value.

# The floor's X and Y lines are drawn in the theme's axis colours -- red and
# green by default. White reads as neutral chrome instead of "there is a
# coordinate system you should care about". NOTE these colours are shared with
# the navigation gizmo in the top-right corner, whose balls turn white too; if
# that matters more than the floor, set this back to None and switch
# `overlay.show_axis_x` / `show_axis_y` off instead, which drops the two
# coloured lines entirely and leaves the plain grid.
NEUTRAL_AXIS_LINES = (1.0, 1.0, 1.0)

# Viewport background. Darker than stock (0.188) so the model carries the frame.
VIEWPORT_BACKGROUND = (0.055, 0.055, 0.055)

# Grid line opacity (the theme's grid colour is RGBA; only alpha is touched).
GRID_ALPHA = 0.30

# How far the grid reaches before it fades out, in Blender units. This is the
# viewport's clip end: overlay_grid_frag.glsl fades the grid with
# `smoothstep(0, 0.5*far_clip, dist - 0.5*far_clip)`, so the horizon follows
# clip_end directly -- 1000 (stock) puts it far past anything the demo shows.
# Shortening it also buys depth precision, which this backend can use.
GRID_FADE_DISTANCE = 200.0

# OpenSubdiv GPU subdivision. OFF on purpose: Blender feeds the GPU subdiv
# shaders OpenSubdiv's patch-basis GLSL, and evaluator_capi.cc only emits it
# under WITH_OPENGL_BACKEND / WITH_VULKAN_BACKEND -- on this backend the source
# comes back empty. CPU subdivision (what the Subsurf modifier needs) is
# unaffected.
GPU_SUBDIVISION = False

# Blender's splash screen (the picture with New File / Open / Recover). It is
# a real window the WM opens on startup, not part of the 3D view, so the only
# switch is the preference. Timing works out: creator.cc calls
# WM_init_splash_on_startup() well after WM_init() has run the startup scripts,
# so clearing the flag from register() lands before the check.
HIDE_SPLASH = True

# --- default scene -----------------------------------------------------------
# The startup file ships a camera and a light. Neither earns its place in a
# modelling tool that lights from a studio HDRI: the light is redundant (see
# HDRI_LIGHTING below) and the camera is one more thing to click by accident.
# Only ever applied to an unsaved file -- opening a .blend keeps its own objects.
# NOTE removing the camera means F12 has nothing to render from; add one back
# (Add > Camera) if a still image is ever needed.
REMOVE_DEFAULT_CAMERA = True
REMOVE_DEFAULT_LIGHT = True

# Light the viewport from the studio HDRI instead of scene lamps: that is what
# makes deleting the light harmless. The HDRI lights but is never shown --
# `studiolight_background_alpha = 0` keeps the plain background behind it, which
# is the "invisible HDRI" part.
# Files present after scripts/trim_assets.sh: city, courtyard, forest, interior
# (+ the `studio` set used by Solid mode). Only the matcaps were dropped.
HDRI_LIGHTING = True
STUDIO_HDRI = 'forest.exr'
# Retry budget for applying it, see _hdri_retry.
_HDRI_RETRY_DELAY = 0.5
_HDRI_RETRY_LIMIT = 4
HDRI_BACKGROUND_ALPHA = 0.0

# --- EEVEE memory ------------------------------------------------------------
# The backend counter says 1127 MB of GPU textures for a one-cube scene, and the
# shadow pool is the bulk of it: EEVEE reserves `shadow_pool_size` MB up front
# (DNA default 512, DNA_scene_types.h) and cuts it into an 8192x8192 atlas --
# visible in the render-pass log as `shadow_write_fr sz=8192x8192`. That budget
# buys nothing here: the startup light is removed, and even with a lamp or two
# a web viewport never approaches the tile count 512 MB is sized for.
#
# Quality is untouched. This is a pool *capacity*, not a resolution: shadows
# still render at full resolution, there is simply room for fewer shadow tiles
# at once. Raise it if a scene ever shows shadow tiles popping in and out.
# Values are enum strings: '16' '32' '64' '128' '256' '512' '1024' '1536' '2048'
SHADOW_POOL_SIZE = '32'

# Left alone on purpose:
#   gi_irradiance_pool_size  already 16 MB by default -- nothing to win.
#   use_raytracing           already off by default (it is not in the DNA flag
#                            defaults), so there was never anything to disable.
#   taa_samples (16)         lowering it would make the viewport converge
#                            faster but noisier -- that IS a quality change.
#   shadow_resolution_scale  same, it halves shadow resolution.

HIDDEN_PROPERTY_TABS = (
    "tool",
    "render",
    "output",
    "view_layer",
    "scene",
    "world",
    "collection",
    "object",
    "data",
)


def _log(msg):
    print("[webapp_ui] " + msg, file=sys.stderr)


# --- top bar -----------------------------------------------------------------
# Unregistering the header class leaves the bar EMPTY, not gone: the top bar is a
# "global area" built by screen_global_topbar_area_refresh() with
# size_min == size_max == screen_global_header_size(), i.e. a fixed height with
# no collapse flag (unlike the status bar, which has SCREEN_COLLAPSE_STATUSBAR).
# Removing the strip itself is a source change in
# source/blender/editors/screen/screen_edit.cc and needs a wasm rebuild.

def _unregister_topbar():
    for name in ("TOPBAR_HT_upper_bar", "TOPBAR_MT_editor_menus"):
        cls = getattr(bpy.types, name, None)
        if cls is None:
            continue
        try:
            bpy.utils.unregister_class(cls)
        except Exception as ex:
            _log("could not unregister {}: {}".format(name, ex))


# --- sculpt ------------------------------------------------------------------
# The mode dropdown itself is drawn with operator_menu_enum("object.mode_set"),
# whose items come from rna_object.cc -- Python cannot filter them, so dropping
# OB_MODE_SCULPT is a source change too. What follows removes sculpt's own menus
# and panels, so the mode is inert even if entered.

def _unregister_sculpt_ui():
    removed = 0
    for name in dir(bpy.types):
        if "sculpt" not in name.lower():
            continue
        # Only UI classes: leave operators, property groups and RNA structs be.
        if not name.startswith(("VIEW3D_MT_", "VIEW3D_PT_", "TOPBAR_MT_")):
            continue
        cls = getattr(bpy.types, name, None)
        if cls is None:
            continue
        try:
            bpy.utils.unregister_class(cls)
            removed += 1
        except Exception:
            pass  # already gone, or not a registered class
    _log("sculpt UI classes removed: {}".format(removed))


# --- colour management -------------------------------------------------------

def _set_view_transform():
    if not VIEW_TRANSFORM:
        return None
    try:
        for scene in bpy.data.scenes:
            if scene.view_settings.view_transform != VIEW_TRANSFORM:
                scene.view_settings.view_transform = VIEW_TRANSFORM
    except Exception as ex:
        # A wrong name here is not fatal: Blender keeps whatever it had.
        _log("view transform failed: {}".format(ex))
    return None


# --- viewport shading --------------------------------------------------------

def _set_default_shading():
    if not DEFAULT_SHADING:
        return None
    try:
        for screen in bpy.data.screens:
            for area in screen.areas:
                if area.type != 'VIEW_3D':
                    continue
                for space in area.spaces:
                    if space.type == 'VIEW_3D':
                        space.shading.type = DEFAULT_SHADING
    except Exception as ex:
        _log("shading default failed: {}".format(ex))
    return None


def _tune_eevee_memory():
    """Shrink the shadow pool (see SHADOW_POOL_SIZE)."""
    if not SHADOW_POOL_SIZE:
        return
    try:
        for scene in bpy.data.scenes:
            eevee = getattr(scene, "eevee", None)
            if eevee is None:
                continue
            if eevee.shadow_pool_size != SHADOW_POOL_SIZE:
                eevee.shadow_pool_size = SHADOW_POOL_SIZE
    except Exception as ex:
        _log("eevee memory tuning failed: {}".format(ex))


def _strip_default_scene():
    """Delete the startup camera and light -- but only from an unsaved file, so
    opening a .blend never loses its own objects."""
    if not (REMOVE_DEFAULT_CAMERA or REMOVE_DEFAULT_LIGHT):
        return
    try:
        if bpy.data.filepath:
            return
        wanted = set()
        if REMOVE_DEFAULT_CAMERA:
            wanted.add('CAMERA')
        if REMOVE_DEFAULT_LIGHT:
            wanted.add('LIGHT')
        doomed = [ob for ob in bpy.data.objects if ob.type in wanted]
        for ob in doomed:
            bpy.data.objects.remove(ob, do_unlink=True)
        if doomed:
            # The object is gone but its datablock lingers with 0 users and
            # would be written back into any saved file.
            for coll in (bpy.data.cameras, bpy.data.lights):
                for data in list(coll):
                    if data.users == 0:
                        coll.remove(data)
            _log("removed {} default object(s)".format(len(doomed)))
    except Exception as ex:
        _log("default scene strip failed: {}".format(ex))


def _use_hdri_lighting():
    """Light the viewport from the studio HDRI rather than scene lamps."""
    if not HDRI_LIGHTING:
        return
    pending = []
    try:
        for screen in bpy.data.screens:
            for area in screen.areas:
                if area.type != 'VIEW_3D':
                    continue
                for space in area.spaces:
                    if space.type != 'VIEW_3D':
                        continue
                    shading = space.shading
                    # Material Preview and Rendered carry separate flags.
                    shading.use_scene_lights = False
                    shading.use_scene_world = False
                    shading.use_scene_lights_render = False
                    shading.use_scene_world_render = False
                    if HDRI_BACKGROUND_ALPHA is not None:
                        shading.studiolight_background_alpha = HDRI_BACKGROUND_ALPHA
                    if STUDIO_HDRI:
                        # studio_light is a DYNAMIC enum: its items come from a
                        # callback that needs the studio lights to be loaded and
                        # a usable context. Called too early in startup it offers
                        # only DEFAULT, and the assignment raises even though the
                        # .exr is right there in datafiles/studiolights/world/.
                        # So a failure here is "too early", not "missing", and
                        # the retry below is what actually applies it.
                        try:
                            shading.studio_light = STUDIO_HDRI
                        except Exception:
                            pending.append(shading)
    except Exception as ex:
        _log("hdri lighting failed: {}".format(ex))

    if pending:
        bpy.app.timers.register(
            lambda: _hdri_retry(pending), first_interval=_HDRI_RETRY_DELAY
        )


def _hdri_retry(shadings, attempt=1):
    """Second pass once the studio lights are really available.

    Returns None so the timer does not repeat: either it worked, or it is worth
    saying so once instead of logging the same line ten times.
    """
    still_pending = []
    for shading in shadings:
        try:
            shading.studio_light = STUDIO_HDRI
        except Exception:
            still_pending.append(shading)

    if not still_pending:
        return None
    if attempt < _HDRI_RETRY_LIMIT:
        bpy.app.timers.register(
            lambda: _hdri_retry(still_pending, attempt + 1),
            first_interval=_HDRI_RETRY_DELAY,
        )
        return None

    _log(
        "studio HDRI '{}' could not be applied to {} viewport(s) after {} tries; "
        "lighting falls back to Blender's default studio light".format(
            STUDIO_HDRI, len(still_pending), attempt
        )
    )
    return None


def _debug_overlay_switches():
    """A/B levers for the grid-over-geometry investigation, off unless asked.
      ENV.BLENDER_WEB_NO_GRID=1     hide the floor grid + axis lines
      ENV.BLENDER_WEB_NO_OVERLAYS=1 hide every overlay
    If the ghost lines survive with the grid hidden, they are not drawn by the
    grid pass at all -- which sends the search to the overlay composite instead.
    """
    no_grid = _os.environ.get("BLENDER_WEB_NO_GRID")
    no_overlays = _os.environ.get("BLENDER_WEB_NO_OVERLAYS")
    if not (no_grid or no_overlays):
        return
    try:
        for screen in bpy.data.screens:
            for area in screen.areas:
                if area.type != 'VIEW_3D':
                    continue
                for space in area.spaces:
                    if space.type != 'VIEW_3D':
                        continue
                    if no_overlays:
                        space.overlay.show_overlays = False
                    if no_grid:
                        space.overlay.show_floor = False
                        space.overlay.show_axis_x = False
                        space.overlay.show_axis_y = False
        _log("debug overlay switches applied")
    except Exception as ex:
        _log("debug overlay switches failed: {}".format(ex))


def _apply_viewport_look():
    """Theme colours + per-space viewport settings (see the constants block)."""
    try:
        theme = bpy.context.preferences.themes[0]
        if NEUTRAL_AXIS_LINES is not None:
            ui = theme.user_interface
            ui.axis_x = NEUTRAL_AXIS_LINES
            ui.axis_y = NEUTRAL_AXIS_LINES
        if VIEWPORT_BACKGROUND is not None:
            gradients = theme.view_3d.space.gradients
            # The gradient only shows the flat colour in SINGLE_COLOR mode.
            gradients.background_type = 'SINGLE_COLOR'
            gradients.high_gradient = VIEWPORT_BACKGROUND
        if GRID_ALPHA is not None:
            grid = list(theme.view_3d.grid)
            grid[3] = GRID_ALPHA
            theme.view_3d.grid = grid
    except Exception as ex:
        _log("viewport theme failed: {}".format(ex))

    if GRID_FADE_DISTANCE is not None:
        try:
            for screen in bpy.data.screens:
                for area in screen.areas:
                    if area.type != 'VIEW_3D':
                        continue
                    for space in area.spaces:
                        if space.type == 'VIEW_3D':
                            space.clip_end = GRID_FADE_DISTANCE
        except Exception as ex:
            _log("clip end failed: {}".format(ex))

    if GPU_SUBDIVISION is not None:
        try:
            bpy.context.preferences.system.use_gpu_subdivision = GPU_SUBDIVISION
        except Exception as ex:
            _log("gpu subdivision toggle failed: {}".format(ex))
    return None


# --- properties tabs ---------------------------------------------------------
# Blender 5.x already filters these per space: ED_buttons_tabs_list() masks the
# tab list with SpaceProperties.visible_tabs, and every bit of that mask is a
# writable RNA boolean (rna_def_space_properties_filter). So this needs no C
# change and no rebuild -- unlike the mode dropdown or the global bars.

def _hide_property_tabs():
    missing = []
    try:
        for screen in bpy.data.screens:
            for area in screen.areas:
                if area.type != 'PROPERTIES':
                    continue
                space = area.spaces.active
                for tab in HIDDEN_PROPERTY_TABS:
                    attr = "show_properties_" + tab
                    if hasattr(space, attr):
                        setattr(space, attr, False)
                    elif tab not in missing:
                        missing.append(tab)
    except Exception as ex:
        _log("property tabs failed: {}".format(ex))
    if missing:
        _log("unknown property tabs (ignored): {}".format(", ".join(missing)))
    return None


# --- status bar --------------------------------------------------------------
# Unlike the top bar, the status bar IS collapsible: it is built with
# size_min = 1 and a SCREEN_COLLAPSE_STATUSBAR flag, exposed to Python as
# Screen.show_statusbar. Collapsing leaves a 1 px line rather than nothing --
# the source change in screen_edit.cc removes the area outright, this is the
# no-rebuild approximation.

def _collapse_statusbar():
    try:
        for screen in bpy.data.screens:
            screen.show_statusbar = False
    except Exception as ex:
        _log("statusbar collapse failed: {}".format(ex))
    return None


# --- timeline ----------------------------------------------------------------
# The bottom strip is a DOPESHEET_EDITOR in TIMELINE ui_mode, an ordinary area of
# the startup layout, so SCREEN_OT_area_close can take it out. Run it off a timer
# rather than inside the load handler: operators need a settled window context.

def _close_timeline():
    try:
        for window in bpy.context.window_manager.windows:
            screen = window.screen
            for area in screen.areas:
                if area.type != 'DOPESHEET_EDITOR':
                    continue
                space = area.spaces.active
                if getattr(space, "mode", None) not in {'TIMELINE', None}:
                    continue
                with bpy.context.temp_override(window=window, screen=screen, area=area):
                    if bpy.ops.screen.area_close.poll():
                        bpy.ops.screen.area_close()
                        _log("timeline area closed")
                    else:
                        _log("area_close poll failed (no neighbour to absorb it)")
                return None
    except Exception as ex:
        _log("timeline close failed: {}".format(ex))
    return None


def _data_ready():
    """bpy.data is a _RestrictData stub while startup scripts register; anything
    touching scenes or screens has to wait for the deferred pass."""
    try:
        bpy.data.scenes
        return True
    except AttributeError:
        return False


def _apply_screen_tweaks():
    """Everything that edits screen/space data rather than registered classes."""
    if not _data_ready():
        return
    if HIDE_STATUSBAR:
        _collapse_statusbar()
    if HIDDEN_PROPERTY_TABS:
        _hide_property_tabs()
    _set_default_shading()
    _set_view_transform()
    _apply_viewport_look()
    _use_hdri_lighting()
    _tune_eevee_memory()
    _strip_default_scene()
    _debug_overlay_switches()


def _deferred_apply():
    """Re-apply once the window is settled: closing an area needs a usable
    operator context, which does not exist yet while scripts are registering."""
    _apply_screen_tweaks()
    if HIDE_TIMELINE:
        _close_timeline()
    return None


@persistent
def _on_load(_dummy):
    _apply_screen_tweaks()
    if HIDE_TIMELINE:
        bpy.app.timers.register(_close_timeline, first_interval=0.2)


# --- registration ------------------------------------------------------------

def _hide_splash():
    """Preferences are readable during register(); bpy.data is not."""
    try:
        bpy.context.preferences.view.show_splash = False
        _log("splash disabled")
    except Exception as ex:
        _log("splash disable failed: {}".format(ex))


def register():
    if HIDE_SPLASH:
        _hide_splash()
    if HIDE_TOPBAR:
        _unregister_topbar()
    if HIDE_SCULPT_UI:
        _unregister_sculpt_ui()
    # The startup file is already loaded by the time scripts register, so screen
    # data can be touched right away -- but load_post is still needed for files
    # opened later, and the timer for what needs a settled context.
    _apply_screen_tweaks()
    if _on_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load)
    bpy.app.timers.register(_deferred_apply, first_interval=0.5)
    _log("lifting applied")


def unregister():
    if _on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load)
