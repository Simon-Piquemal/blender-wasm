#!/usr/bin/env bash
# numpy -> wasm-sysroot, as a STATIC archive plus its Python sources.
#
# Why not just `pip install numpy`: there is no wasm wheel that fits. Pyodide's
# wheels are SIDE_MODULEs that need a MAIN_MODULE host, and this Blender links
# CPython statically with no dynamic loading at all.
#
# Why not `meson --default-library=static`: numpy declares its C code with
# py.extension_module(), which is a shared module by construction. Meson still
# compiles every .c to a .o first, so this script lets meson run its own codegen
# and per-file compiles, then ignores the link step and archives the objects.
# The module init functions are registered in CPython's inittab (see
# cmake/stubs / creator.cc), which is how a static interpreter gets a C
# extension without a loader.
set -euo pipefail

ROOT="${ROOT:-$HOME/blender-wasm}"
source "$ROOT/scripts/dep_common.sh"

NUMPY_VERSION="${NUMPY_VERSION:-2.2.6}"
NATIVE="$DEPS/build/python-native/python"
export PYTHONPATH="$HOME/.local/lib/python3.13/site-packages"
export PATH="$HOME/.local/bin:$EM_BIN:$PATH"

src=$(fetch_extract \
  "https://github.com/numpy/numpy/releases/download/v${NUMPY_VERSION}/numpy-${NUMPY_VERSION}.tar.gz" \
  "numpy-${NUMPY_VERSION}.tar.gz" "numpy-${NUMPY_VERSION}")

log "numpy source at $src"

# The pip-installed `cython` console script carries a shebang pointing at
# /usr/local/bin/python3.13, which does not exist here: the 3.13 interpreter is
# an UNINSTALLED build under deps/build/python-native. Meson reports that as
# "No such file or directory: 'cython'", which reads like a missing PATH entry
# and is not. Generate a launcher that runs Cython through the interpreter we
# actually have.
# Named `cython` inside a directory we prepend to PATH: Cython runs on the
# BUILD machine (it emits C, it does not cross-compile), so meson resolves it
# through the native machine file / PATH and ignores the [binaries] entry in a
# cross file. Putting it on PATH works whichever way meson decides to look.
CYTHON_BIN="$BLD/wasmbin"
CYTHON_SHIM="$CYTHON_BIN/cython"
mkdir -p "$CYTHON_BIN"
export PATH="$CYTHON_BIN:$PATH"
{
  echo '#!/usr/bin/env bash'
  echo "export PYTHONPATH=\"$PYTHONPATH\""
  echo "exec \"$NATIVE\" -c 'import sys; from Cython.Compiler.Main import setuptools_main; sys.exit(setuptools_main())' \"\$@\""
} > "$CYTHON_SHIM"
chmod +x "$CYTHON_SHIM"
"$CYTHON_SHIM" -V

CROSS="$BLD/numpy-cross-emscripten.ini"
mkdir -p "$BLD"
cat > "$CROSS" <<EOF
[binaries]
c = '$EM_BIN/emcc'
cpp = '$EM_BIN/em++'
ar = '$EM_BIN/emar'
strip = '$EM_BIN/emstrip'
python = '$NATIVE'
cython = '$CYTHON_SHIM'

[built-in options]
c_args = ['-O2', '-pthread', '-fexceptions', '-DNPY_DISABLE_OPTIMIZATION=1']
cpp_args = ['-O2', '-pthread', '-fexceptions', '-DNPY_DISABLE_OPTIMIZATION=1']
c_link_args = ['-pthread', '-fexceptions']
cpp_link_args = ['-pthread', '-fexceptions']

[properties]
needs_exe_wrapper = true
longdouble_format = 'IEEE_QUAD_LE'  # emcc: sizeof(long double) == 16

[host_machine]
system = 'emscripten'
cpu_family = 'wasm32'
cpu = 'wasm32'
endian = 'little'
EOF

# Point the build interpreter's sysconfig at the WASM build. Without this,
# meson's python module asks the native 3.13 what a Python extension looks like
# and gets x86_64 answers: it compiled the Cython sanity check against the
# native pyconfig.h and failed on
#   pyport.h: "LONG_BIT definition appears wrong for platform"
# because SIZEOF_VOID_P was 8 where the target has 4. _PYTHON_SYSCONFIGDATA_NAME
# is CPython's own cross-compilation switch and makes one interpreter report the
# other one's configuration, which is all meson actually needs here.
export _PYTHON_SYSCONFIGDATA_NAME=_sysconfigdata__emscripten_wasm32-emscripten
export PYTHONPATH="$SYSROOT/lib/python3.13:$PYTHONPATH"
"$NATIVE" -c "import sysconfig; print('target SIZEOF_VOID_P =', sysconfig.get_config_var('SIZEOF_VOID_P'))"

log "cross file: $CROSS"

# numpy ships a PATCHED meson under vendored-meson/: its meson.build uses a
# custom "features" module for CPU dispatch that upstream meson does not have
# (ERROR: Module "features" does not exist). Use numpys own copy.
BUILDDIR="$BLD/numpy-wasm"
rm -rf "$BUILDDIR"

# meson-python normally drives this; we call meson directly so we keep the
# build tree (and therefore the objects) instead of getting a wheel.
"$NATIVE" "$src/vendored-meson/meson/meson.py" setup "$BUILDDIR" "$src" \
  --cross-file "$CROSS" \
  --prefix="$SYSROOT" \
  -Dbuildtype=release \
  -Dblas= -Dlapack= \
  -Dallow-noblas=true \
  -Ddisable-optimization=true \
  -Dcpu-baseline=none \
  -Dcpu-dispatch=none \
  2>&1 | tail -40

log "meson setup done, compiling (link failures are expected and ignored)"
"$NATIVE" "$src/vendored-meson/meson/meson.py" compile -C "$BUILDDIR" 2>&1 | tail -60 || true

log "objects produced:"
find "$BUILDDIR" -name "*.o" | wc -l
log "done"
