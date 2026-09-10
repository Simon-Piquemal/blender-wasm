#!/usr/bin/env bash
# Release/demo link: like link_blender_web.sh but with NO --preload-file —
# the demo app (demo/) extracts assets into OPFS once (setup screen) and the
# wasm mounts them via the wasmfs OPFS backend (BLENDER_WEB_OPFS env, see
# creator.cc). Outputs into demo/public/:
#   blender.js            emscripten glue (loads wasm via Module.instantiateWasm)
#   blender.wasm.zst      zstd --ultra -21 of the wasm
#   assets.tar.zst        5.3/ + lib/python3.13/ (PYTHONHOME=/opfs/assets)
#   wgsl-cache.json       pre-seeded GLSL->WGSL translations (see below)
# The dev fast-path (link_blender_web.sh) is untouched and skips zstd.
set -euo pipefail
ROOT="${ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
export PATH="$ROOT/emsdk/upstream/emscripten:$PATH"
BUILD="$ROOT/build-blender"
OUT="$ROOT/demo/public"
SYSROOT="$ROOT/wasm-sysroot"
STAGE="$ROOT/demo/.stage"

mkdir -p "$OUT"

# --- stage runtime assets (same trims as the dev script) --------------------
echo ">> staging assets -> $STAGE"
rm -rf "$STAGE"; mkdir -p "$STAGE/5.3" "$STAGE/lib"
cp -r "$ROOT/blender/scripts"           "$STAGE/5.3/scripts"
cp -r "$ROOT/blender/release/datafiles" "$STAGE/5.3/datafiles"
# Python under lib/python3.13 so PYTHONHOME=/opfs/assets resolves the stdlib.
cp -r "$SYSROOT/lib/python3.13" "$STAGE/lib/python3.13"
# Shared staging diet (fonts, feature-dead addons, CPython dev modules, OCIO):
# see scripts/trim_assets.sh for what each cut costs and how to opt out.
bash "$ROOT/scripts/trim_assets.sh" "$STAGE/5.3" "$STAGE/lib/python3.13"

# --- essentials brush assets -> 5.3/datafiles/assets/brushes -----------------
# Blender 4.3+ ships sculpt/paint brushes as ID *assets* in the bundled
# "essentials" library, resolved at runtime from <datafiles>/assets (see
# essentials_directory_path()). Without them BKE_paint_brush() stays null and
# sculpt/paint strokes have NO effect (the Brush Asset panel is empty). The
# blends live in blender/assets/ (git-lfs); we only need brushes/ for
# sculpt+paint (skip the larger nodes/ asset libraries).
#
# On by default. (Bundling these 64-bit-authored blends once tripped a wasm32
# blend-read bug when the asset library was indexed — blo_bhead_id_asset_data_address
# read the asset_data pointer at the reader's width, truncating the file's
# 64-bit value and crashing; that's fixed in readfile.cc.) Set
# NOW OFF BY DEFAULT: sculpt/paint are hidden from this build, so the 6.6 MB
# asset library is unreachable. BLENDER_WEB_BUNDLE_BRUSHES=1 puts it back.
#
# Source resolution: the brush blends are NOT hosted on any anonymous git-LFS
# (they 404 on every fork's GitHub LFS and are auth-gated upstream), so the
# canonical source is the in-repo VENDORED copy (demo/brush-assets/), which is
# always present in CI. Fall back to blender/assets (dev checkout with LFS) or
# the on-disk native reference only if the vendored copy is somehow missing.
if [ "${BLENDER_WEB_BUNDLE_BRUSHES:-0}" != "0" ]; then
  ASSETS_DST="$STAGE/5.3/datafiles/assets"
  sculpt_name="brushes/essentials_brushes-mesh_sculpt.blend"
  ASSETS_SRC=""
  for cand in \
      "$ROOT/demo/brush-assets" \
      "$ROOT/blender/assets" \
      $(ls -d "$ROOT"/tests/native/blender-*/5.3/datafiles/assets 2>/dev/null | head -1); do
    if [ -n "$cand" ] && [ "$(stat -c%s "$cand/$sculpt_name" 2>/dev/null || echo 0)" -ge 1024 ]; then
      ASSETS_SRC="$cand"; break
    fi
  done
  # Last resort for a dev checkout: materialize the LFS content in blender/assets.
  if [ -z "$ASSETS_SRC" ] && [ -e "$ROOT/blender/assets/$sculpt_name" ]; then
    ( cd "$ROOT/blender" && git lfs pull --include="assets/brushes/**" ) 2>/dev/null || true
    [ "$(stat -c%s "$ROOT/blender/assets/$sculpt_name" 2>/dev/null || echo 0)" -ge 1024 ] && \
      ASSETS_SRC="$ROOT/blender/assets"
  fi
  echo ">> brush asset source: ${ASSETS_SRC:-<none found>}"
  sculpt_blend="$ASSETS_SRC/$sculpt_name"
  if [ "$(stat -c%s "$sculpt_blend" 2>/dev/null || echo 0)" -lt 1024 ]; then
    # Still unresolved (LFS pointers): do NOT copy — bundling 131-byte pointer
    # stubs would ship broken "blends". Skip cleanly; the demo boots without
    # brushes (sculpt/paint disabled) rather than shipping garbage.
    echo "!! WARNING: essentials brush assets unresolved (LFS pointers) — NOT bundling; sculpt/paint disabled"
  else
    mkdir -p "$ASSETS_DST/brushes"
    cp -f "$ASSETS_SRC/blender_assets.cats.txt" "$ASSETS_DST/"        2>/dev/null || true
    cp -f "$ASSETS_SRC/LICENSE"                 "$ASSETS_DST/"        2>/dev/null || true
    cp -f "$ASSETS_SRC"/brushes/*.blend         "$ASSETS_DST/brushes/" 2>/dev/null || true
    echo ">> staged essentials brushes ($(ls "$ASSETS_DST"/brushes/*.blend | wc -l) blends)"
  fi
else
  echo ">> brush assets NOT bundled (BLENDER_WEB_BUNDLE_BRUSHES=0) — sculpt/paint brushes disabled"
fi

# --- assets.tar.zst ----------------------------------------------------------
echo ">> assets.tar.zst (zstd --ultra -21 -T0)"
( cd "$STAGE" && tar -cf "$OUT/assets.tar" 5.3 lib )
zstd -f --ultra -21 -T0 "$OUT/assets.tar" -o "$OUT/assets.tar.zst"
rm -f "$OUT/assets.tar"

# --- link without preload ----------------------------------------------------
raw=$(ninja -C "$BUILD" -t commands blender | grep -- "-o bin/blender.js" | tail -1)
cmd=${raw#*&& }
cmd=${cmd%% && cd *}
cmd=${cmd/-o bin\/blender.js/-o $OUT/blender.js}
cmd=${cmd//-sNODERAWFS=1/}

# Custom wasmfs backends (compiled against emscripten's internal wasmfs headers):
#  - localdir_backend.cpp: lazy async mount of a user-picked folder (open/save).
#  - provider_backend.cpp + provider-fs.js: gecko-wasm's FsProvider backend,
#    verbatim — serves /assets from the in-memory decompressed tar
#    (Module.geckoProviders[0], built in demo/src/main.js). Zero-copy: files are
#    views into the one decompressed buffer, materialized on open.
WASMFS_INC="$ROOT/emsdk/upstream/emscripten/system/lib/wasmfs"

# --use-port=emdawnwebgpu: source/blender/gpu/CMakeLists.txt adds the port to
# this module's COMPILES only, and says the final link is expected to add it
# via CMAKE_EXE_LINKER_FLAGS -- which nothing in this repo ever sets. The
# relink therefore produced a module with no WebGPU implementation at all,
# and -sERROR_ON_UNDEFINED_SYMBOLS=0 let it through to abort at startup with
# "missing function: wgpuCreateInstance".

# Brotli archives: freetype is built with FT_REQUIRE_BROTLI=ON (woff2 fonts --
# and every font we ship IS woff2), but Blender's CMake puts only
# libfreetype.a on the link line, never its brotli dependency. With
# -sERROR_ON_UNDEFINED_SYMBOLS=0 the link happily stubs the symbol and startup
# then dies with "missing function: BrotliDecoderDecompress". Order matters:
# dec before common.
# -sDEFAULT_LIBRARY_FUNCS_TO_INCLUDE=$GL: OFFSCREENCANVAS_SUPPORT's JS glue
# reaches into the $GL library, but this build has no WebGL user, so -O2's
# dead-code elimination drops it and startup aborts with
#   Assertion failed: OFFSCREENCANVAS_SUPPORT assumes GL is in use
#   pthread_create: failed to transfer control of canvas to OffscreenCanvas
# -O1 kept it by accident. Force it in -- single-quoted, because the link
# line survives TWO expansions -- the double-quoted WEB_FLAGS assignment and
# then `eval` -- so the dollar needs three backslashes, or set -u aborts with
# "GL: unbound variable".
# -O2 (was -O1): the whole module was linked at -O1, which is why it is both
# oversized and slow -- it also makes the in-browser GLSL->WGSL toolchain
# (glslang + Tint, compiled in) crawl. -O2 is the balanced level: smaller AND
# faster, unlike -Oz which trades runtime speed for size. RISK: the CI comment
# warns that emcc + wasm-opt over a ~145 MB module already spikes RAM; -O2
# raises that. The workflow adds a 16 GB swapfile for exactly this. Revert to
# -O1 here if the runner starts getting OOM-killed (exit 143).
# -g0: strip DWARF *and* the wasm name section. On a ~145 MB module the symbol
# names alone are tens of MB of pure download with no runtime role; release
# stack traces become numeric offsets, which is what the dev link
# (link_blender_web.sh, still -g2) is for.
# -sINITIAL_MEMORY=512MB (was 1 GB): this heap is COMMITTED at startup, not
# merely reserved -- the pthread build backs it with a SharedArrayBuffer, and
# Chrome bills that buffer to every agent that maps it. Measured in-page with
# performance.measureUserAgentSpecificMemory(): 3311 MB for the tab, as three
# ~1 GB entries (page + 2 workers). So each MB saved here is saved three times
# over in what the browser reports. ALLOW_MEMORY_GROWTH stays on, so a scene
# that genuinely needs more still gets it, at the cost of one growth event.
# -sPTHREAD_POOL_SIZE=16 (was 32): 32 pre-spawned workers x 4 MB of stack is
# 128 MB of heap held for threads a browser viewport never runs at once.
# STRICT=0 keeps the safety net -- past 16, Blender still gets its thread, it
# is just created on demand instead of coming from the pool.
WEB_FLAGS="-pthread \
  -sEXIT_RUNTIME=0 -g0 \
  -O2 \
  -sALLOW_MEMORY_GROWTH=1 -sINITIAL_MEMORY=536870912 -sMAXIMUM_MEMORY=4294967296 \
  -sSTACK_SIZE=16777216 -sDEFAULT_PTHREAD_STACK_SIZE=4194304 \
  -sPTHREAD_POOL_SIZE=16 -sPTHREAD_POOL_SIZE_STRICT=0 \
  -sPROXY_TO_PTHREAD=1 \
  -sOFFSCREENCANVAS_SUPPORT=1 -sOFFSCREENCANVASES_TO_PTHREAD=#canvas -sDEFAULT_LIBRARY_FUNCS_TO_INCLUDE=\\\$GL \
  --use-port=emdawnwebgpu \
  -sWASMFS -sFORCE_FILESYSTEM=1 \
  -std=c++17 -I$WASMFS_INC $ROOT/demo/localdir_backend.cpp $ROOT/demo/provider_backend.cpp \
  --js-library $ROOT/demo/localdir_lib.js \
  --js-library $ROOT/demo/provider-fs.js \
  --post-js $ROOT/demo/wgsl_cache_worker.js \
  -sEXPORTED_RUNTIME_METHODS=FS,callMain,ccall,cwrap,ENV,HEAPU8,HEAPU16,HEAPF32 \
  -sENVIRONMENT=web,worker -sASSERTIONS=1 -sERROR_ON_UNDEFINED_SYMBOLS=0 \
  $SYSROOT/lib/libbrotlidec.a $SYSROOT/lib/libbrotlicommon.a \
  -Wl,--start-group $SYSROOT/lib/libnumpy.a $SYSROOT/lib/libpython3.13.a -Wl,--end-group"

echo ">> relinking blender → $OUT/blender.js (release, no preload)"
( cd "$BUILD" && eval "$cmd $WEB_FLAGS" )

echo ">> blender.wasm.zst (zstd --ultra -21 -T0)"
zstd -f --ultra -21 -T0 "$OUT/blender.wasm" -o "$OUT/blender.wasm.zst"
rm -f "$OUT/blender.wasm"

# --- pre-seeded WGSL translations ------------------------------------------
# Without this the browser runs the whole GLSL -> SPIR-V -> WGSL toolchain
# (glslang + Tint, compiled into the module) for EVERY shader it meets, at
# 200-500 ms a piece: ~30 stalls just to reach the default scene, more on every
# mode switch. It is the single biggest source of interactive stutter.
#
# It used to be copied from web/ with `2>/dev/null || true`, i.e. SILENTLY
# SKIPPED -- and web/ is written by the dev link + scripts/wgsl_seed.mjs, which
# CI never runs, so every CI-built release shipped with no cache at all.
# Resolution order: the committed demo/wgsl-cache.json.zst, then a fresh local
# seed in web/. Missing is now loud.
# Shipped UNCOMPRESSED: the consumer is a pthread worker doing a synchronous
# XHR (demo/wgsl_cache_worker.js) and there is no sync unzstd there. Any static
# host gzips it on the wire (2.5 MB -> ~0.4 MB); git keeps the zstd copy.
WGSL_SEED=""
if [ -f "$ROOT/demo/wgsl-cache.json.zst" ]; then
  zstd -d -f "$ROOT/demo/wgsl-cache.json.zst" -o "$OUT/wgsl-cache.json"
  WGSL_SEED="demo/wgsl-cache.json.zst (committed)"
elif [ -f "$ROOT/web/wgsl-cache.json" ]; then
  cp -f "$ROOT/web/wgsl-cache.json" "$OUT/wgsl-cache.json"
  WGSL_SEED="web/wgsl-cache.json (fresh seed)"
fi
if [ -n "$WGSL_SEED" ]; then
  echo ">> wgsl seed: $WGSL_SEED -> $(du -h "$OUT/wgsl-cache.json" | cut -f1)"
else
  echo "!! WARNING: no WGSL cache found - the demo will translate every shader"
  echo "!!          at runtime (200-500 ms each). Regenerate with:"
  echo "!!            node scripts/wgsl_seed.mjs   # writes web/wgsl-cache.json"
  echo "!!          then commit it as demo/wgsl-cache.json.zst (zstd -19)."
fi

# Manifest with decompressed sizes (zstddec wants explicit sizes).
python3 - "$OUT" <<'PYEOF'
import json
import os
import subprocess
import sys
out = sys.argv[1]
sizes = {}
for name in ("blender.wasm.zst", "assets.tar.zst"):
    r = subprocess.run(["zstd", "-lv", os.path.join(out, name)], capture_output=True, text=True)
    for line in r.stdout.splitlines():
        if "Decompressed Size" in line:
            sizes[name] = int(line.split("(")[1].split()[0].replace(",", ""))
open(os.path.join(out, "manifest.json"), "w").write(json.dumps(sizes))
print("manifest:", sizes)
PYEOF

ls -la "$OUT"
# What a first-time visitor actually downloads (the two .zst + the glue).
echo ">> download budget:"
du -ch "$OUT/blender.wasm.zst" "$OUT/assets.tar.zst" "$OUT/blender.js" | sed 's/^/>>   /'
echo ">> done"
