# SPDX-FileCopyrightText: 2026 The glTF-Blender-IO authors
#
# SPDX-License-Identifier: Apache-2.0

# --- WebAssembly build note -------------------------------------------------
# ctypes is unavailable in this Python (no _ctypes extension module), and the
# only thing this module does with it is dlopen a NATIVE shared library that
# has no WebAssembly equivalent. Importing it unconditionally took the whole
# glTF add-on down with it: `bpy.ops.export_scene.gltf()` died at import time
# with ModuleNotFoundError: No module named '_ctypes', so glTF export -- the
# format that matters most on the web -- was entirely unavailable.
#
# So the import is optional and the failure is deferred to the one place that
# genuinely needs it. Plain glTF/GLB export and import work; only Draco and
# meshopt compression are out of reach, which they were regardless.
# ----------------------------------------------------------------------------
_FEATURE_NAME = "meshopt decompression"

try:
    import ctypes
except ImportError:
    ctypes = None


class _MissingCtypes:
    """Stands in for ctypes so importing this module cannot fail."""

    def __getattr__(self, name):
        raise RuntimeError(
            "{} needs the ctypes module, which this Python build does not have. "
            "It loads a native shared library, so it cannot work in WebAssembly "
            "either way.".format(_FEATURE_NAME)
        )


if ctypes is None:
    ctypes = _MissingCtypes()
import sys
import os
from .gltf2_io_gltf import ImportError
from ..com.library import dll_path


class MeshoptDecoder:
    """Meshopt decoder."""
    def __new__(cls, *args, **kwargs):
        raise RuntimeError("%s should not be instantiated" % cls)

    @staticmethod
    def find_library():
        """Find the Meshopt decoder library."""
        path = dll_path('bf_intern_meshopt_bridge', 'MeshOptimizer')
        if path is not None and path.exists() and path.is_file():
            return path
        else:
            raise ImportError("Meshopt decoder library not found at {}".format(path))

    @staticmethod
    def load_library(gltf):
        """Load the Meshopt decoder library."""
        if hasattr(gltf, 'meshopt_decoder'):
            return
        lib_path = MeshoptDecoder.find_library()
        try:
            lib = ctypes.CDLL(lib_path.resolve())
        except Exception as e:
            raise ImportError("Failed to load Meshopt decoder library: {}".format(e))

        gltf.meshopt_decoder = lib

        # Define type signatures for the decoder functions
        decode_funcs = [
            lib.decodeVertexBuffer,
            lib.decodeIndexBuffer,
            lib.decodeIndexSequence,
        ]
        for func in decode_funcs:
            func.restype = ctypes.c_int
            func.argtypes = [
                ctypes.c_void_p,  # void* destination
                ctypes.c_size_t,  # size_t count
                ctypes.c_size_t,  # size_t stride
                ctypes.POINTER(ctypes.c_ubyte),  # const unsigned char* buffer
                ctypes.c_size_t,  # size_t buffer_size
            ]

        filter_funcs = [
            lib.decodeFilterOct,
            lib.decodeFilterQuat,
            lib.decodeFilterExp,
        ]
        for func in filter_funcs:
            func.restype = None
            func.argtypes = [
                ctypes.c_void_p,  # void* buffer
                ctypes.c_size_t,  # size_t count
                ctypes.c_size_t,  # size_t stride
            ]

    @staticmethod
    def get_buffer_view(gltf, bufferview_index):
        """Decodes EXT/KHR_meshopt_compression buffer view."""
        # Check if already in cache
        if not hasattr(gltf, 'meshopt_cache'):
            gltf.meshopt_cache = {}
        if bufferview_index in gltf.meshopt_cache:
            return gltf.meshopt_cache[bufferview_index]

        bufview = gltf.data.buffer_views[bufferview_index]
        # TODO: make sure we detect before that we have EXT or KHR
        if 'EXT_meshopt_compression' in bufview.extensions:
            ext = bufview.extensions['EXT_meshopt_compression']
        else:
            ext = bufview.extensions['KHR_meshopt_compression']

        buffer_idx = ext['buffer']
        byte_length = ext['byteLength']
        byte_offset = ext.get('byteOffset', 0)
        byte_stride = ext['byteStride']
        count = ext['count']
        mode = ext['mode']
        filter = ext.get('filter', None)

        MeshoptDecoder.load_library(gltf)
        lib = gltf.meshopt_decoder

        # load buffer
        if buffer_idx not in gltf.buffers:
            gltf.load_buffer(buffer_idx)

        buffer = gltf.buffers[buffer_idx]
        buffer = buffer[byte_offset:byte_offset + byte_length]

        # Create output buffer
        output = memoryview(bytearray(count * byte_stride))
        dst_ptr = (ctypes.c_ubyte * len(output)).from_buffer(output)
        # TODO: this creates a copy of the buffer, we should be able to decode in place without copy
        buffer_ptr = (ctypes.c_ubyte * len(buffer)).from_buffer_copy(buffer)

        decode_func = {
            'ATTRIBUTES': lib.decodeVertexBuffer,
            'TRIANGLES': lib.decodeIndexBuffer,
            'INDICES': lib.decodeIndexSequence,
        }.get(mode)

        error_code = decode_func(
            dst_ptr,
            count,
            byte_stride,
            buffer_ptr,
            len(buffer),
        )
        if error_code != 0:
            raise ImportError("Meshopt decoding failed with error code {}".format(error_code))

        if mode == 'ATTRIBUTES' and filter is not None:
            filter_func = {
                'OCTAHEDRAL': lib.decodeFilterOct,
                'QUATERNION': lib.decodeFilterQuat,
                'EXPONENTIAL': lib.decodeFilterExp,
                'COLOR': lib.decodeFilterExp,
            }.get(filter)
            filter_func(dst_ptr, count, byte_stride)

        # Cache the decoded buffer view
        gltf.meshopt_cache[bufferview_index] = output
        return output
