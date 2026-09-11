// Resize text-loss detector.
//
// The defect: after a window resize, a widget's background is painted and its
// label is not. Absolute pixel counts drift across a session, so this measures
// a frame against ITSELF rather than against a stored reference:
//
//   1. resize storm, with the settle repair disabled so the defect is visible
//   2. capture A -- the frame the resize produced
//   3. force a full redraw without resizing
//   4. capture B -- the same window, same size, drawn again
//   5. count pixels lit in B and dark in A, per region
//
// A pixel that is present only in B was missing from the frame the resize
// produced. The Properties panel is the subject; the 3D viewport is the
// control -- EEVEE's TAA makes it differ between any two frames, so a non-zero
// viewport delta is expected and a near-zero panel delta is the pass condition.
//
// Usage: node scripts/resize_textloss_test.mjs [runs]
import { spawn } from "node:child_process";
import { chromium } from "playwright";

const RUNS = Number(process.argv[2] || 6);
const PORT = 8177;
const ROOT = "demo/dist";

const srv = spawn("node", ["scripts/serve.mjs", ROOT, String(PORT)], { stdio: "ignore" });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
await sleep(700);

// No executablePath: the older harnesses pin chromium-1228, which is not what
// `playwright install` puts down any more. Let playwright resolve its own.
const browser = await chromium.launch({
  headless: false,
  env: { ...process.env, DISPLAY: ":0", XDG_RUNTIME_DIR: "/run/user/1000" },
  args: ["--ozone-platform=x11", "--enable-unsafe-webgpu", "--enable-features=Vulkan",
         "--ignore-gpu-blocklist", "--window-size=1700,1000"],
});
const ctx = await browser.newContext({ viewport: { width: 1600, height: 950 } });
const page = await ctx.newPage();

const logs = [];
page.on("console", (m) => {
  const t = m.text();
  // WGPU_UNCAPTURED is the whole point of the new listener: never drop it.
  if (t.includes("WGPU_UNCAPTURED") || t.includes("WGPU_STATS")) logs.push(t);
});

await page.addInitScript(() => {
  window.__CAPENV = { BLENDER_WEB_NO_RESIZE_REPAIR: "1" };
});
await page.goto(`http://localhost:${PORT}/`, { waitUntil: "load" });

process.stdout.write("boot");
for (let i = 0; i < 90; i++) {
  if ((await page.evaluate(() => window.__BGUI__ || {})).window) break;
  process.stdout.write(".");
  await sleep(2000);
}
await sleep(5000);
console.log(" ok");

// Counters exported by the backend. Log lines are dropped by the page console
// under load; these are not.
const COUNTERS = [
  "vp_collapsed", "sc_collapsed", "rect_neg_origin",
  "rp_attach_dirty", "device_errors",
  "text_draw_issued", "text_draw_recorded",
];
const readCounters = () =>
  page.evaluate((names) => {
    const M = window.Module, out = {};
    for (const n of names) {
      const f = M["_blender_web_" + n];
      out[n] = typeof f === "function" ? f() : null;
    }
    return out;
  }, COUNTERS);

// Capture the UI framebuffer into a page-side slot.
const capture = (slot) =>
  page.evaluate(async (slot) => {
    const M = window.Module;
    M._wgpu_ui_capture_request();
    for (let i = 0; i < 400; i++) {
      if (M._wgpu_ui_capture_ready()) break;
      await new Promise((r) => requestAnimationFrame(r));
    }
    if (!M._wgpu_ui_capture_ready()) return null;
    const w = M._wgpu_ui_capture_w(), h = M._wgpu_ui_capture_h();
    const p = M._wgpu_ui_capture_ptr();
    window.__caps = window.__caps || {};
    // Copy out: the backend reuses this buffer for the next request.
    window.__caps[slot] = { w, h, px: M.HEAPU8.slice(p, p + w * h * 4) };
    return { w, h };
  }, slot);

// Count pixels meaningfully brighter in B than in A, split by column band.
// Text on a panel is a small bright run on a flat ground, so luminance is
// enough and it avoids counting the viewport's TAA shimmer as content.
const diff = () =>
  page.evaluate(() => {
    const A = window.__caps.a, B = window.__caps.b;
    if (!A || !B || A.w !== B.w || A.h !== B.h) return { error: "size mismatch" };
    const { w, h } = A;
    const panelX0 = Math.floor(w * 0.80);       // Properties editor
    const viewX0 = Math.floor(w * 0.20), viewX1 = Math.floor(w * 0.72);
    let panel = 0, view = 0;
    const lum = (d, i) => 0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2];
    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        const i = (y * w + x) * 4;
        if (lum(B.px, i) - lum(A.px, i) < 40) continue;
        if (x >= panelX0) panel++;
        else if (x >= viewX0 && x < viewX1) view++;
      }
    }
    return { w, h, panel, view };
  });

const results = [];
for (let run = 1; run <= RUNS; run++) {
  const before = await readCounters();

  // Resize storm. A resize is the only full interface redraw, which is why it
  // is the only moment the defect appears.
  for (const [w, h] of [[1500, 900], [1580, 960], [1460, 880], [1600, 950]]) {
    await page.setViewportSize({ width: w, height: h });
    await sleep(120);
  }
  await sleep(1200);
  const ca = await capture("a");

  // Force a full redraw at the SAME size: sweep the pointer down the panel so
  // every widget under it is tagged, then let it settle.
  const box = await page.locator("canvas").boundingBox();
  for (let i = 0; i < 26; i++) {
    await page.mouse.move(box.x + box.width * 0.90, box.y + 60 + i * 30);
    await sleep(18);
  }
  await page.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.5);
  await sleep(1500);
  const cb = await capture("b");

  const after = await readCounters();
  if (!ca || !cb) { console.log(`run ${run}: capture failed`); continue; }
  const d = await diff();
  const delta = {};
  for (const k of COUNTERS) {
    delta[k] = before[k] === null ? null : after[k] - before[k];
  }
  results.push({ ...d, delta });
  console.log(
    `run ${run}: panneau=${String(d.panel).padStart(5)} vue=${String(d.view).padStart(6)}` +
    `  | vp_collapsed=${delta.vp_collapsed} sc_collapsed=${delta.sc_collapsed}` +
    ` neg_origin=${delta.rect_neg_origin} attach_dirty=${delta.rp_attach_dirty}` +
    ` device_errors=${delta.device_errors}`);
}

const panels = results.map((r) => r.panel).filter((n) => Number.isFinite(n));
if (panels.length) {
  panels.sort((a, b) => a - b);
  const med = panels[panels.length >> 1];
  console.log(`\npanneau: min=${panels[0]} med=${med} max=${panels[panels.length - 1]}` +
              `  (proche de 0 = le texte ne se perd plus)`);
}
const errs = logs.filter((l) => l.includes("WGPU_UNCAPTURED"));
console.log(`erreurs device rapportees: ${errs.length}`);
for (const e of errs.slice(0, 8)) console.log("  " + e.slice(0, 200));

await browser.close();
srv.kill();
