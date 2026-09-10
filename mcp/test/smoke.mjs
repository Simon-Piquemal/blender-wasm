// End-to-end smoke test against the native adapter.
//
// Spawns headless Blender through the adapter, exercises every tool, and checks
// the SHAPE of each answer rather than just "no exception" — a bridge that
// returns {} for everything would otherwise pass.
//
//   MCP_BLENDER_BIN="C:/Program Files/Blender Foundation/Blender 5.1/blender.exe" npm run smoke

import { createNativeAdapter } from "../src/adapters/native.js";

const adapter = createNativeAdapter();
let failures = 0;

const check = (label, condition, detail = "") => {
  const ok = Boolean(condition);
  if (!ok) failures++;
  console.log(`${ok ? "  ok  " : "FAIL  "}${label}${detail ? `  ${detail}` : ""}`);
};

const call = async (op, args) => {
  const answer = await adapter.call(op, args);
  if (!answer.ok) {
    failures++;
    console.log(`FAIL  ${op}: ${answer.error}`);
  }
  return answer.result ?? {};
};

try {
  const caps = await call("capabilities");
  check("capabilities", caps.blender && caps.ops?.length >= 10, `blender ${caps.blender}, ${caps.ops?.length} ops`);
  check("capabilities.target", caps.target === "native", `target=${caps.target}`);

  const cube = await call("add_primitive", { kind: "cube", name: "SmokeCube", location: [0, 0, 1] });
  check("add_primitive", cube.name === "SmokeCube" && cube.counts?.verts === 8);

  const moved = await call("set_transform", { name: "SmokeCube", scale: [2, 1, 1] });
  check("set_transform", Math.abs(moved.dimensions[0] - 4) < 1e-4, `dim=${moved.dimensions}`);

  await call("add_modifier", { object: "SmokeCube", type: "SUBSURF", params: { levels: 2 } });
  const info = await call("object_info", { name: "SmokeCube" });
  check("add_modifier + object_info", info.modifiers?.[0]?.params?.levels === 2);
  check("object_info.bounds_world", Array.isArray(info.bounds_world?.min), JSON.stringify(info.bounds_world));

  const dup = await call("duplicate_object", { name: "SmokeCube", new_name: "SmokeCopy", location: [3, 0, 1] });
  check("duplicate_object", dup.name === "SmokeCopy", `name=${dup.name}`);

  await call("apply_modifier", { object: "SmokeCube" });
  const applied = await call("object_info", { name: "SmokeCube" });
  check("apply_modifier", (applied.modifiers?.length ?? 0) === 0 && applied.counts.verts > 8,
    `verts=${applied.counts.verts}`);

  const graph = await call("scene_graph");
  const names = graph.objects.map((o) => o.name);
  check("scene_graph", names.includes("SmokeCube") && names.includes("SmokeCopy"), names.join(","));

  const py = await call("run_python", { code: "result = 2 + 3\nprint('hello')" });
  check("run_python", py.result === 5 && py.stdout.includes("hello"));

  const render = await call("render", { width: 128, height: 128 });
  check("render", render.base64?.length > 1000, `${render.base64?.length} chars`);
  // The bridge picks the container, so it has to say which one it picked --
  // the MCP layer stamps the mime type of the image content from this.
  check("render names its container", render.format === "png" && render.mime === "image/png",
    `${render.format} / ${render.mime}`);
  const jpg = await call("render", { width: 128, height: 128, format: "JPEG" });
  check("render honours a forced container", jpg.mime === "image/jpeg" && jpg.base64?.length > 500,
    `${jpg.format} / ${jpg.base64?.length} chars`);

  const gltf = await call("export", { format: "gltf" });
  check("export gltf", gltf.bytes > 0, `${gltf.bytes} bytes`);

  await call("delete_object", { name: "SmokeCopy" });
  const after = await call("scene_graph");
  check("delete_object", !after.objects.some((o) => o.name === "SmokeCopy"));

  const bad = await adapter.call("object_info", { name: "DoesNotExist" });
  check("error path", bad.ok === false && /no object named/.test(bad.error), bad.error);

  const badOp = await adapter.call("nope", {});
  check("unknown op", badOp.ok === false && /unknown op/.test(badOp.error));
} finally {
  await adapter.close();
}

console.log(failures === 0 ? "\nall checks passed" : `\n${failures} check(s) failed`);
process.exit(failures === 0 ? 0 : 1);
