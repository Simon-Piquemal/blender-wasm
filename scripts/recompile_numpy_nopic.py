#!/usr/bin/env python3
"""Recompile numpy's objects without -fPIC.

Meson builds every Python extension as a shared module, so every object comes
out position-independent. Linking those into a non-PIC static executable fails
with, for every function address numpy takes:

    relocation R_WASM_TABLE_INDEX_REL_SLEB is not supported against an
    undefined symbol `DOUBLE_fmax_indexed`

There is no meson switch for this: py.extension_module() is a shared module by
construction and PIC comes with it. So rather than fight the build system, reuse
what it already worked out -- compile_commands.json holds the exact command line
for every source, include paths, generated headers, feature defines and all --
and replay each one with -fPIC dropped and the output redirected.

Usage: recompile_numpy_nopic.py <builddir> <outdir> [jobs]
"""

import json
import os
import shlex
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor


_SKIP_MODULES = (
    "_multiarray_tests",
    "_umath_tests",
    "_rational_tests",
    "_struct_ufunc_tests",
    "_operand_flag_tests",
)


def main():
    builddir = sys.argv[1]
    outdir = sys.argv[2]
    jobs = int(sys.argv[3]) if len(sys.argv) > 3 else (os.cpu_count() or 4)

    with open(os.path.join(builddir, "compile_commands.json")) as fh:
        entries = json.load(fh)

    wanted = []
    for e in entries:
        out = e.get("output", "")
        if not out.endswith(".o"):
            continue
        # numpy's own test extensions never run here, and _umath_tests pulls in
        # a CPU-dispatch variable that only its shared-module link resolves
        # (undefined symbol: _umath_tests_dispatch_var). Nothing imports them.
        if any(m in out for m in _SKIP_MODULES):
            continue
        # Extension modules AND every static library feeding them. numpy puts
        # its CPU-dispatch loops in separate archives named
        # lib<name>.dispatch.h_baseline.a.p/, not under the module .so.p/ dir.
        # Missing those linked cleanly (ERROR_ON_UNDEFINED_SYMBOLS=0 stubbed
        # them) and then aborted at runtime with
        #   Aborted(missing function: DOUBLE_log)
        # the first time anything touched a ufunc.
        if ".so.p/" not in out and ".a.p/" not in out:
            continue
        wanted.append(e)

    print("replaying %d compiles without -fPIC into %s" % (len(wanted), outdir))
    os.makedirs(outdir, exist_ok=True)

    def run(index_entry):
        index, e = index_entry
        argv = shlex.split(e["command"])
        argv = [a for a in argv if a not in ("-fPIC", "-fpic", "-fPIE", "-fpie")]
        # Redirect -o to a flat, collision-proof name: numpy has many objects
        # with the same basename across modules (loops.c.o, ufunc_object.c.o).
        flat = "np%04d_%s" % (index, os.path.basename(e["output"]))
        target = os.path.join(outdir, flat)
        for i, a in enumerate(argv):
            if a == "-o":
                argv[i + 1] = target
                break
        proc = subprocess.run(
            argv, cwd=e.get("directory", builddir),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        if proc.returncode != 0:
            return (e["output"], proc.stdout.decode("utf-8", "replace")[-1500:])
        return None

    failures = []
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        for result in pool.map(run, enumerate(wanted, 1)):
            if result is not None:
                failures.append(result)

    produced = len([f for f in os.listdir(outdir) if f.endswith(".o")])
    print("objects produced: %d" % produced)
    if failures:
        print("FAILURES: %d" % len(failures))
        for name, log in failures[:3]:
            print("--- %s ---" % name)
            print(log)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
