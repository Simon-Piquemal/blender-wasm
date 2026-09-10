/* Appended to the emscripten glue with --post-js, so it runs in EVERY scope
 * blender.js is loaded in -- including emscripten's pthread workers.
 *
 * Why this exists: the release link uses -sPROXY_TO_PTHREAD=1, so the program's
 * main() -- and therefore every shader compilation -- runs on a WORKER. The
 * page installs globalThis.__WGSL_CACHE__ in its own global scope, which the
 * worker cannot see, so webgpu_shader.cc's wgpu_wgsl_cache_query() found
 * nothing and re-ran glslang + Tint for every shader, every session, at
 * 200-500 ms a piece (~30 stalls just to reach the default scene). Its writes
 * went nowhere too: __WGSL_CACHE_PUT__ is undefined there, so IndexedDB stayed
 * empty. The dev link has no PROXY_TO_PTHREAD, which is why the cache appears
 * to work when seeding it (scripts/wgsl_seed.mjs) and is inert once shipped.
 *
 * The wasm side only duck-types .get()/.set(), so a lazy stand-in is enough --
 * no rebuild required. Synchronous XHR is legal on a worker and the seed is
 * already in the HTTP cache by then (the page fetched it), so the read is a
 * cache hit; the parse is deferred to the first lookup so only the thread that
 * actually compiles shaders pays for it.
 */
(function () {
  /* WorkerGlobalScope rather than importScripts: the latter only exists in
   * classic workers, and emscripten may spawn module workers. */
  var isWorker = typeof document === "undefined" &&
                 typeof WorkerGlobalScope !== "undefined";
  if (!isWorker || globalThis.__WGSL_CACHE__) {
    return; /* the page installs its own on the main thread */
  }
  var map = null;
  function load() {
    if (map) {
      return map;
    }
    map = new Map();
    try {
      var url = new URL("wgsl-cache.json", self.location.href).href;
      var xhr = new XMLHttpRequest();
      xhr.open("GET", url, false);
      xhr.send();
      if (xhr.status >= 200 && xhr.status < 300) {
        map = new Map(JSON.parse(xhr.responseText));
      }
    }
    catch (e) {
      /* No seed: every shader translates at runtime, as before. */
    }
    console.log("[wgsl] worker seed: " + map.size + " entries");
    return map;
  }
  /* Write-through to the same IndexedDB store the page reads at boot, so a
   * shader the seed does not cover is translated once per browser, not once
   * per session. The page owns the schema; mirror it in case the worker gets
   * there first. */
  var dbp = null;
  function db() {
    if (!dbp) {
      dbp = new Promise(function (res, rej) {
        var rq = indexedDB.open("blender-fs", 2);
        rq.onupgradeneeded = function () {
          var d = rq.result;
          if (!d.objectStoreNames.contains("files")) { d.createObjectStore("files", { keyPath: "path" }); }
          if (!d.objectStoreNames.contains("wgsl")) { d.createObjectStore("wgsl", { keyPath: "key" }); }
        };
        rq.onsuccess = function () { res(rq.result); };
        rq.onerror = function () { rej(rq.error); };
      });
    }
    return dbp;
  }
  globalThis.__WGSL_CACHE__ = {
    get: function (k) { return load().get(k); },
    set: function (k, v) {
      load().set(k, v);
      db().then(function (d) {
        d.transaction("wgsl", "readwrite").objectStore("wgsl").put({ key: k, wgsl: v });
      }).catch(function () { /* persistence is best-effort */ });
    },
  };
})();

/* WEB BUILD debug: surface WebGPU validation errors from the render pthread.
 * A rejected submit silently drops every pass in that command buffer, so an
 * unnoticed error looks like "some draws vanished". */
if (typeof GPUAdapter !== "undefined" && !GPUAdapter.prototype.__blenderErrHook) {
  GPUAdapter.prototype.__blenderErrHook = true;
  const __origRequestDevice = GPUAdapter.prototype.requestDevice;
  GPUAdapter.prototype.requestDevice = async function (...args) {
    const dev = await __origRequestDevice.apply(this, args);
    try {
      let n = 0;
      dev.addEventListener("uncapturederror", (ev) => {
        if (n++ < 40) {
          const e = ev.error;
          console.error("WGPU_UNCAPTURED " + (e && e.constructor ? e.constructor.name : "?") + ": " +
                        String(e && e.message ? e.message : e).slice(0, 700));
        }
      });
    } catch (e) {}
    return dev;
  };
}
