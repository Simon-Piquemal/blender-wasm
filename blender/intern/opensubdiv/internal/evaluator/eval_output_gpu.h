/* SPDX-FileCopyrightText: 2021 Blender Foundation
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 *
 * Author: Sergey Sharybin. */

#ifndef OPENSUBDIV_EVAL_OUTPUT_GPU_H_
#define OPENSUBDIV_EVAL_OUTPUT_GPU_H_

#include "internal/evaluator/eval_output.h"
#include "internal/evaluator/gpu_compute_evaluator.h"
#include "internal/evaluator/gpu_patch_table.hh"

/* WEB BUILD -- see blender/LOCAL_CHANGES.md. Same story as the <epoxy/gl.h> in
 * gpu_compute_evaluator.cc: these two OpenSubdiv GL headers are vestigial here.
 * `GLPatchTable` and `GLVertexBuffer` appear nowhere in this evaluator except
 * inside doc comments ("GLPatchTable or equivalent") -- the template arguments
 * are Blender's own GPUPatchTable / GPUVertexBuffer wrappers. They only exist
 * in an OpenSubdiv built with a GPU backend, and ours is CPU-only because a
 * wasm sysroot has no GL, so including them broke the build for nothing. */

#include "gpu_vertex_buffer_wrapper.hh"

namespace blender::opensubdiv {

class GpuEvalOutput : public VolatileEvalOutput<GPUVertexBuffer,
                                                GPUVertexBuffer,
                                                GPUStencilTableSSBO,
                                                GPUPatchTable,
                                                GPUComputeEvaluator> {
 public:
  GpuEvalOutput(const StencilTable *vertex_stencils,
                const StencilTable *varying_stencils,
                const std::vector<const StencilTable *> &all_face_varying_stencils,
                const int face_varying_width,
                const PatchTable *patch_table,
                EvaluatorCache *evaluator_cache = nullptr);

  gpu::StorageBuf *create_patch_arrays_buf() override;

  gpu::StorageBuf *get_patch_index_buf() override
  {
    return getPatchTable()->GetPatchIndexBuffer();
  }

  gpu::StorageBuf *get_patch_param_buf() override
  {
    return getPatchTable()->GetPatchParamBuffer();
  }

  gpu::VertBuf *get_source_buf() override
  {
    return getSrcBuffer()->get_vertex_buffer();
  }

  gpu::VertBuf *get_source_data_buf() override
  {
    return getSrcVertexDataBuffer()->get_vertex_buffer();
  }

  gpu::StorageBuf *create_face_varying_patch_array_buf(const int face_varying_channel) override;

  gpu::StorageBuf *get_face_varying_patch_index_buf(const int face_varying_channel) override
  {
    GPUPatchTable *patch_table = getFVarPatchTable(face_varying_channel);
    return patch_table->GetFVarPatchIndexBuffer(face_varying_channel);
  }

  gpu::StorageBuf *get_face_varying_patch_param_buf(const int face_varying_channel) override
  {
    GPUPatchTable *patch_table = getFVarPatchTable(face_varying_channel);
    return patch_table->GetFVarPatchParamBuffer(face_varying_channel);
  }

  gpu::VertBuf *get_face_varying_source_buf(const int face_varying_channel) override
  {
    GPUVertexBuffer *vertex_buffer = getFVarSrcBuffer(face_varying_channel);
    return vertex_buffer->get_vertex_buffer();
  }
};

}  // namespace blender::opensubdiv

#endif  // OPENSUBDIV_EVAL_OUTPUT_GPU_H_
