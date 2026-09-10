# SPDX-License-Identifier: GPL-2.0-or-later

"""Fill in the corners of CPython that WebAssembly does not have.

Small, load-bearing gaps: the standard library exposes the function, calling it
raises, and the caller is usually third-party code that has no reason to expect
a platform without it. Patching the one function is a great deal less invasive
than patching every caller, and a wall clock is the honest answer when there is
no CPU clock to report.

Additive, like the other `webapp_*.py` modules: delete this file and the gaps
come back, nothing else changes.
"""

import time


def _log(msg):
    print("[webapp_pycompat] {}".format(msg))


def _patch_process_time():
    """`time.process_time()` raises under emscripten.

    There is no per-process CPU clock in a browser, so CPython's implementation
    fails with

        RuntimeError: the processor time used is not available or its value
        cannot be represented

    Blender's FBX exporter calls it to time its own passes
    (`export_fbx_bin.py`, `fbx_objects_elements`), so the whole export died on
    a progress message. Anything measuring elapsed work is served just as well
    by a monotonic wall clock here, where nothing else competes for the thread.
    """
    try:
        time.process_time()
        return False
    except Exception:
        pass

    monotonic = time.perf_counter
    origin = monotonic()

    def process_time():
        return monotonic() - origin

    def process_time_ns():
        return int((monotonic() - origin) * 1e9)

    time.process_time = process_time
    time.process_time_ns = process_time_ns
    return True


def register():
    patched = []
    if _patch_process_time():
        patched.append("time.process_time")
    if patched:
        _log("wall clock substituted for {}".format(", ".join(patched)))


def unregister():
    pass
