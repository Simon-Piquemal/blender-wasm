#!/usr/bin/env bash
# Rebuild demo/public/assets.tar.zst from the vendored tree -- the UI iteration loop.
#
# Blender's interface is Python (blender/scripts/startup/bl_ui/, plus our
# webapp_ui.py) and data (release/datafiles: theme presets, splash, icons), and
# both ship INSIDE assets.tar.zst. So hiding a menu, restyling the theme or
# rebranding needs no wasm rebuild at all -- just this, then reload the page.
#
# Applies the same diet as the release link so local == shipped.
#
#   usage: restage_assets.sh
set -euo pipefail
ROOT="${ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
OUT="$ROOT/demo/public"
STAGE="$ROOT/demo/.stage-ui"
SYSROOT_PY="$ROOT/wasm-sysroot/lib/python3.13"

[ -d "$ROOT/blender/scripts" ] || { echo "!! blender/ not vendored"; exit 1; }

# The CPython stdlib never changes when we edit the UI: keep the staged copy
# across runs instead of re-copying ~9 MB every time.
KEEP=""
if [ -d "$STAGE/lib/python3.13" ]; then
  KEEP="$ROOT/demo/.stage-ui-py"
  rm -rf "$KEEP"; mv "$STAGE/lib/python3.13" "$KEEP"
fi

rm -rf "$STAGE"; mkdir -p "$STAGE/5.3" "$STAGE/lib"
echo ">> copying scripts + datafiles from blender/"
cp -r "$ROOT/blender/scripts"           "$STAGE/5.3/scripts"
cp -r "$ROOT/blender/release/datafiles" "$STAGE/5.3/datafiles"

if [ -n "$KEEP" ]; then
  mv "$KEEP" "$STAGE/lib/python3.13"
elif [ -d "$SYSROOT_PY" ]; then
  cp -r "$SYSROOT_PY" "$STAGE/lib/python3.13"
else
  echo "!! no CPython stdlib. Seed it once from a shipped build:"
  echo "!!   mkdir -p $STAGE/lib && cp -a <extracted assets>/lib/python3.13 $STAGE/lib/"
  exit 1
fi

bash "$ROOT/scripts/trim_assets.sh" "$STAGE/5.3" "$STAGE/lib/python3.13"

# Essentials brushes (6.6 MB). OFF by default: sculpt and paint modes are hidden
# from this build (webapp_ui.py + the mode allow-list), so the brush asset
# library is unreachable weight. BLENDER_WEB_BUNDLE_BRUSHES=1 puts it back.
if [ "${BLENDER_WEB_BUNDLE_BRUSHES:-0}" != "0" ] && [ -d "$ROOT/demo/brush-assets/brushes" ]; then
  mkdir -p "$STAGE/5.3/datafiles/assets/brushes"
  cp -f "$ROOT/demo/brush-assets"/brushes/*.blend "$STAGE/5.3/datafiles/assets/brushes/" 2>/dev/null || true
  cp -f "$ROOT/demo/brush-assets/blender_assets.cats.txt" "$STAGE/5.3/datafiles/assets/" 2>/dev/null || true
fi

echo ">> packing assets.tar.zst"
( cd "$STAGE" && tar -cf "$OUT/assets.tar" 5.3 lib )
# Compress with the python module when available, else the zstd CLI: plain
# system pythons have no zstandard module, but the distro zstd package does
# ship the command (this loop must keep working on a bare machine).
if python3 -c "import zstandard" 2>/dev/null; then
  python3 - "$OUT" <<'PYEOF'
import os, sys
import zstandard
out = sys.argv[1]
raw = open(os.path.join(out, "assets.tar"), "rb").read()
# level 12, not the release's 22: this runs on every UI tweak.
comp = zstandard.ZstdCompressor(level=12).compress(raw)
open(os.path.join(out, "assets.tar.zst"), "wb").write(comp)
print(">> assets.tar.zst {:.1f} MB on the wire ({:.1f} MB raw)".format(
    len(comp) / 1048576, len(raw) / 1048576))
PYEOF
elif command -v zstd >/dev/null 2>&1; then
  zstd -q -f -12 "$OUT/assets.tar" -o "$OUT/assets.tar.zst"
  echo ">> assets.tar.zst $(stat -c%s "$OUT/assets.tar.zst") octets (zstd CLI)"
else
  echo "!! neither the python zstandard module nor the zstd command: cannot compress"
  exit 1
fi
# The manifest carries the DECOMPRESSED size: the demo sizes its buffer with it.
python3 - "$OUT" <<'PYEOF'
import json, os, sys
out = sys.argv[1]
mpath = os.path.join(out, "manifest.json")
manifest = json.load(open(mpath)) if os.path.exists(mpath) else {}
manifest["assets.tar.zst"] = os.path.getsize(os.path.join(out, "assets.tar"))
json.dump(manifest, open(mpath, "w"))
PYEOF
rm -f "$OUT/assets.tar"

# demo/public is the SOURCE for the vite build; the dev/preview server serves
# demo/dist. Mirror into dist when it exists so a reload picks the change up
# without re-running the bundler.
if [ -d "$ROOT/demo/dist" ]; then
  cp -f "$OUT/assets.tar.zst" "$OUT/manifest.json" "$ROOT/demo/dist/"
  echo ">> mirrored into demo/dist (served build)"
fi
echo ">> done -- reload the page"
