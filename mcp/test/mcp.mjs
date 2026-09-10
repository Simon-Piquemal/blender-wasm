// Talks to src/server.js as a real MCP client over stdio.
//
// The smoke test proves the adapter; this proves the MCP surface an agent
// actually sees: tool discovery, schemas, and the content types coming back
// (an image for `render`, not a base64 blob).

import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SERVER = path.resolve(HERE, "../src/server.js");

let failures = 0;
const check = (label, condition, detail = "") => {
  if (!condition) failures++;
  console.log(`${condition ? "  ok  " : "FAIL  "}${label}${detail ? `  ${detail}` : ""}`);
};

const transport = new StdioClientTransport({
  command: process.execPath,
  args: [SERVER],
  env: { ...process.env, MCP_TARGET: "native" },
  stderr: "inherit",
});

const client = new Client({ name: "smoke-client", version: "0.1.0" });
await client.connect(transport);

try {
  const { tools } = await client.listTools();
  const names = tools.map((t) => t.name).sort();
  check("tool discovery", tools.length >= 11, names.join(","));
  check("run_python hidden by default", !names.includes("run_python"),
    "MCP_ALLOW_PYTHON is not set");
  check("schemas present", tools.every((t) => t.inputSchema && t.inputSchema.type === "object"));

  const caps = await client.callTool({ name: "capabilities", arguments: {} });
  const capsText = caps.content.find((c) => c.type === "text")?.text ?? "";
  check("capabilities", capsText.includes("blender"), capsText.slice(0, 60).replace(/\s+/g, " "));

  await client.callTool({ name: "add_primitive", arguments: { kind: "monkey", name: "Suzanne_MCP" } });
  const graph = await client.callTool({ name: "scene_graph", arguments: {} });
  check("add_primitive via MCP", graph.content[0].text.includes("Suzanne_MCP"));

  const render = await client.callTool({ name: "render", arguments: { width: 128, height: 128 } });
  const image = render.content.find((c) => c.type === "image");
  check("render returns MCP image content", image?.mimeType === "image/png" && image.data.length > 1000,
    `${image?.data?.length ?? 0} chars`);

  const exported = await client.callTool({ name: "export", arguments: { format: "stl" } });
  check("export withholds bytes by default", /Base64 withheld/.test(exported.content[0].text),
    exported.content[0].text.slice(0, 70));

  const bad = await client.callTool({ name: "object_info", arguments: { name: "Ghost" } });
  check("errors surface as isError", bad.isError === true, bad.content[0].text.split("\n")[0]);
} finally {
  await client.close();
}

console.log(failures === 0 ? "\nMCP surface ok" : `\n${failures} check(s) failed`);
process.exit(failures === 0 ? 0 : 1);
