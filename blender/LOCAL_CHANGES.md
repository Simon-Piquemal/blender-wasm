# Local changes to the vendored Blender tree

`blender/` is a **vendored snapshot**, not a checkout. It is a trimmed copy of
the fork listed in the Makefile (`BLENDER_URL` @ `BLENDER_REF`, recorded in
`.vendored-ref`), committed to this repository so it can be edited in place.

## Why vendored

Cutting features and changing behaviour for the browser build means editing
Blender itself, not only flipping CMake `WITH_*` flags. A pinned shallow clone
made that impossible to keep: every `make blender` re-fetched the fork and any
local edit lived only on one machine.

## What was removed from the upstream snapshot

Done by `scripts/vendor_blender.sh`; both are dead weight for this build:

| Path | Size | Why it is safe |
|---|---|---|
| `locale/` | 80 MB | Translation `.po` files, referenced only inside `if(WITH_INTERNATIONAL)` in `source/creator/CMakeLists.txt`. That option is OFF for wasm. |
| `tests/files/` | 22 MB | Test `.blend` data. No CMake file refers to it. The rest of `tests/` stays because `add_subdirectory(tests)` is unconditional. |

`release/datafiles` git-LFS pointers are resolved in place at vendoring time
(`scripts/fetch_lfs_datafiles.sh`), so the build never needs git-LFS.

## Deviations from the fork

Each is kept as a patch under `scripts/patches/` so it can be re-applied after a
snapshot refresh (`vendor_blender.sh` replaces the tree wholesale).

### `webgpu-ssbo-vertex-pulling.patch` — APPLIED

`source/blender/gpu/webgpu/webgpu_shader_interface.cc`

`WebGPUShaderInterface::init()` never built `ssbo_attr_mask_`. The OpenGL
(`gl_shader_interface.cc`) and Vulkan (`vk_shader_interface.cc`) interfaces both
build it from `info.geometry_resources_`; the WebGPU one was the only backend
that did not — it set the other five masks and missed this one.

`GPU_batch_bind_as_resources()` (`gpu_batch.cc`) early-returns when that mask is
0, so `gpu_index_buf` / `gpu_attr_N` were never bound. An unresolved binding
invalidates the whole bind group, and the WebGPU backend then skips the draw.

Every SSBO vertex-pulled draw was therefore silently dropped: edit-mesh **edges**
(`overlay_edit_mesh_edge` is `STORAGE_BUF_FREQ(..., GEOMETRY)` only, no vertex
buffers), edit-mesh normals and face dots, and every `gpu_shader_3D_polyline_*`
overlay. Edit-mesh **faces** kept drawing because they use ordinary vertex
attributes — which is exactly the "orange cube with no wireframe" symptom.

Observed before the fix:
`WGPU_BG unresolved 'gpu_shader_3D_polyline_flat_color' @2 kind=1 res='gpu_index_buf'`

### `webgpu-hide-global-bars.patch` — APPLIED

`source/blender/editors/screen/screen_edit.cc`

The top bar and the status bar are *global areas*, not ordinary editors.
`screen_global_area_refresh()` builds the top bar with
`size_min == size_max == screen_global_header_size()` — a fixed height with no
collapse path — so unregistering its Python header (see
`scripts/startup/webapp_ui.py`) only empties it: the strip stays and the editors
below cannot grow into it. The status bar is collapsible
(`SCREEN_COLLAPSE_STATUSBAR`, exposed as `Screen.show_statusbar`) but only down
to 1 px.

`ED_screen_global_areas_refresh()` now frees both and returns, giving the main
screen the whole window. This mirrors what the existing child/temporary-window
branch does a few lines above — which is also the evidence that the rest of the
code copes with their absence, since child windows never have global areas.

Escape hatch, no rebuild needed: `BLENDER_WEB_GLOBAL_BARS=1` restores stock
behaviour (the demo forwards `window.__CAPENV` into the environment).

### `webgpu-hide-object-modes.patch` — APPLIED

`source/blender/editors/object/object_edit.cc`

Filters modes out of the 3D view's mode dropdown, `object_mode_set_itemf()`.

Deliberately NOT done by editing `rna_enum_object_mode_items`: the header
resolves the *current* mode with `enum_items[object_mode]`, so removing an entry
from that array would break the header for any object already in that mode, and
for any `.blend` saved in one. Filtering the operator's itemf only changes what
is offered.

An ALLOW-list, not a deny-list: modes added by a later fork bump stay hidden by
default instead of silently appearing. Comma-separated identifiers, default
`OBJECT,EDIT`, overridable without a rebuild via `BLENDER_WEB_MODES` (empty
allows everything). Matching is length-checked, so `EDIT` never matches
`EDIT_GPENCIL`.

## Edited upstream UI files

### `scripts/startup/bl_ui/space_view3d.py`

A `WEBAPP_HEADER` dict at the top of the file switches each block of
`VIEW3D_HT_header.draw()` on or off. Currently off: the View/Select/Add/Object
menus, the transform block (orientation, snapping, proportional editing), the
Selectability & Visibility popover, the Gizmo toggle, the Overlays toggle, and
Toggle X-Ray. The shading mode buttons stay.

This is one of two places the lifting edits an upstream file instead of living
in `webapp_ui.py`, and the reason is structural: these blocks are inline
statements in a 280-line `draw()`. There is no class to unregister and no RNA
flag to clear, so the alternative was copying that whole method into our module
and letting the copy rot out of sync on every fork bump. Guarding in place keeps
the diff to seven `if` lines.

### `scripts/startup/bl_ui/properties_data_modifier.py`

`WEBAPP_MODIFIER_ASSETS = False` at the top of the file. This is what made the
modifiers look broken: the first "Array" entry of the Add Modifier menu is not
the Array modifier at all, it is an *asset* — a geometry node group loaded from
`assets/nodes/geometry_nodes_essentials.blend`. That library is never packaged
(neither `link_blender_web.sh` nor `restage_assets.sh` stages `blender/assets/`,
and `tar -tf assets.tar` has no `datafiles/assets/` at all), so
`get_node_group()` returns null and the operator just reports `CANCELLED`. The
report goes to the status bar, which `webgpu-hide-global-bars.patch` removes —
so the click did nothing at all, silently, and the real modifier sat right below
under the discouraging label "Array (Legacy)".

The same flag drops the permanent "Loading Asset Libraries" line at the bottom
of the menu: `root_catalogs_draw()` gates it on `asset::list::is_loaded()`, which
answers false forever here because nothing in this build ever fetches an asset
list.

With the flag off: the three asset entries ("Array", "Curve to Tube", "Scatter
on Surface") disappear, "Array (Legacy)" gets its real name back, and the menu
ends on Geometry Nodes. Set it to `True` to restore upstream behaviour exactly.

## Edited upstream C/C++ files (outside the patch files)

### `intern/opensubdiv/internal/evaluator/gpu_compute_evaluator.cc`

Dropped `#include <epoxy/gl.h>`. It is vestigial upstream — not one line of the
file calls a GL entry point any more, everything goes through the GPU
abstraction (`GPU_compute`, `GPU_storage_buffer`, shader create infos) — but the
include is unconditional while epoxy is only located when `WITH_OPENGL_BACKEND`
is on. It was the single thing standing between `WITH_OPENSUBDIV=ON` and a
successful compile on this backend. The evaluator itself is backend-agnostic, so
subdivision compute runs on WebGPU.

### `intern/opensubdiv/internal/evaluator/eval_output_gpu.h`

Dropped `<opensubdiv/osd/glPatchTable.h>` and `<opensubdiv/osd/glVertexBuffer.h>`
for the same reason: `GLPatchTable` and `GLVertexBuffer` appear nowhere in the
evaluator outside doc comments ("GLPatchTable or equivalent") — the template
arguments are Blender's own `GPUPatchTable` / `GPUVertexBuffer` wrappers. Those
headers only exist in an OpenSubdiv built with a GPU backend; ours is CPU-only.

Note what stays broken by design: `evaluator_capi.cc` only emits OpenSubdiv's
patch-basis shader source under `WITH_OPENGL_BACKEND` / `WITH_VULKAN_BACKEND`,
so on this backend GPU **subdivision** would run with an empty patch basis.
`webapp_ui.py` therefore forces `preferences.system.use_gpu_subdivision = False`
(`GPU_SUBDIVISION`). CPU subdivision — what the Subsurf modifier actually needs
— is unaffected.

### `draw/engines/overlay/overlay_instance.cc`

Forces `state.is_render_depth_available = false` when the viewport renders with
EEVEE. Upstream keeps it true for EEVEE as an optimisation ("to avoid a
significant overhead"), which tells the overlay it can test against the depth
the engine already produced. On this backend that depth never reaches the
overlay's attachment, so every depth-tested overlay was testing against an empty
buffer -- the grid drew straight through the geometry.

Measured with the backend's own probe, reading the exact buffer the grid tests
against, same pixel, same moment:

| | Solid (workbench) | Material Preview (EEVEE) |
|---|---|---|
| before | `center=0.99947`, 18068 texels < 1 | `center=1.0`, **0 texels < 1** |
| after  | unchanged | `center=0.99947`, 117148 texels < 1 |

Declaring the depth unavailable takes the fallback upstream already has for
engines whose depth cannot be trusted: the overlay clears the buffer and fills
it with its own depth prepass (`object_needs_prepass()`, the `dt >= OB_SOLID`
branch). It costs one depth prepass of the scene per frame.
`BLENDER_WEB_EEVEE_DEPTH=1` restores the upstream assumption.

### `editors/render/render_preview.cc`

**Opening the Material tab froze the whole tab.** Reported as "putting a
material on an object crashes it". It was not a crash: preview icon rendering
was running a full EEVEE render, synchronously, on the main thread.

Under emscripten a WM job runs synchronously (see the `__EMSCRIPTEN__` branch in
`wm_jobs.cc`: worker pthreads cannot touch WebGPU, so a job that renders would
crash on its first GPU call). Preview icons are a job. One material icon
therefore drags an entire EEVEE pipeline through Tint, with the interface dead
until it finishes.

Traced with `BLENDER_WEB_LOG_PREVIEW=1`, which this file now supports:

```
PREVIEW_STAGE other_id_types:enter
PREVIEW_STAGE prepare_scene:enter
PREVIEW_STAGE prepare_scene:done
PREVIEW_STAGE RE_PreviewRender:enter     <- 34 shaders compiled from here
WGPU_FILM inject: no Render for scene    <- and the pixels are dropped anyway
```

| what | measured |
|---|---|
| shaders compiled for one 32x32 icon | 34 |
| Tint translation time alone | 16.5 s |
| interface during that time | frozen |
| thumbnail actually produced | none, the film injection fails |
| first attempt, colder cache | never finished |

So the freeze bought a blank square. Render-based previews are now off in this
build, `BLENDER_WEB_PREVIEW_RENDER=1` puts them back. After the change, the same
gesture leaves Blender answering in **860 ms**, and the Material tab, the
Principled BSDF panel and Material Preview shading all behave normally.

Object, collection and image previews are untouched (solid mode or straight from
an ImBuf, both cheap). Re-enabling for real means seeding the preview scene's
shaders into `wgsl-cache.json` and fixing the film injection.

### `scripts/addons_core/io_scene_gltf2/` — `ctypes` made optional

glTF export failed on its first line with `ModuleNotFoundError: No module named
'_ctypes'`. The add-on imports Draco and meshopt at module scope, and both do
nothing but `dlopen` a native shared library through `ctypes` -- a library that
cannot exist for WebAssembly, for a feature that therefore cannot work here.
One unconditional import was taking the whole exporter down with it.

The import is now optional in the four leaf modules (`io/exp/draco.py`,
`io/exp/meshopt.py`, `io/imp/gltf2_io_binary_meshopt.py`,
`blender/imp/draco_compression_extension.py`) and the failure is deferred to the
entry points that genuinely need it, with a message that says so.

This is a Python-only change: `bash scripts/restage_assets.sh` and reload, no
wasm rebuild.

**glTF and FBX are still unavailable**, one step further along: both exporters
`import numpy`, and the wasm CPython has no numpy. See "Known, still unfixed".

### `imbuf/intern/oiio/openimageio_support.cc`

**Saving a PNG was impossible in this build.** Every attempt died with
`libpng error: tEXt: invalid keyword` followed by `No IDATs written into file`,
so nothing was ever written: F12 renders, image export, the MCP `render` tool.
Blender reported it as `Could not set PNG pHYs chunk`, which is a red herring --
that string comes from the most recently armed `setjmp` in OIIO's
`write_png_header`, and libpng's `png_error` longjmps back to it from much later
in `png_write_info`.

The trigger is the resolution metadata. `imb_create_write_spec` attaches
`ResolutionUnit`/`XResolution`/`YResolution` for every OIIO format;
`put_parameter` in OIIO's `png_pvt.h` is supposed to consume all three before
its generic "any leftover string attribute becomes a tEXt chunk" branch, and on
native builds it does. Here it does not, and the tEXt that reaches libpng has an
empty keyword, which `png_check_keyword` rejects.

Measured, with `IMB_DUMP_ATTRIBS=1` (an `__EMSCRIPTEN__`-only dump in this same
file) and a blank 8x8 image driven from the browser console:

| attribute in the failing spec | type | fate |
|---|---|---|
| `ResolutionUnit` | string | the only string present |
| `XResolution` / `YResolution` | float | never eligible for tEXt |
| `oiio:UnassociatedAlpha` | int | skipped by prefix |
| `png:compressionLevel` | int | never eligible for tEXt |

| image state | `image.save()` to PNG |
|---|---|
| default 72 DPI | fails, no file |
| after `image.resolution = (0, 0)` | 189 bytes, valid |

JPEG, BMP, TARGA and OpenEXR were unaffected throughout, which is why the
failure looked like "renders are broken" rather than "PNG is broken".

The fix skips those three attributes for PNG under `__EMSCRIPTEN__` only. It
costs the pHYs chunk, i.e. the DPI hint, and nothing else. It does **not** claim
to fix OIIO: which half misbehaves -- the case-insensitive name compare that
should have matched `ResolutionUnit`, or the `ustring` backing the keyword --
was not isolated, and doing so means rebuilding OIIO rather than Blender.

### `intern/ghost/intern/GHOST_SystemWeb.cc`

`handleResize()` also calls `emscripten_set_canvas_element_size()`. Under
`PROXY_TO_PTHREAD` the canvas is an OffscreenCanvas owned by the render thread,
so the main thread's `canvas.width = ...` never reaches it: Blender resized its
framebuffers while the drawing surface kept its old size, and the viewport
drifted out of registration with the mouse.

## UI lifting

`scripts/startup/webapp_ui.py` is an additive startup module — no upstream file
is edited, and deleting it reverts the whole lifting. It ships in
`assets.tar.zst`, so changes need no wasm rebuild: run
`bash scripts/restage_assets.sh` and reload.

Currently: top bar emptied, status bar collapsed, timeline area closed, sculpt
menus and panels unregistered, splash screen suppressed, and the viewport look
(white axis lines, darker background, fainter grid, `clip_end` shortened so the
grid fades at 200 BU instead of 1000). Toggles are at the top of the file.

`scripts/startup/webapp_floor.py` is a second additive module: it makes the grid
plane behave like solid ground. Objects are lifted so their world-space bounding
box never crosses Z=0, from a `depsgraph_update_post` handler — so the clamp
holds *during* a drag, not just after it. Not a rigid body on purpose: physics
would only resolve while the timeline plays and would need a bake to survive a
reload. Deleting the file restores stock behaviour.

What Python cannot do, and why:

| Wanted | Blocker |
|---|---|
| Remove the top-bar strip | Fixed-height global area — needs `webgpu-hide-global-bars.patch` |
| Drop "Sculpt Mode" from the mode dropdown | Operator enum built in C — needs `webgpu-hide-object-modes.patch` |

The Properties tabs, by contrast, needed no patch at all: Blender 5.x already
masks them with `SpaceProperties.visible_tabs`, every bit of which is a writable
RNA boolean (`show_properties_*`).

Until the global-bars patch ships, `BLEND_TOPBAR_INTO_HEADER` repaints the strip
to match the 3D view header so it reads as chrome rather than a black band. It
still occupies its fixed height — this is a cosmetic stopgap, not the fix.

## WebGPU backend debug probes

Added to `source/blender/gpu/webgpu/`. All are read with `getenv` and inert
unless set, so the shipped build behaves exactly as before. They are injected
from JS with `window.__CAPENV = { ... }` before boot (`demo/src/main.js` copies
that object into `ENV`), which matters because the renderer runs on a pthread
where a normal env var would not reach it.

| Variable | Effect |
|---|---|
| `WGPU_DEPTH_STATS=<sub>,<sub>` | Before each matching draw, copies the target's depth aspect to a MapRead buffer and prints min/max, the count of texels < 1, the centre value and a 6×10 map of per-cell minima. 6 hits per token. |
| `WGPU_DEPTH_STATS_AFTER=<sub>` | Same, but *after* the draw — the pair tells "never written" apart from "written wrong". |
| `WGPU_DEPTH_WATCH=1` | Re-probes the texture captured by `_AFTER` at every render-pass begin, so a value can be followed across the whole frame. |
| `WGPU_DEPTH_STATS_NOFLUSH=1` | Keeps the probe's copy inside the batched encoder instead of submitting it, so the probe observes the real submission order instead of perturbing it. |
| `WGPU_FLUSH_AFTER=<sub>` | Submits right after a matching draw. Isolates batching/ordering bugs from state bugs. |
| `WGPU_LOG_RP=1` | Lifts the `&`-name gate on render-pass logging and adds `WGPU_SUBMIT #n` per queue submit. `WGPU_RP` lines carry the depth *view* pointer, which is how you tell two framebuffers sharing one depth texture apart. |

Reading depth back is the point of the first four: `read_color_sync` refuses
depth formats, so before this there was no way to see a depth value without a
rebuild. The callback runs on the render thread and prints to stderr, which the
demo forwards to the console as `[err]`.

## numpy, statically linked

glTF and FBX are Python add-ons that `import numpy`, and this CPython had no
numpy, so both died on their first line. That took the format the web actually
uses -- and the one an agent harness reaches for first -- off the table
entirely. It is now built and linked in. `scripts/build_numpy.sh` cross-compiles
it, `scripts/package_numpy.sh` turns the result into something a static
interpreter can load, and both are documented in place. The parts worth knowing:

- **There is no wheel that fits.** Pyodide's are SIDE_MODULEs needing a
  MAIN_MODULE host; this Blender links CPython statically with no loader at all.
- **meson needed three separate corrections** to cross-compile: numpy's own
  patched meson under `vendored-meson/` (upstream has no `features` module for
  CPU dispatch), `_PYTHON_SYSCONFIGDATA_NAME` so the build interpreter reports
  the target's configuration instead of x86_64's, and `IEEE_QUAD_LE` because
  `sizeof(long double)` is 16 under emcc, not 8.
- **The objects had to be recompiled without `-fPIC`.** `py.extension_module()`
  is a shared module by construction, and PIC objects in a static link fail with
  `relocation R_WASM_TABLE_INDEX_REL_SLEB is not supported`.
  `scripts/recompile_numpy_nopic.py` replays each compile from
  `compile_commands.json` with that flag dropped.
- **`-sERROR_ON_UNDEFINED_SYMBOLS=0` hides mistakes here.** A first attempt
  linked cleanly and then aborted at runtime with
  `Aborted(missing function: DOUBLE_log)`: numpy keeps its CPU-dispatch loops in
  separate `lib*.dispatch.h_baseline.a.p/` archives that the object sweep was
  not collecting. A clean link is not evidence; check for stubbed symbols.
- **Dotted builtins need help on the Python side.**
  `BuiltinImporter.find_spec` returns None the moment it is passed a package
  path, so the inittab entries for `numpy._core._multiarray_umath` and friends
  would never have been consulted. `scripts/startup/webapp_numpy.py` installs a
  meta path finder that closes exactly that gap.

Two small bugs surfaced behind it, both fixed:

- `io_scene_gltf2/io/com/library.py` dereferenced the `None` that `dll_path()`
  returns on an unrecognised platform, so glTF export failed with
  `AttributeError: 'NoneType' object has no attribute 'absolute'` while trying
  to report that Draco was unavailable.
- `time.process_time()` raises under emscripten (no per-process CPU clock) and
  the FBX exporter calls it to time its own passes.
  `scripts/startup/webapp_pycompat.py` substitutes a monotonic wall clock.

| cost | measured |
|---|---|
| `blender.wasm.zst` | +1.16 MB |
| `assets.tar.zst` | +0.67 MB, after trimming stubs/headers/f2py/tests |
| first `import numpy` | ~10 s, once per session |

Verified in the browser: numpy 2.2.6 arithmetic, then export in all five formats
(glTF 71 KB, FBX 37 KB, OBJ, STL, PLY), then a glTF round trip that reimported
its own output.

### `gpu/webgpu/webgpu_context.cc` — UI framebuffer capture

The canvas is an OffscreenCanvas owned by the render thread, so the page cannot
read a single pixel of it: no `getImageData`, no `toDataURL`. Bugs in the 3D
viewport can be inspected through the render readback; bugs in the INTERFACE had
no path at all, which is why "a label sometimes disappears after a resize" could
only ever be confirmed by a human looking at a screenshot.

`wgpu_ui_capture_request()` copies the composited backbuffer into a tight RGBA8
buffer in the wasm heap; `wgpu_ui_capture_ready()`, `_w()`, `_h()` and `_ptr()`
read it back. One frame per request, nothing captured unless asked. From JS:

```js
Module._wgpu_ui_capture_request();
// wait a few frames
const w = Module._wgpu_ui_capture_w(), h = Module._wgpu_ui_capture_h();
const px = Module.HEAPU8.subarray(Module._wgpu_ui_capture_ptr(), ... + w * h * 4);
```

Two things to know about the pixels. The buffer follows this backend's bottom-up
row convention, so screen row `R` is buffer row `h - 1 - R`. And the request flag
is written from the page's main thread while `present_backbuffer` reads it on the
render thread, so it is `volatile` -- without that the optimiser hoists the check
and the request is never seen, which is exactly what happened first.

Verified: a 1180x760 capture of the running interface, and a measurable change in
the image after switching the Properties tab.

### `intern/ghost/intern/GHOST_SystemWeb.hh` — front-buffer read capability

**`bpy.ops.screen.screenshot()` killed the tab**: `memory access out of bounds`,
no Blender error, no stack, the whole page gone.

`GHOST_SystemWeb::getCapabilities()` returned `GHOST_CAPABILITY_FLAG_ALL` minus
a list of exclusions, and `GHOST_kCapabilityGPUReadFrontBuffer` was not on that
list. There is no front buffer here: the canvas is presented by copying our own
backbuffer into a surface texture, and nothing keeps the presented image. So
`WM_window_pixels_read()` took the front-buffer branch into a read that cannot
work, and the module trapped.

It reached two features, not one, because both read the window the same way:
Save Screenshot, and the **colour picker**, through
`WM_window_pixels_read_sample()`.

Dropping the flag routes both to the offscreen path, which redraws the window
into a texture it owns. That stops the crash — and then produced a 1180x760 PNG
of pure black, because the offscreen read has the same underlying problem: **no
synchronous GPU readback exists in this build.** A texture is read by copying it
into a buffer and mapping that buffer, the map completes on the browser event
loop, and the event loop cannot run while wasm is on the stack (no JSPI, which
is incompatible with `-fexceptions`). The render pipeline works around it by
deferring a frame; an operator that needs pixels *now* cannot.

So `WM_window_pixels_read_from_offscreen()` and its single-sample sibling now
return failure on this platform, with one line saying why. The screenshot
operator reports `CANCELLED` and writes nothing, which is the truthful answer,
instead of a black file that looks like it worked.

| `bpy.ops.screen.screenshot()` | outcome |
|---|---|
| before | tab killed, `memory access out of bounds` |
| after dropping the capability | 11 947-byte PNG, entirely black |
| now | `CANCELLED`, no file, reason logged, Blender alive |

For a picture of the interface, use the backbuffer capture described above: it
is asynchronous, which is exactly why it works.

## Known, still unfixed

### `scripts/startup/webapp_resize.py` — repaint once a resize settles

**A mitigation, deliberately, and labelled as one.** The defect below is real and
its cause is not found. What IS established, in every campaign run against it, is
the repair: a full redraw restores the missing text, without fail. That is also
what the original report described -- hovering the button brought the label back,
because hovering redraws that widget.

So this waits for the window size to stop changing, then asks for one full
redraw. One per gesture, not one per resize event: a drag emits dozens of events
and repainting on each would be the expensive, useless version of it.

Measured as an A/B in the same build, counting lit pixels in the properties
column after a resize storm:

| | spread across runs | panel content |
|---|---|---|
| `BLENDER_WEB_NO_RESIZE_REPAIR=1` | 120 px over 6 runs | 3439-3559 |
| repair on (default) | **27 px over 8 runs** | 3899-3926 |

A missing label is worth 253 pixels, so a 27-pixel spread is antialiasing, not a
lost text run -- and the ~400 pixels of extra content is what the repair puts
back. Cost is one redraw at the end of a resize gesture.

This does not close the investigation below. It stops the user seeing it.

**A label sometimes fails to draw after a window resize.** Reproducible on
demand now, and measured rather than eyeballed.

A detector exists: the UI framebuffer capture, plus resizes driven from the page
(`handleResize()` reads the canvas CSS size, so setting `canvas.style.width` and
dispatching a `resize` event replays the real path). The measurement that works
is a **self-comparison**: capture the frame the resize produced, then force a
full redraw at the same size and capture again, and count pixels that are lit in
the second and dark in the first. Absolute counts are useless -- they drift as
the layout shifts by a pixel -- but the difference between two frames of the
same layout is exact.

Two traps that cost a lot of time before that worked, both worth knowing:

- The capture only completes on a frame that is actually **presented**, and
  Blender idles when nothing changes, so a request with no redraw behind it
  never returns. And a capture taken mid-resize catches the backbuffer just
  after reallocation, which is empty; those frames must be discarded.
- The **3D viewport is not a stable reference**. EEVEE accumulates temporally,
  so a resize resets the accumulation and the viewport legitimately differs
  between two frames. Counting the whole window makes everything look broken.
  The metric has to be restricted to the interface, and the viewport delta
  reported separately as a control.

With that, the signature is precise:

| | |
|---|---|
| baseline, no resize, 8 pairs | 0 differing pixels, every time |
| resize storms, panel-only failures | 3 to 4 out of every 6-10 |
| what is missing | 253 pixels, one **69x8 text run** in the properties panel |
| 3D viewport delta on those runs | 0 |

One line of text, painted by the repair frame and absent from the resize frame.
That is exactly the reported symptom, now with coordinates.

Hypotheses eliminated with data, not reasoning. Every one of these was tried,
measured against the detector, and reverted:

| suspect | how it was tested | result |
|---|---|---|
| CPU-side text clipping | `BLENDER_WEB_LOG_CLIP` | never fires |
| malformed text batches | `BLENDER_WEB_LOG_BLF`, 656 batches | none empty, none without an atlas |
| bind-group cache aliasing | `WGPU_BG_CACHE_OFF=1` | fails identically, 8/9 |
| icon preview render interleaving | `BLENDER_WEB_PREVIEW_RENDER=1` vs off | fails either way |
| atlas upload ordering vs the draw | `GPU_flush()` after the upload | 3/5 either way |
| text queued past the region's target | `BLF_batch_draw_flush()` at region unbind | 5/5 with, 3/5 without |
| first draw at a new size not settled | replay the size event 3 more times | 4/7, unchanged |

The single most informative measurement is a control, not a fix. Redraw ONLY the
3D view after the resize, so the properties region is not re-rendered but merely
re-composited from the offscreen it already holds:

| what is redrawn after the resize | missing pixels reappear? |
|---|---|
| everything | yes, all 253 |
| the 3D view alone | no, 0 across 5 runs |

So the region's own offscreen genuinely does not contain those pixels. This is
not a draw that landed somewhere else, and not a compositing problem: on the
frame the resize produced, that text was never painted into the region's
texture. The next time the region draws, it is. That moves the search off the
GPU backend and onto what the interface decides to draw, which is where the next
attempt should start -- and it is consistent with the original report, where
hovering the button (which redraws that one widget) brings the label back.

The chain is now bracketed end to end, with counters rather than log lines.

Log lines produced a confidently wrong reading here **twice**: the page console
drops them under load, and a dropped line reads exactly like "this never
happened". `interface_widgets.cc` therefore exports counters --
`blender_web_uitext_count()`, `blender_web_clip_lost()`,
`blender_web_clip_shrunk()`, `blender_web_clip_okwidth()` -- selected by
`BLENDER_WEB_LOG_TEXT=<substring>`. Read the counters. Do not trust the log.

What they say, measured across resize storms on the frame that loses the label:

| step | measured | verdict |
|---|---|---|
| `widget_draw_text` called for the label | 1-3 times, same as passing frames | asked for |
| clipping empties it | 0, always | not clipped |
| clipping shortens it | 0, always | not clipped |
| width available to it | 123-139 px, ample | not squeezed |
| BLF batch carrying it | non-empty, atlas bound | queued and issued |
| the widget's background | painted | the draw ran |
| the widget's text | absent | lost |

And rendered as characters, the failing region is an evenly lit button with
nothing on it, against a repaired frame that reads "Add Modifier".

So: the interface asks, the string survives clipping, the glyphs are queued, the
draw is issued, the background lands, and the text does not.

The per-draw instrumentation that question demanded now exists.
`webgpu_batch.cc` counts, for the text shader only, how many draws
`record_draw()` is handed and how many it actually records, plus one counter per
place it can give up silently -- no pipeline, no render pass, no bind group, no
vertices. Read them with `blender_web_text_draw_issued()`,
`blender_web_text_draw_recorded()`, `blender_web_text_drop_nopass()` and
friends.

The answer is unambiguous, and it closes the most attractive remaining theory:

| across resize storms that DO reproduce the defect | count |
|---|---|
| text draws issued | ~3 500 |
| text draws recorded into a render pass | ~3 500 |
| dropped for want of a render pass | **0** |
| dropped for want of a bind group, pipeline or vertices | **0** |

Not one text draw is lost. Every one is recorded. The pixels still do not
arrive, so the loss is after recording: either the pass those draws went into is
not the one that ends up on screen, or it is overwritten before it gets there.

Two more theories died the same way, both worth naming so nobody spends the
afternoon on them again:

- **Stale attachment views.** A region offscreen recreated on resize would leave
  `WebGPUTexture::attachment_views_` pointing at the old texture, and the text
  would land in something nobody composites. It cannot happen: `texture_` is
  only ever assigned at construction, so a resized target is a new object with
  an empty cache.
- **A render pass re-begun with Clear**, wiping what was already drawn into the
  region. `webgpu_framebuffer.cc` counts begins by load op
  (`blender_web_rp_clear()` / `blender_web_rp_load()`). Across storms the Clear
  share sits at 16-21 % whether the frame kept its label or lost it -- 16 % on
  the worst frame measured, 19 % on the best. No correlation.

What DOES respond is submit ordering. `WGPU_FLUSH_AFTER=text` submits
immediately after every matching draw instead of letting the encoder batch them;
with it, and the settle repair disabled so the defect is visible, the panel comes
back about 230 pixels fuller -- roughly one label -- though the run-to-run spread
stays. **The defect is sensitive to when the encoder is submitted**, which is a
property of the batching in the WebGPU backend, not of Blender's interface code.

That is the thread to pull, and pulling it means tracing an individual recorded
draw to the submit that carries it: which encoder, which pass, which submit, and
whether that submit is the one whose results reach the screen. Everything short
of that has been tried; the table above is the list, and every entry in it was
built, measured, and reverted rather than left in.


**`bpy.ops.screen.screenshot()` crashes the tab** with `memory access out of
bounds`. It draws the whole window into an offscreen buffer and reads it back, a
path this backend does not support. Not reachable by clicking -- the global top
bar is hidden in this build, so there is no Window menu to reach it from -- but
an agent driving Blender through `run_python` can hit it. Use the MCP `render`
tool, which goes through the working readback.



**The floor grid used to ghost through the model in Material Preview** -- fixed,
see `overlay_instance.cc` above. Kept here because the measurements are what
made it findable and they cost several rebuilds:

- The ghost was drawn by the grid pass itself (hiding the floor removed it), and
  it reproduced in orthographic, where the grid's multi-iteration depth bias is
  not applied at all -- so the bias was never the cause.
- The push-constant emulation is sound: the bind-group cache keys on the arena
  slice offset, so `grid_iter` is versioned per draw.
- An earlier reading of "EEVEE writes the depth 3e-5 too far" was **wrong**: the
  reference it was compared against, `outline_depth_tx`, carries the outline
  prepass's deliberate `gl_Position.z -= 1e-3`
  (`overlay_outline_prepass_vert.glsl:37`), which is exactly `1e-3/(2*17)` of
  screen depth at this distance.
- What settled it was probing the exact buffer the grid tests against, at the
  same pixel and the same moment, in both shading modes: Solid had the cube
  (`center=0.99947`, 18068 texels < 1), Material Preview had **nothing**
  (`center=1.0`, 0 texels < 1).

**Edit-mode vertices render as 1-pixel points.** WebGPU points are always 1 px,
so the backend emulates point sprites by expanding each point into an instanced
quad — but the gate in `webgpu_batch.cc` requires `elem_() == nullptr`, i.e. a
non-indexed batch. Measured with the backend's own probe (`WGPU_LOG_POINTS=1`):

```
WGPU_PTS 'overlay_extra_point'    expand=1 can=1 inst=1 elem=0   <- object mode, visible
WGPU_PTS 'overlay_edit_mesh_vert' expand=0 can=1 inst=1 elem=1   <- edit mode, invisible
```

`can=1` means the shader supports expansion; `elem=1` (indexed) is the only
reason it is disabled. Fixing it means letting the expansion path read the index
buffer — either resolving indices into a scratch buffer, or binding the index
buffer as an SSBO and indexing inside the generated wrapper.
