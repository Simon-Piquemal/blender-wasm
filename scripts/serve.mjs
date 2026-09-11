// Tiny static file server that sets the cross-origin isolation headers
// (COOP/COEP) WASM pthreads + SharedArrayBuffer require. Used by both the
// dev workflow and the Playwright verifier.
import { createServer } from "node:http";
import { readFile, stat, writeFile } from "node:fs/promises";
import { extname, join, resolve, relative, isAbsolute } from "node:path";

// Resolved, not as given: join() emits the platform separator, so on Windows a
// raw `startsWith(ROOT)` containment check compares "demo\dist\..." against
// "demo/dist" and rejects every request with a 403.
const ROOT = resolve(process.argv[2] || process.cwd());
const PORT = Number(process.argv[3] || 8080);

const TYPES = {
  ".html": "text/html",
  ".js": "text/javascript",
  ".mjs": "text/javascript",
  ".wasm": "application/wasm",
  ".data": "application/octet-stream",
  ".json": "application/json",
  ".css": "text/css",
  ".png": "image/png",
};

const server = createServer(async (req, res) => {
  try {
    const url = new URL(req.url, "http://localhost");

    /* Dev-only drop point for regenerating the WGSL translation seed.
     *
     * The seed lives in the page's IndexedDB, written by the shader worker, and
     * is several MB -- too big to carry back out through a devtools evaluate.
     * This lets the page POST it straight to a file.
     *
     * Off unless SERVE_SEED_OUT names the file to write, and that is the ONLY
     * path it will ever write: a static file server that takes uploads is a
     * footgun, so it is opt-in, single-purpose, and the destination comes from
     * the command line rather than from the request. */
    if (req.method === "POST" && url.pathname === "/__seed") {
      const out = process.env.SERVE_SEED_OUT;
      if (!out) { res.writeHead(403).end("SERVE_SEED_OUT not set"); return; }
      const chunks = [];
      for await (const c of req) chunks.push(c);
      const body = Buffer.concat(chunks);
      await writeFile(out, body);
      res.writeHead(200, { "Access-Control-Allow-Origin": "*" });
      res.end(String(body.length));
      console.log(`seed written: ${out} (${body.length} bytes)`);
      return;
    }
    let p = resolve(join(ROOT, decodeURIComponent(url.pathname)));
    const rel = relative(ROOT, p);
    if (rel.startsWith("..") || isAbsolute(rel)) { res.writeHead(403).end(); return; }
    let s = await stat(p).catch(() => null);
    if (s && s.isDirectory()) { p = join(p, "index.html"); s = await stat(p).catch(() => null); }
    if (!s) { res.writeHead(404).end("not found: " + url.pathname); return; }
    const body = await readFile(p);
    res.writeHead(200, {
      "Content-Type": TYPES[extname(p)] || "application/octet-stream",
      // pthreads / SharedArrayBuffer require cross-origin isolation:
      "Cross-Origin-Opener-Policy": "same-origin",
      "Cross-Origin-Embedder-Policy": "require-corp",
      "Cross-Origin-Resource-Policy": "cross-origin",
      "Cache-Control": "no-store",
    });
    res.end(body);
  } catch (e) {
    res.writeHead(500).end(String(e));
  }
});

server.listen(PORT, () => console.log(`serving ${ROOT} on http://localhost:${PORT}`));
