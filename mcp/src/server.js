#!/usr/bin/env node
// MCP server: one Blender tool contract, two interchangeable adapters.
//
//   MCP_TARGET=native   drive Blender headless on this machine or a VPS
//   MCP_TARGET=web      drive the wasm build in a browser tab, via the relay
//
// The agent sees the same tools either way — that is the whole point. Develop
// against `native` (fast, scriptable, testable), switch to `web` when a human
// needs to watch, without touching the harness above.

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";

import { TOOLS, GUARDED } from "./tools.js";
import { createNativeAdapter } from "./adapters/native.js";
import { createWebAdapter } from "./adapters/web.js";

const TARGET = process.env.MCP_TARGET || "native";
const ALLOW_PYTHON = process.env.MCP_ALLOW_PYTHON === "1";

const adapter = TARGET === "web" ? createWebAdapter() : createNativeAdapter();

/**
 * Turn a bridge answer into an MCP tool result.
 *
 * Images come back as base64 and are returned as MCP image content, not as a
 * wall of base64 text: that is what lets a vision-capable agent actually look
 * at its own render instead of being handed 50 KB of noise. Exported geometry
 * stays textual — the agent is meant to hand it to your service, not read it.
 */
function toToolResult(op, answer) {
  if (!answer.ok) {
    return {
      isError: true,
      content: [
        {
          type: "text",
          text: answer.traceback ? `${answer.error}\n\n${answer.traceback}` : String(answer.error),
        },
      ],
    };
  }

  const result = answer.result ?? {};

  if (op === "render" && result.base64) {
    return {
      content: [
        { type: "image", data: result.base64, mimeType: result.mime ?? "image/png" },
        {
          type: "text",
          // Name the container only when it was not the one asked for, so the
          // agent learns that PNG is unavailable without being told every time.
          text:
            `Rendered ${result.width}x${result.height} through camera '${result.camera}'.` +
            (result.fallback_from?.length
              ? ` Encoded as ${result.format} because ${result.fallback_from.join(", ")} failed.`
              : ""),
        },
      ],
    };
  }

  if (op === "export" && result.base64) {
    // Keep the payload out of the transcript by default: an agent almost never
    // needs to see the bytes, and a 3 MB glb would evict everything else from
    // its context. MCP_INLINE_EXPORT=1 puts them back for callers that do.
    const summary = `Exported ${result.bytes} bytes as ${result.format} (${result.filename}).`;
    if (process.env.MCP_INLINE_EXPORT !== "1") {
      return { content: [{ type: "text", text: `${summary} Base64 withheld (set MCP_INLINE_EXPORT=1 to inline it).` }] };
    }
    return {
      content: [
        { type: "text", text: summary },
        { type: "text", text: result.base64 },
      ],
    };
  }

  return { content: [{ type: "text", text: JSON.stringify(result, null, 2) }] };
}

const server = new McpServer({ name: "blender-mcp-bridge", version: "0.1.0" });

for (const tool of TOOLS) {
  if (GUARDED.has(tool.name) && !ALLOW_PYTHON) {
    // Registering it and refusing at call time would still advertise it; leaving
    // it out means an agent never plans around a tool it cannot use.
    continue;
  }

  server.registerTool(
    tool.name,
    {
      title: tool.title,
      description: tool.description,
      inputSchema: tool.schema,
      annotations: { readOnlyHint: ["capabilities", "scene_graph", "object_info"].includes(tool.name) },
    },
    async (args) => {
      const answer = await adapter.call(tool.name, args ?? {});
      return toToolResult(tool.name, answer);
    },
  );
}

const shutdown = async () => {
  try {
    await adapter.close();
  } finally {
    process.exit(0);
  }
};
process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);

// stdout belongs to the MCP transport; anything we want to say goes to stderr.
process.stderr.write(
  `[blender-mcp] target=${TARGET} python=${ALLOW_PYTHON ? "allowed" : "blocked"} ` +
    `tools=${TOOLS.length - (ALLOW_PYTHON ? 0 : GUARDED.size)}\n`,
);

await server.connect(new StdioServerTransport());
