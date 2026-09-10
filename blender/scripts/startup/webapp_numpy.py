# SPDX-License-Identifier: GPL-2.0-or-later

"""Make the statically linked numpy importable.

The wasm build has no dynamic loader, so numpy's extension modules are compiled
into the executable and registered in CPython's inittab under their full dotted
names (`numpy._core._multiarray_umath` and friends -- see
`scripts/package_numpy.sh` and the `BPY_NUMPY_STATIC` block in
`bpy_interface.cc`).

That is not enough on its own. `BuiltinImporter.find_spec` bails out the moment
it is given a package path:

    if path is not None:
        return None

and importing a SUBMODULE always passes the parent package's `__path__`. So the
inittab entries exist and are never consulted, and `from numpy._core import
_multiarray_umath` fails with ModuleNotFoundError while `sys.builtin_module_names`
lists it. The finder below closes exactly that gap: for names the interpreter
reports as builtin, it hands back a spec pointing at BuiltinImporter, which then
finds the inittab entry by name and initialises it normally.

Additive, like the rest of webapp_*.py: delete this file and numpy stops
importing, nothing else changes. It must run before anything imports numpy,
which startup scripts do -- the glTF and FBX add-ons only reach for it when an
operator actually executes.
"""

import sys
from importlib.machinery import BuiltinImporter, ModuleSpec


def _log(msg):
    print("[webapp_numpy] {}".format(msg))


class _StaticExtensionFinder:
    """Route dotted builtin extension modules to BuiltinImporter."""

    # Only these prefixes, so a typo in some unrelated import cannot be
    # answered by this finder and produce a confusing failure elsewhere.
    PREFIXES = ("numpy.",)

    @classmethod
    def find_spec(cls, fullname, path=None, target=None):
        if not fullname.startswith(cls.PREFIXES):
            return None
        try:
            import _imp

            if not _imp.is_builtin(fullname):
                return None
        except Exception:
            return None
        spec = ModuleSpec(fullname, BuiltinImporter, origin="built-in")
        spec.has_location = False
        return spec

    @classmethod
    def invalidate_caches(cls):
        return None


def register():
    if any(f is _StaticExtensionFinder for f in sys.meta_path):
        return
    builtin = [n for n in sys.builtin_module_names if n.startswith("numpy.")]
    if not builtin:
        # Nothing was linked in; leave the import system alone so the failure
        # stays honest instead of turning into a confusing spec error.
        return
    # Ahead of PathFinder: site-packages/numpy holds the Python half and must
    # not be allowed to answer for the compiled half.
    sys.meta_path.insert(0, _StaticExtensionFinder)
    _log("{} static extension modules available".format(len(builtin)))


def unregister():
    try:
        sys.meta_path.remove(_StaticExtensionFinder)
    except ValueError:
        pass
