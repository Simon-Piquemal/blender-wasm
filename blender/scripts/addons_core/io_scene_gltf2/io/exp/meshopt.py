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
_FEATURE_NAME = "meshopt compression"

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
import numpy as np

from ...io.com.library import dll_path

# encodeExpMode enum values (from encoder.h)
ENCODE_EXP_SEPARATE = 0
ENCODE_EXP_SHARED_VECTOR = 1
ENCODE_EXP_SHARED_COMPONENT = 2
ENCODE_EXP_CLAMPED = 3

# Precision bits for filters
OCT_FILTER_BITS = 8
EXP_FILTER_BITS = 12
QUAT_FILTER_BITS = 8


class MeshoptEncoder:
    """Meshopt encoder."""
    def __new__(cls, *args, **kwargs):
        raise RuntimeError("%s should not be instantiated" % cls)

    @staticmethod
    def find_library():
        """Find the Meshopt encoder library."""
        path = dll_path('bf_intern_meshopt_bridge', 'MeshOptimizer')
        if path is not None and path.exists() and path.is_file():
            return path
        else:
            raise RuntimeError("Meshopt encoder library not found at {}".format(path))

    @staticmethod
    def load_library(export_settings):
        """Load the Meshopt encoder library."""
        if 'meshopt_encoder' in export_settings.keys():
            return
        lib_path = MeshoptEncoder.find_library()
        try:
            lib = ctypes.CDLL(lib_path.resolve())
        except Exception as e:
            raise RuntimeError("Failed to load Meshopt encoder library: {}".format(e))

        export_settings['meshopt_encoder'] = lib

        lib.encodeIndexVersion.argtypes = [ctypes.c_int]
        lib.encodeIndexVersion.restype = None
        lib.encodeIndexVersion(1)

        lib.encodeVertexVersion.argtypes = [ctypes.c_int]
        lib.encodeVertexVersion.restype = None
        if export_settings['gltf_meshopt_extension'] == 'EXT_meshopt_compression':
            lib.encodeVertexVersion(0)
        elif export_settings['gltf_meshopt_extension'] == 'KHR_meshopt_compression':
            lib.encodeVertexVersion(1)
        else:
            raise RuntimeError("Unsupported meshopt extension: {}".format(export_settings['gltf_meshopt_extension']))

        lib.encodeIndexBufferBound.argtypes = [ctypes.c_size_t, ctypes.c_size_t]
        lib.encodeIndexBufferBound.restype = ctypes.c_size_t

        lib.encodeIndexBuffer.argtypes = [
            ctypes.c_void_p,  # unsigned char* buffer
            ctypes.c_size_t,  # size_t buffer_size
            ctypes.c_void_p,  # const unsigned int* indices
            ctypes.c_size_t,  # size_t index_count
        ]
        lib.encodeIndexBuffer.restype = ctypes.c_size_t

        lib.encodeVertexBufferBound.argtypes = [ctypes.c_size_t, ctypes.c_size_t]
        lib.encodeVertexBufferBound.restype = ctypes.c_size_t

        lib.encodeVertexBuffer.argtypes = [
            ctypes.c_void_p,  # unsigned char* buffer
            ctypes.c_size_t,  # size_t buffer_size
            ctypes.c_void_p,  # const void* vertices
            ctypes.c_size_t,  # size_t vertex_count
            ctypes.c_size_t,  # size_t vertex_size
        ]
        lib.encodeVertexBuffer.restype = ctypes.c_size_t

        lib.encodeIndexSequenceBound.argtypes = [ctypes.c_size_t, ctypes.c_size_t]
        lib.encodeIndexSequenceBound.restype = ctypes.c_size_t

        lib.encodeIndexSequence.argtypes = [
            ctypes.c_void_p,  # unsigned char* buffer
            ctypes.c_size_t,  # size_t buffer_size
            ctypes.c_void_p,  # const unsigned int* indices
            ctypes.c_size_t,  # size_t index_count
        ]
        lib.encodeIndexSequence.restype = ctypes.c_size_t

        # API(void) encodeFilterOct(void* destination, size_t count, size_t stride, int bits, const float* data)
        lib.encodeFilterOct.argtypes = [
            ctypes.c_void_p,  # void* destination
            ctypes.c_size_t,  # size_t count
            ctypes.c_size_t,  # size_t stride
            ctypes.c_int,     # int bits
            ctypes.c_void_p,  # const float* data
        ]
        lib.encodeFilterOct.restype = None

        # API(void) encodeFilterQuat(void* destination, size_t count, size_t stride, int bits, const float* data)
        lib.encodeFilterQuat.argtypes = [
            ctypes.c_void_p,  # void* destination
            ctypes.c_size_t,  # size_t count
            ctypes.c_size_t,  # size_t stride
            ctypes.c_int,     # int bits
            ctypes.c_void_p,  # const float* data
        ]
        lib.encodeFilterQuat.restype = None

        # API(void) encodeFilterExp(void* destination, size_t count, size_t
        # stride, int bits, const float* data, enum encodeExpMode mode)
        lib.encodeFilterExp.argtypes = [
            ctypes.c_void_p,  # void* destination
            ctypes.c_size_t,  # size_t count
            ctypes.c_size_t,  # size_t stride
            ctypes.c_int,     # int bits
            ctypes.c_void_p,  # const float* data
            ctypes.c_int,     # enum encodeExpMode mode
        ]
        lib.encodeFilterExp.restype = None

    @staticmethod
    def encode_indices(mode, data, export_settings):

        if mode not in [4, None]:
            return MeshoptEncoder.encode_index_sequence(data, export_settings)
        else:
            return MeshoptEncoder.encode_index_buffer(data, export_settings)

    @staticmethod
    def encode_index_buffer(data, export_settings):

        filter = None

        MeshoptEncoder.load_library(export_settings)
        lib = export_settings['meshopt_encoder']

        index_count = len(data)
        vertex_count = int(data.max()) + 1

        bound = lib.encodeIndexBufferBound(index_count, vertex_count)
        buffer = (ctypes.c_ubyte * bound)()

        to_be_converted_data = np.ascontiguousarray(data, dtype=np.uint32)

        written = lib.encodeIndexBuffer(
            buffer,
            bound,
            to_be_converted_data.ctypes.data_as(ctypes.c_void_p),
            index_count
        )

        return bytes(buffer[:written]), filter

    @staticmethod
    def encode_index_sequence(data, export_settings):

        filter = None

        MeshoptEncoder.load_library(export_settings)
        lib = export_settings['meshopt_encoder']

        index_count = len(data)
        vertex_count = int(data.max()) + 1

        bound = lib.encodeIndexSequenceBound(index_count, vertex_count)
        buffer = (ctypes.c_ubyte * bound)()

        to_be_converted_data = np.ascontiguousarray(data, dtype=np.uint32)

        written = lib.encodeIndexSequence(
            buffer,
            bound,
            to_be_converted_data.ctypes.data_as(ctypes.c_void_p),
            index_count
        )

        return bytes(buffer[:written]), filter

    @staticmethod
    def encode_attribute(attribute_name, data, byteStride, export_settings):

        filter = None
        if attribute_name in ["POSITION", "SK_POSITION", "NORMAL", "SK_NORMAL", "TANGENT", "SK_TANGENT",
                              "SCALE", "TIME", "GPU_TRANSLATION", "GPU_SCALE", "SK_ANIM"]:
            filter = 'EXPONENTIAL'
        elif attribute_name in ["ROTATION", "SK_ROTATION", "GPU_ROTATION"]:
            filter = 'QUATERNION'
        else:
            pass

        MeshoptEncoder.load_library(export_settings)
        lib = export_settings['meshopt_encoder']

        vertex_count = len(data)
        vertex_size = data.strides[0]

        bound = lib.encodeVertexBufferBound(vertex_count, byteStride if filter is not None else vertex_size)
        buffer = (ctypes.c_ubyte * bound)()

        to_be_converted_data = np.ascontiguousarray(data)

        if filter == 'EXPONENTIAL':
            filtered_data = np.empty(vertex_count * byteStride, dtype=np.uint8)
            lib.encodeFilterExp(
                filtered_data.ctypes.data_as(ctypes.c_void_p),
                vertex_count,
                byteStride,
                EXP_FILTER_BITS,
                to_be_converted_data.ctypes.data_as(ctypes.c_void_p),
                ENCODE_EXP_SHARED_VECTOR
            )
        elif filter == 'QUATERNION':
            filtered_data = np.empty(vertex_count * byteStride, dtype=np.uint8)
            lib.encodeFilterQuat(
                filtered_data.ctypes.data_as(ctypes.c_void_p),
                vertex_count,
                byteStride,
                QUAT_FILTER_BITS,
                to_be_converted_data.ctypes.data_as(ctypes.c_void_p)
            )

        if filter is not None:
            written = lib.encodeVertexBuffer(
                buffer, bound,
                filtered_data.ctypes.data_as(ctypes.c_void_p),
                vertex_count, byteStride)
        else:
            written = lib.encodeVertexBuffer(
                buffer, bound,
                to_be_converted_data.ctypes.data_as(ctypes.c_void_p),
                vertex_count, vertex_size)

        return bytes(buffer[:written]), filter
