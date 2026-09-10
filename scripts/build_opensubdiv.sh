#!/usr/bin/env bash
# OpenSubdiv v3_7_0 → wasm-sysroot. Powers the Subsurf/Multires modifiers and
# the "Subdivision Surface" geometry node; without it BKE reports
# "disabled build without opensubdiv" and every subdivided mesh renders as its
# base cage.
#
# Version is exactly what Blender 5.3 pins:
#   blender/build_files/build_environment/cmake/versions.cmake
#     OPENSUBDIV_VERSION v3_7_0 / MD5 470d53c4d4335a601c33a052ce7c33b4
# (the GitHub tarball hashes to that MD5, so this is the same source drop the
# upstream precompiled libs are made from).
#
# CPU-ONLY build. Blender needs osdCPU (sdc/vtr/far/bfr + the CPU & TBB
# evaluators); every GPU backend is off because a wasm sysroot has no GL, GLES,
# OpenCL, CUDA, DX or Metal, and Blender's own GPU subdivision path runs through
# bf::gpu (our WebGPU backend) rather than through OpenSubdiv's osdGPU.
# TBB stays ON (NO_TBB=OFF): scripts/build_tbb.sh already stages a static
# libtbb.a + TBBConfig.cmake in the sysroot with the same ABI flags.
set -euo pipefail
source "$(dirname "$0")/dep_common.sh"
src=$(fetch_extract \
  "https://github.com/PixarAnimationStudios/OpenSubdiv/archive/v3_7_0.tar.gz" \
  "opensubdiv-v3_7_0.tar.gz" "OpenSubdiv-3_7_0")

# CMAKE_DISABLE_FIND_PACKAGE_OpenGLES: OpenSubdiv's root CMakeLists calls
# find_package(OpenGLES) UNCONDITIONALLY (it is not gated by NO_OPENGL), and
# emscripten's sysroot does ship GLES2/gl2.h + libGLESv2. A hit there flips the
# internal OSD_GPU flag back to TRUE and drags the whole GL evaluator/patch
# shader machinery into the build. Kill the probe outright.
# CMAKE_CXX_STANDARD=17: OpenSubdiv defaults to C++14; match Blender.
em_cmake "$src" opensubdiv \
  -DCMAKE_CXX_STANDARD=17 \
  -DNO_LIB=OFF \
  -DNO_EXAMPLES=ON \
  -DNO_TUTORIALS=ON \
  -DNO_REGRESSION=ON \
  -DNO_TESTS=ON \
  -DNO_GLTESTS=ON \
  -DNO_DOC=ON \
  -DNO_PTEX=ON \
  -DNO_OMP=ON \
  -DNO_TBB=OFF \
  -DNO_CUDA=ON \
  -DNO_OPENCL=ON \
  -DNO_CLEW=ON \
  -DNO_OPENGL=ON \
  -DNO_METAL=ON \
  -DNO_DX=ON \
  -DNO_GLEW=ON \
  -DNO_GLFW=ON \
  -DNO_GLFW_X11=ON \
  -DTBB_DIR="$SYSROOT/lib/cmake/TBB" \
  -DCMAKE_DISABLE_FIND_PACKAGE_OpenGLES=TRUE

# Empty libosdGPU.a shim.
#
# Blender's build_files/cmake/Modules/FindOpenSubdiv.cmake looks for BOTH
# components -- osdGPU *and* osdCPU -- and appends whatever find_library returns
# to OPENSUBDIV_LIBRARIES, which dependency_targets.cmake then feeds straight to
# target_link_libraries(bf_deps_optional_opensubdiv ...). With every GPU backend
# disabled OpenSubdiv never creates that target at all
# (opensubdiv/CMakeLists.txt: `if(OSD_GPU) ... osd_static_gpu ...`), so the
# lookup would resolve to the literal OPENSUBDIV_OSDGPU_LIBRARY-NOTFOUND and end
# up on Blender's link line.
#
# Stage a valid but EMPTY static archive instead: find_library succeeds, the
# link picks up zero objects, and nothing in blender/ has to be patched. If this
# ever gets deleted, configure will silently drop WITH_OPENSUBDIV again
# (set_and_warn_library_found) or the link will die on a -NOTFOUND item.
rm -f "$SYSROOT/lib/libosdGPU.a"
emar rcs "$SYSROOT/lib/libosdGPU.a" || true
if [ ! -s "$SYSROOT/lib/libosdGPU.a" ]; then
  printf '!<arch>\n' > "$SYSROOT/lib/libosdGPU.a"
fi
log "staged empty libosdGPU.a (no GPU backends; satisfies FindOpenSubdiv)"
log "done"
