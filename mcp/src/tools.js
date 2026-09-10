// The tool contract. Single source of truth for both adapters.
//
// An agent must never be able to tell whether it is driving native Blender on a
// server or the wasm build in a browser tab: same tool names, same arguments,
// same result shapes. That is what lets you develop against the server (fast,
// scriptable, testable in CI) and switch to the tab for the human-facing
// viewport without touching the harness.
//
// Each entry maps 1:1 onto an `op` in mcp/blender/mcp_bridge.py. Keep them in
// step: the bridge is the thing that actually validates arguments, so a schema
// that drifts here only produces worse error messages, never wrong behaviour.

import { z } from "zod";

const vec3 = z.array(z.number()).length(3);

/** Tools an agent should reach for constantly: cheap, read-only, no side effects. */
export const READ_ONLY = new Set(["capabilities", "scene_graph", "object_info"]);

/**
 * `run_python` is arbitrary code execution inside the user's Blender session.
 * It is off unless MCP_ALLOW_PYTHON=1, and it is listed here so the decision is
 * visible in one place rather than buried in a handler.
 */
export const GUARDED = new Set(["run_python"]);

export const TOOLS = [
  {
    name: "capabilities",
    title: "Blender capabilities",
    description:
      "What this particular Blender can do: version, target (native/web), render engines, " +
      "available exporters, modifier types, and the list of tools. CALL THIS FIRST and keep " +
      "the answer in context. The web build is trimmed (no Cycles, no OpenVDB, no QuadriFlow, " +
      "and more) while the server build is not, so a script written against the wrong feature " +
      "set is the most common way this fails.",
    schema: {},
  },
  {
    name: "scene_graph",
    title: "Scene graph",
    description:
      "Compact structured state of the scene: every object with its transform, dimensions, " +
      "modifier stack, polygon counts and materials, plus the active and selected objects. " +
      "This is the cheap way to know where you are — prefer it over reading the scene with " +
      "run_python.",
    schema: {
      selected_only: z
        .boolean()
        .optional()
        .describe("Restrict the listing to the current selection."),
    },
  },
  {
    name: "object_info",
    title: "Object detail",
    description:
      "Everything about one object: world matrix, world-space bounding box, full modifier " +
      "parameters, counts. Use after scene_graph when you need the detail it omits.",
    schema: { name: z.string().describe("Object name.") },
  },
  {
    name: "add_primitive",
    title: "Add primitive",
    description:
      "Create a primitive: cube, plane, circle, uv_sphere, ico_sphere, cylinder, cone, torus, " +
      "monkey, empty, camera, light. Returns the created object's record.",
    schema: {
      kind: z.string().describe("Primitive kind, e.g. 'cube'."),
      name: z.string().optional().describe("Name to give the new object."),
      location: vec3.optional(),
      rotation: vec3.optional().describe("Euler XYZ, radians."),
      scale: vec3.optional(),
      params: z
        .record(z.any())
        .optional()
        .describe("Extra arguments for the underlying operator, e.g. {\"radius\": 2}."),
    },
  },
  {
    name: "set_transform",
    title: "Set transform",
    description: "Set location, rotation and/or scale of an existing object. Omitted fields are left alone.",
    schema: {
      name: z.string(),
      location: vec3.optional(),
      rotation: vec3.optional().describe("Euler XYZ, radians."),
      scale: vec3.optional(),
    },
  },
  {
    name: "add_modifier",
    title: "Add modifier",
    description:
      "Add a modifier to an object and set its parameters, e.g. type 'SUBSURF' with " +
      "{\"levels\": 2}. Check `capabilities.modifiers` for what this build supports.",
    schema: {
      object: z.string(),
      type: z.string().describe("Modifier type identifier, e.g. 'SUBSURF', 'ARRAY', 'REMESH'."),
      name: z.string().optional(),
      params: z.record(z.any()).optional(),
    },
  },
  {
    name: "apply_modifier",
    title: "Apply modifier",
    description:
      "Apply a modifier destructively, baking it into the mesh. Defaults to the last one in " +
      "the stack.",
    schema: { object: z.string(), modifier: z.string().optional() },
  },
  {
    name: "duplicate_object",
    title: "Duplicate object",
    description: "Copy an object (and its mesh data) into the current collection.",
    schema: { name: z.string(), new_name: z.string().optional(), location: vec3.optional() },
  },
  {
    name: "delete_object",
    title: "Delete object",
    description: "Remove an object from the file.",
    schema: { name: z.string() },
  },
  {
    name: "render",
    title: "Render to image",
    description:
      "Render the scene to an image and return it inline (base64). Creates a temporary camera " +
      "framing the scene if there is none — this build's startup scene deliberately has no " +
      "camera. Use small resolutions: this is for checking your own work, not for delivery.",
    schema: {
      width: z.number().int().min(64).max(2048).optional(),
      height: z.number().int().min(64).max(2048).optional(),
      format: z
        .enum(["PNG", "JPEG"])
        .optional()
        .describe("Force a container. Left out, PNG is tried first and JPEG is the fallback."),
      keep_camera: z
        .boolean()
        .optional()
        .describe("Keep the camera this call created instead of removing it."),
    },
  },
  {
    name: "export",
    title: "Export geometry",
    description:
      "Export the scene (or just the selection) and return the bytes inline (base64). " +
      "Formats: gltf, fbx, obj, stl, ply — check `capabilities.exporters` first, they differ " +
      "between the server and web builds.",
    schema: {
      format: z.string().optional().describe("gltf (default), fbx, obj, stl, ply."),
      selected_only: z.boolean().optional(),
    },
  },
  {
    name: "run_python",
    title: "Run Python (escape hatch)",
    description:
      "Execute Python inside Blender. `bpy` and `mathutils` are in scope, stdout is captured, " +
      "and assigning to `result` returns a value. This is the fallback for what the named " +
      "tools do not cover: it is unvalidated and unreplayable, so prefer the named tools when " +
      "one fits. Disabled unless the server is started with MCP_ALLOW_PYTHON=1.",
    schema: { code: z.string().describe("Python source to execute.") },
  },
];

export const TOOLS_BY_NAME = new Map(TOOLS.map((t) => [t.name, t]));
