#!/usr/bin/env bash
# Staging-time diet, shared by link_blender_web.sh (dev) and
# link_blender_release.sh (demo). Operates ONLY on the staged copies — the
# source tree and the wasm sysroot are never touched.
#
#   usage: trim_assets.sh <staged 5.3 dir> <staged python3.13 dir>
#
# Every cut below removes data the WASM build cannot use (feature compiled out)
# or that costs multiple MB of download for a feature nobody exercises in a
# browser demo. Escape hatches, all opt-in:
#   BLENDER_WEB_ALL_FONTS=1    keep the whole Noto font stack (+14 MB)
#   BLENDER_WEB_ALL_SCRIPTS=1  keep every bundled addon/preset (+3 MB)
#   BLENDER_WEB_FULL_STDLIB=1  keep the CPython stdlib untrimmed (+~5 MB)
set -euo pipefail
D53="$1"        # .../5.3   (scripts/ + datafiles/)
PYDIR="$2"      # .../python3.13
ROOT="${ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

sz() { du -shL "$1" 2>/dev/null | cut -f1; }

# --- datafiles --------------------------------------------------------------
# splash_template.xcf: GIMP source shipped upstream by accident (1.2 MB).
rm -f "$D53/datafiles/splash_template.xcf"

# Icon SOURCES, not icons. The running Blender reads datafiles/icons/*.dat
# (geometry icons) and the raster sheets compiled into the binary by datatoc.
# icons_blend/toolbar.blend (2.7 MB) and icons_svg/ (784 SVGs, 1.0 MB) are the
# authoring files those are GENERATED from -- see release/datafiles/
# blender_icons_geom_update.py, which is the only thing that opens them, at
# development time, on a developer's machine. Nothing loads them at runtime.
rm -rf "$D53/datafiles/icons_blend" "$D53/datafiles/icons_svg"
for f in blender_icons_geom.py blender_icons_geom_update.py ctodata.py DejaVuSans-Lite.sfd.bz2; do
  rm -f "$D53/datafiles/$f"
done

# Matcaps: 2.0 MB of EXR only ever sampled by Solid shading's Matcap lighting,
# which this build's stripped 3D-view header cannot reach. The world/*.exr
# HDRIs STAY -- Material Preview is the default shading and lights the scene
# with them.
if [ "${BLENDER_WEB_ALL_STUDIOLIGHTS:-0}" = "0" ]; then
  rm -rf "$D53/datafiles/studiolights/matcap"
fi

# Grease Pencil preview thumbnails: 1.1 MB for a datablock type nothing in this
# build creates or displays.
rm -f "$D53/datafiles/preview_grease_pencil.blend"

# OCIO diet (see trim_ocio.py): a browser canvas is an sRGB display.
# NOTE: OpenColorIO is NOT optional in Blender 5.x (no WITH_OPENCOLORIO switch),
# so datafiles/colormanagement IS read at runtime — trim it, don't delete it.
python3 "$ROOT/scripts/trim_ocio.py" "$D53/datafiles/colormanagement"

# Fonts: 14.7 MB of woff2, already-compressed bytes that go straight onto the
# download (10.9 MB of it is "Noto Sans CJK Regular.woff2" alone). The font
# stack is loaded by DIRECTORY SCAN (blf_load_datafiles_dir), so dropping files
# is safe — only the two defaults are opened by name:
#   BLF_DEFAULT_PROPORTIONAL_FONT "Inter.woff2"
#   BLF_DEFAULT_MONOSPACED_FONT   "DejaVuSansMono.woff2"
# The rest only widen glyph coverage (CJK/Arabic/Devanagari/emoji/math), and
# WITH_INTERNATIONAL is OFF in this build: the UI is English-only anyway.
if [ "${BLENDER_WEB_ALL_FONTS:-0}" = "0" ] && [ -d "$D53/datafiles/fonts" ]; then
  before=$(sz "$D53/datafiles/fonts")
  find "$D53/datafiles/fonts" -type f \
    ! -name 'Inter.woff2' ! -name 'DejaVuSansMono.woff2' -delete
  echo ">>   fonts: $before -> $(sz "$D53/datafiles/fonts") (non-Latin coverage dropped)"
fi

# --- bundled python scripts -------------------------------------------------
if [ "${BLENDER_WEB_ALL_SCRIPTS:-0}" = "0" ] && [ -d "$D53/scripts" ]; then
  before=$(sz "$D53/scripts")
  S="$D53/scripts"
  # Tied to features compiled OUT (see cmake/blender-wasm-cache.cmake):
  rm -rf "$S/freestyle"                       # WITH_FREESTYLE=OFF
  rm -rf "$S/addons_core/viewport_vr_preview" # WITH_XR_OPENXR=OFF
  rm -rf "$S/addons_core/ui_translate"        # WITH_INTERNATIONAL=OFF
  rm -rf "$S/addons_core/hydra_storm"         # WITH_HYDRA/WITH_USD=OFF
  rm -rf "$S/templates_osl"                   # no OSL (no Cycles)
  # Heavy and never reached in a demo session:
  rm -rf "$S/addons_core/rigify"              # 1.7 MB, not enabled by default
  rm -f  "$S/modules/_rna_manual_reference.py"  # 0.6 MB of online-manual URLs
  rm -f  "$S/presets/keyconfig/Industry_Compatible.py" \
         "$S/presets/keyconfig/Blender_27x.py" \
         "$S/presets/keyconfig/keymap_data/industry_compatible_data.py"
  # keymap_data/blender_default.py STAYS: it is the default keymap, not a preset.
  find "$S" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
  echo ">>   scripts: $before -> $(sz "$S")"
fi

# --- CPython stdlib ---------------------------------------------------------
# config-3.13-*/: build artifacts incl. a 41 MB libpython3.13.a never read at
# runtime; ensurepip: bundled pip wheel (no pip on wasm).
if [ -d "$PYDIR" ]; then
  before=$(sz "$PYDIR")
  ( cd "$PYDIR" && rm -rf test tests idlelib lib2to3 turtledemo tkinter \
      config-3.13-wasm32-emscripten ensurepip )
  if [ "${BLENDER_WEB_FULL_STDLIB:-0}" = "0" ]; then
    # Unreachable or unusable in a single-process wasm build. Blender's startup
    # path imports none of these; they are only reached by user scripts.
    ( cd "$PYDIR" && rm -rf unittest asyncio multiprocessing concurrent \
        pydoc_data venv sqlite3 dbm curses xmlrpc wsgiref zoneinfo ) 
    # (email/http/urllib stay: bl_pkg imports urllib for the extensions UI.)
  fi
  ( cd "$PYDIR" && find . -name '__pycache__' -type d -prune -exec rm -rf {} + )
  echo ">>   python: $before -> $(sz "$PYDIR")"
fi

# --- MCP bridge -------------------------------------------------------------
# mcp/blender/mcp_bridge.py is the ONE implementation of the agent-facing
# operations, shared with the native/server target. Staging it into
# scripts/modules/ makes it importable by scripts/startup/webapp_mcp.py, which
# only starts it when ENV.BLENDER_WEB_MCP=1. Copying rather than vendoring a
# second copy is the point: the two targets cannot drift apart.
BRIDGE_SRC="$(dirname "$0")/../mcp/blender/mcp_bridge.py"
if [ -f "$BRIDGE_SRC" ] && [ -d "$D53/scripts/modules" ]; then
  cp -f "$BRIDGE_SRC" "$D53/scripts/modules/mcp_bridge.py"
  echo ">>   staged mcp_bridge.py (agent bridge; inert unless BLENDER_WEB_MCP=1)"
fi

# --- numpy: drop what only a COMPILER needs ---------------------------------
# numpy is here so the glTF and FBX add-ons can run (see LOCAL_CHANGES.md); its
# C extensions are linked into the wasm binary, not shipped as files. That makes
# a large part of the installed tree dead weight in a browser: type stubs are
# read by type checkers and never by the interpreter, and the headers and .pyx
# sources exist so third-party code can COMPILE against numpy, which nothing
# here can do. f2py is a Fortran wrapper generator, testing/ needs pytest.
# BLENDER_WEB_NUMPY_FULL=1 keeps the lot.
if [ -d "$PYDIR/site-packages/numpy" ] && [ -z "${BLENDER_WEB_NUMPY_FULL:-}" ]; then
  _np_before=$(du -sk "$PYDIR/site-packages/numpy" | cut -f1)
  find "$PYDIR/site-packages/numpy" -name "*.pyi" -delete
  find "$PYDIR/site-packages/numpy" -name "*.pyx" -delete
  find "$PYDIR/site-packages/numpy" -name "*.pxd" -delete
  find "$PYDIR/site-packages/numpy" -name "*.h" -delete
  find "$PYDIR/site-packages/numpy" -name "*.c" -delete
  rm -rf "$PYDIR/site-packages/numpy/_core/include"
  rm -rf "$PYDIR/site-packages/numpy/f2py"
  rm -rf "$PYDIR/site-packages/numpy/testing"
  rm -rf "$PYDIR/site-packages/numpy/_pyinstaller"
  find "$PYDIR/site-packages/numpy" -type d -name tests -exec rm -rf {} + 2>/dev/null || true
  _np_after=$(du -sk "$PYDIR/site-packages/numpy" | cut -f1)
  echo ">>   numpy: ${_np_before}K -> ${_np_after}K (stubs, headers, f2py, tests)"
fi
