#!/usr/bin/env bash
# Refresh the VENDORED Blender source tree in blender/.
#
# blender/ is not a checkout and not a submodule: it is a trimmed snapshot of
# the fork, committed to this repository so it can be edited in place. That is
# the whole point -- features get cut and behaviour changed in the source, not
# only through CMake flags -- so this script exists to move the snapshot to a
# newer upstream ref, not to be run on every build.
#
# It takes the GitHub source tarball rather than a clone: Blender's history is
# multiple GB and none of it is useful here.
#
#   usage: vendor_blender.sh [--force]
#          BLENDER_URL / BLENDER_REF override the Makefile defaults.
#
# WHAT IT DROPS (and why it is safe):
#   locale/       80 MB of translation .po files, referenced only inside
#                 `if(WITH_INTERNATIONAL)` in source/creator/CMakeLists.txt,
#                 and WITH_INTERNATIONAL is OFF for wasm.
#   tests/files/  22 MB of test .blend data. tests/CMakeLists.txt never refers
#                 to it (add_subdirectory(tests) itself IS unconditional, so the
#                 rest of tests/ stays).
# Everything else is kept verbatim, then the release/datafiles LFS pointers are
# resolved in place (see fetch_lfs_datafiles.sh).
set -euo pipefail
ROOT="${ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
URL="${BLENDER_URL:-https://github.com/HeyPuter/blender}"
REF="${BLENDER_REF:-$(sed -n 's/^BLENDER_REF *:= *//p' "$ROOT/Makefile" | head -1)}"
DEST="$ROOT/blender"
FORCE="${1:-}"

[ -n "$REF" ] || { echo "!! no BLENDER_REF (not in the Makefile either)"; exit 1; }

# Refreshing overwrites the tree. Local edits live in git history, so uncommitted
# ones would vanish silently -- refuse unless the caller insists.
if [ -d "$DEST" ] && [ -z "$(git -C "$ROOT" status --porcelain -- blender 2>/dev/null)" ]; then
  :
elif [ -d "$DEST" ] && [ "$FORCE" != "--force" ]; then
  echo "!! blender/ has uncommitted changes; commit them first or pass --force"
  git -C "$ROOT" status --short -- blender | head -20
  exit 1
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
SLUG="${URL#https://github.com/}"
echo ">> fetching $SLUG @ ${REF:0:12}"
curl -fsSL -o "$TMP/src.tar.gz" "https://codeload.github.com/$SLUG/tar.gz/$REF"
tar -xzf "$TMP/src.tar.gz" -C "$TMP"
SRC="$(find "$TMP" -maxdepth 1 -type d -name 'blender-*' | head -1)"
[ -d "$SRC" ] || { echo "!! unexpected tarball layout"; exit 1; }

echo ">> trimming (see header)"
rm -rf "$SRC/locale" "$SRC/tests/files"

bash "$ROOT/scripts/fetch_lfs_datafiles.sh" "$SRC"

echo ">> syncing -> blender/"
# Wipe-and-copy rather than rsync --delete: rsync is not present on a stock
# Windows/Git-Bash box, and this is a snapshot replacement, not a merge.
rm -rf "$DEST"
mkdir -p "$DEST"
cp -a "$SRC/." "$DEST/"
echo "$REF" > "$DEST/.vendored-ref"
echo ">> blender/ now at $REF ($(du -shL "$DEST" | cut -f1)); review with: git status -- blender"
