#!/usr/bin/env bash
# FreeType 2.13.3 → wasm-sysroot, WITH brotli (Blender requires woff2/brotli
# support) + zlib + png (all in the sysroot). Harfbuzz off (avoid the circular
# freetype<->harfbuzz dep).
set -euo pipefail
source "$(dirname "$0")/dep_common.sh"
src=$(fetch_extract \
  "https://download.savannah.gnu.org/releases/freetype/freetype-2.13.3.tar.gz" \
  "freetype-2.13.3.tar.gz" "freetype-2.13.3" \
  "https://downloads.sourceforge.net/project/freetype/freetype2/2.13.3/freetype-2.13.3.tar.gz" \
  "https://mirror.netcologne.de/savannah/freetype/freetype-2.13.3.tar.gz")
# So FreeType's BrotliDec/zlib/png pkg-config probes find our staged deps.
export PKG_CONFIG_PATH="$SYSROOT/lib/pkgconfig"
em_cmake "$src" freetype \
  -DFT_REQUIRE_ZLIB=ON \
  -DFT_REQUIRE_PNG=ON \
  -DFT_REQUIRE_BROTLI=ON \
  -DFT_DISABLE_HARFBUZZ=ON \
  -DFT_DISABLE_BZIP2=ON
log "done"
