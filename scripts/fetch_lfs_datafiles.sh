#!/usr/bin/env bash
# Resolve the git-LFS pointers under <tree>/release/datafiles in place.
#
# The Blender fork's own GitHub LFS storage does NOT host these objects (forks
# do not inherit LFS), and a GitHub source tarball ships pointers rather than
# content. Upstream projects.blender.org serves them anonymously over the LFS
# batch API, which is what this fetches -- by content hash, so it is independent
# of any branch or fork.
#
# Idempotent: a file that is no longer a pointer is left alone.
#
#   usage: fetch_lfs_datafiles.sh <blender source tree>
set -euo pipefail
TREE="${1:?usage: fetch_lfs_datafiles.sh <blender source tree>}"
LFS_API="https://projects.blender.org/blender/blender.git/info/lfs/objects"
DIR="$TREE/release/datafiles"

mapfile -t POINTERS < <(grep -rl "^version https://git-lfs" "$DIR" 2>/dev/null || true)
if [ "${#POINTERS[@]}" -eq 0 ]; then
  echo ">> LFS datafiles already resolved"
  exit 0
fi
echo ">> resolving ${#POINTERS[@]} LFS pointers under release/datafiles"

fail=0
for f in "${POINTERS[@]}"; do
  oid=$(sed -n 's/^oid sha256://p' "$f")
  want=$(sed -n 's/^size //p' "$f")
  [ -n "$oid" ] && [ -n "$want" ] || { echo "!! unreadable pointer: $f"; fail=1; continue; }
  # The batch endpoint hands out a download href; the direct object URL works
  # too and costs one round trip instead of two.
  if ! curl -fsSL --retry 3 -o "$f.lfstmp" "$LFS_API/$oid" </dev/null; then
    echo "!! download failed: $f"; rm -f "$f.lfstmp"; fail=1; continue
  fi
  got=$(stat -c%s "$f.lfstmp")
  if [ "$got" != "$want" ]; then
    echo "!! size mismatch for $f (want $want, got $got)"; rm -f "$f.lfstmp"; fail=1; continue
  fi
  mv -f "$f.lfstmp" "$f"
done

if [ "$fail" -ne 0 ]; then
  echo "!! some LFS objects could not be resolved -- the build will ship broken datafiles"
  exit 1
fi
echo ">> release/datafiles resolved ($(du -shL "$DIR" | cut -f1))"
