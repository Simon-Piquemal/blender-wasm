# CMake initial-cache for the full Blender → WASM build (WITH_BLENDER=ON).
# Initial target: get DNA/RNA codegen running (host tools via node) + the core
# modules compiling. Headless for now (no GL/GHOST) to minimize configure walls;
# WebGPU backend + GUI come after the core builds. Passed via `cmake -C`.

set(WITH_BLENDER             ON  CACHE BOOL "")
set(WITH_PYTHON              ON  CACHE BOOL "")
set(WITH_PYTHON_MODULE       OFF CACHE BOOL "")
set(WITH_PYTHON_INSTALL      OFF CACHE BOOL "")
set(WITH_LIBS_PRECOMPILED    OFF CACHE BOOL "")
set(WITH_STRICT_BUILD_OPTIONS OFF CACHE BOOL "")

# --- our cross-compiled CPython 3.13 in the wasm sysroot -------------------
# The codegen steps (generate_datamodels.cmake, blenkernel, nodes) shell out to
# ${PYTHON_EXECUTABLE}. Emscripten's toolchain points find_program at the wasm
# sysroot, which has no interpreter, so the variable resolved to
# PYTHON_EXECUTABLE-NOTFOUND and ninja died with "PYTHON_EXECUTABLE-NOTFOUND:
# not found". Point it at the native build-python that scripts/build_python.sh
# already compiled to drive the CPython cross-build -- same 3.13 series as
# PYTHON_VERSION below, so codegen runs on the version it is generating for.
set(PYTHON_EXECUTABLE    "${CMAKE_SOURCE_DIR}/../deps/build/python-native/python" CACHE FILEPATH "")
set(PYTHON_VERSION       "3.13" CACHE STRING "")
set(PYTHON_INCLUDE_DIR   "${CMAKE_SOURCE_DIR}/../wasm-sysroot/include/python3.13" CACHE PATH "")
set(PYTHON_INCLUDE_DIRS  "${CMAKE_SOURCE_DIR}/../wasm-sysroot/include/python3.13" CACHE PATH "")
set(PYTHON_LIBRARY       "${CMAKE_SOURCE_DIR}/../wasm-sysroot/lib/libpython3.13.a" CACHE FILEPATH "")
set(PYTHON_LIBPATH       "${CMAKE_SOURCE_DIR}/../wasm-sysroot/lib" CACHE PATH "")
set(WITH_PYTHON_NUMPY    OFF CACHE BOOL "")

# --- GPU: WebGPU backend (our gpu/webgpu/), no GL/Vulkan -------------------
# Headless GHOST (no window); EEVEE renders offscreen through the WebGPU device
# obtained from JS (emscripten_webgpu_get_device), bypassing GL/Vulkan context.
set(WITH_HEADLESS            OFF CACHE BOOL "")
set(WITH_GHOST_WEB           ON  CACHE BOOL "")
set(WITH_WEBGPU_BACKEND      ON  CACHE BOOL "")
set(WITH_OPENGL_BACKEND      OFF CACHE BOOL "")
set(WITH_VULKAN_BACKEND      OFF CACHE BOOL "")
set(WITH_GHOST_SDL           OFF CACHE BOOL "")
# The web GHOST backend is the only one; disable the desktop backends so their
# find_package(X11 REQUIRED) / Wayland probes don't wall a headless CI runner
# (they only passed locally because the dev box has libx11-dev installed).
set(WITH_GHOST_X11           OFF CACHE BOOL "")
set(WITH_GHOST_WAYLAND       OFF CACHE BOOL "")
set(WITH_OPENIMAGEDENOISE    OFF CACHE BOOL "")
set(WITH_INTERNATIONAL       OFF CACHE BOOL "")
set(WITH_HARFBUZZ            OFF CACHE BOOL "")
set(WITH_FRIBIDI            OFF CACHE BOOL "")

# --- trim heavy/irrelevant features to reach codegen fast ------------------
set(WITH_CYCLES              OFF CACHE BOOL "")
set(WITH_OPENVDB             OFF CACHE BOOL "")
# WITH_OPENSUBDIV lives with the KEPT ON PURPOSE block below (it is ON).
# NOTE: WITH_OPENCOLORIO / WITH_OPENIMAGEIO / WITH_COMPOSITOR_CPU are no
# longer CMake options in Blender 5.x (both libs are always built, the
# compositor is GPU-only), so setting them here was a no-op. OCIO being
# unconditional is why datafiles/colormanagement must still ship -- see
# scripts/trim_ocio.py for the diet it gets instead.
set(WITH_TBB                 ON  CACHE BOOL "")
set(WITH_TBB_MALLOC_PROXY    OFF CACHE BOOL "")
set(WITH_LIBMV               OFF CACHE BOOL "")
set(WITH_DRACO               OFF CACHE BOOL "")
set(WITH_MESHOPTIMIZER       OFF CACHE BOOL "")
set(WITH_MOD_FLUID           OFF CACHE BOOL "")
set(WITH_AUDASPACE           OFF CACHE BOOL "")
set(WITH_CODEC_FFMPEG        OFF CACHE BOOL "")
set(WITH_CODEC_SNDFILE       OFF CACHE BOOL "")
set(WITH_SDL                 OFF CACHE BOOL "")
set(WITH_JACK                OFF CACHE BOOL "")
set(WITH_PULSEAUDIO          OFF CACHE BOOL "")
set(WITH_OPENAL              OFF CACHE BOOL "")
set(WITH_FFTW3               OFF CACHE BOOL "")
set(WITH_IMAGE_OPENJPEG      OFF CACHE BOOL "")
set(WITH_GMP                 OFF CACHE BOOL "")
set(WITH_POTRACE             OFF CACHE BOOL "")
set(WITH_HARU                OFF CACHE BOOL "")
set(WITH_MANIFOLD            OFF CACHE BOOL "")
set(WITH_QUADRIFLOW          OFF CACHE BOOL "")
set(WITH_INPUT_NDOF          OFF CACHE BOOL "")
set(WITH_BULLET              OFF CACHE BOOL "")
set(WITH_XR_OPENXR           OFF CACHE BOOL "")
set(WITH_ALEMBIC             OFF CACHE BOOL "")
set(WITH_USD                 OFF CACHE BOOL "")
set(WITH_HYDRA               OFF CACHE BOOL "")
set(WITH_MATERIALX           OFF CACHE BOOL "")

# --- second pass: heavy features still ON by default upstream ---------------
# Each of these compiles a sizeable chunk of C++ into the module for something
# a browser session cannot or will not use. Cost of re-enabling: flip to ON.
set(WITH_FREESTYLE           OFF CACHE BOOL "")  # NPR line renderer: whole
                                                 # intern/freestyle tree + its
                                                 # Python API, and it only runs
                                                 # under a CPU render pipeline
                                                 # we do not ship (no Cycles).
set(WITH_IK_ITASC            OFF CACHE BOOL "")  # alternative armature IK
                                                 # solver (Eigen-heavy); the
                                                 # default IK solver stays via
                                                 # WITH_IK_SOLVER.
set(WITH_IO_FBX              ON  CACHE BOOL "")  # C++ FBX importer (ufbx).
                                                 # ON because the Python FBX
                                                 # add-on cannot run here: it
                                                 # imports numpy, which this
                                                 # CPython does not have. ufbx
                                                 # is self-contained C++ with no
                                                 # such dependency, so it is the
                                                 # only way .fbx opens at all in
                                                 # the browser build. Import
                                                 # only -- there is no C++ FBX
                                                 # writer upstream.
set(WITH_IO_GREASE_PENCIL    OFF CACHE BOOL "")  # GP SVG/PDF IO; the PDF half
                                                 # was already dead (WITH_HARU
                                                 # is OFF).
set(WITH_IMAGE_CINEON        OFF CACHE BOOL "")  # DPX/Cineon film scans.
set(WITH_IMAGE_WEBP          OFF CACHE BOOL "")  # no libwebp in the sysroot.
# Auto-disabled by dependency rules, listed for the record: WITH_MOD_OCEANSIM
# and WITH_RUBBERBAND follow WITH_FFTW3=OFF, WITH_NANOVDB follows WITH_OPENVDB.

# KEPT ON PURPOSE (modelling tools the demo is actually for), even though both
# cost real module size:
#   WITH_MOD_REMESH  Remesh modifier -- Blocks/Smooth/Sharp modes work; the
#                    Voxel mode needs OpenVDB, which stays OFF.
#   WITH_UV_SLIM     "Minimum Stretch" unwrap (Eigen sparse solvers).
#   WITH_OPENSUBDIV  Subsurf/Multires modifiers + the Subdivision Surface
#                    geometry node -- see the block right below.
# Note WITH_QUADRIFLOW (Object > Quad Remesh) was already OFF before this file's
# size pass -- flip it ON if real quad remeshing matters more than the download.

# --- OpenSubdiv (CPU only) --------------------------------------------------
# Was OFF, which made the Subsurf modifier a no-op reporting "disabled build
# without opensubdiv": every subdivided scene rendered as its base cage. Set
# explicitly (upstream default is ON) so the intent is on the record.
# scripts/build_opensubdiv.sh stages osdCPU (v3_7_0, the version Blender 5.3
# pins) in the sysroot; OpenSubdiv's own GPU evaluators are off -- GPU
# subdivision goes through Blender's bf::gpu compute path (WebGPU) instead.
set(WITH_OPENSUBDIV          ON  CACHE BOOL "")
# build_files/cmake/Modules/FindOpenSubdiv.cmake is a hand-rolled module whose
# only HINTS are OPENSUBDIV_ROOT_DIR and two /opt/lib paths. CMAKE_PREFIX_PATH
# covers the sysroot anyway, but be explicit so a stray /opt/lib/opensubdiv on a
# dev box can never win. It resolves TWO components, osdGPU and osdCPU: the
# GPU-less build stages an empty libosdGPU.a for that lookup to land on (see
# scripts/build_opensubdiv.sh).
set(OPENSUBDIV_ROOT_DIR "${CMAKE_SOURCE_DIR}/../wasm-sysroot" CACHE PATH "")

# emscripten ships arm_neon.h emulation → Blender's NEON probe misfires.
set(SUPPORTS_NEON_BUILD      FALSE CACHE INTERNAL "")
set(WITH_TESTS               OFF CACHE BOOL "")
set(WITH_GTESTS              OFF CACHE BOOL "")
set(CMAKE_INSTALL_PREFIX     "${CMAKE_BINARY_DIR}/install" CACHE PATH "")
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY BOTH CACHE STRING "")
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE BOTH CACHE STRING "")
set(CMAKE_FIND_ROOT_PATH_MODE_PACKAGE BOTH CACHE STRING "")
