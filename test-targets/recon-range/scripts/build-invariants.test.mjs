import { test } from "node:test";
import assert from "node:assert/strict";
import { readdir, readFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

// Two flavours of build output, both emitted by `npm run build`:
//   MAP_DISTS   — sourcemaps ON: the pipeline recovers ORIGINAL source and analyzes that.
//   NOMAP_DISTS — sourcemaps OFF: the pipeline has to analyze the MINIFIED chunk directly.
// The no-map flavour exists because the cross-chunk case (bs-crosschunk) is invisible on the
// recovered-source path — with maps the extractor reads orders.js's original
// `fetch(API_BASE + ORDERS_PATH)` and never touches the minified `fetch(a+o)` / `fetch(r.t+r.M)`
// that the cross-module resolver must actually learn to attribute.
const MAP_DISTS = ["dist/vite/assets", "dist/webpack"];
const NOMAP_DISTS = ["dist/vite-nomap/assets", "dist/webpack-nomap"];
const ALL_DISTS = [...MAP_DISTS, ...NOMAP_DISTS];
const root = new URL("../", import.meta.url);

async function jsFiles(rel) {
  const dir = new URL(rel + "/", root);
  const entries = await readdir(dir, { withFileTypes: true });
  // NOTE: deviation from the plan's verbatim `join(dir.pathname, e.name)` — on
  // Windows a file:// URL's .pathname is "/C:/Users/..." (leading slash before
  // the drive letter). path.join() doesn't recognize that as already-absolute,
  // so it normalizes to "\C:\Users\..." (drive-relative-to-current-drive), and
  // Node then resolves it by prepending the cwd's drive letter, producing a
  // doubled "C:\C:\Users\...\file.js" that ENOENTs. fileURLToPath() converts
  // the URL to a proper native path first; behavior on POSIX is unchanged.
  return entries.filter(e => e.isFile() && e.name.endsWith(".js")).map(e => join(fileURLToPath(dir), e.name));
}

for (const dist of MAP_DISTS) {
  test(`[${dist}] >=3 chunks, each with a sourcesContent map`, async () => {
    const files = await jsFiles(dist);
    assert.ok(files.length >= 3, `expected >=3 js files, got ${files.length}`);
    let mapped = 0;
    for (const f of files) {
      const code = await readFile(f, "utf8");
      if (!/[#@]\s*sourceMappingURL=/.test(code)) continue;
      mapped++;
      const map = JSON.parse(await readFile(f + ".map", "utf8"));
      assert.ok(Array.isArray(map.sourcesContent) && map.sourcesContent.some(s => s && s.length > 0),
        `${f}.map has empty sourcesContent`);
    }
    assert.ok(mapped >= 3, `expected >=3 chunks with a map comment, got ${mapped}`);
  });
}

for (const dist of NOMAP_DISTS) {
  test(`[${dist}] >=3 chunks, NONE mapped (forces minified analysis)`, async () => {
    const files = await jsFiles(dist);
    assert.ok(files.length >= 3, `expected >=3 js files, got ${files.length}`);
    for (const f of files) {
      const code = await readFile(f, "utf8");
      assert.doesNotMatch(code, /[#@]\s*sourceMappingURL=/, `${f} still carries a sourceMappingURL comment`);
      assert.ok(!existsSync(f + ".map"), `${f}.map should not exist in a no-map build`);
    }
  });
}

for (const dist of ALL_DISTS) {
  // bs-crosschunk premise: the base URL const (base.js) must survive the build in a DIFFERENT
  // chunk from the orders module that consumes it, i.e. the bundler emitted a real cross-chunk
  // edge instead of inlining the literal into the fetch. If a bundler ever folds
  // `https://api.acme.com` straight into the orders chunk, the fixture stops testing cross-chunk
  // resolution (a per-file extractor would just read the literal), so fail loudly here.
  test(`[${dist}] base URL crosses a chunk boundary (orders chunk does not inline it)`, async () => {
    const files = await jsFiles(dist);
    const baseChunks = [];
    const ordersChunks = [];
    for (const f of files) {
      const code = await readFile(f, "utf8");
      if (code.includes("api.acme.com")) baseChunks.push(f);
      if (code.includes("loadOrders")) ordersChunks.push(f);
    }
    assert.ok(baseChunks.length > 0, "base URL literal 'api.acme.com' vanished from the build (dead-code eliminated?)");
    assert.ok(ordersChunks.length > 0, "orders module ('loadOrders') not found in any chunk");
    const inlined = ordersChunks.filter(f => baseChunks.includes(f));
    assert.equal(inlined.length, 0,
      `orders chunk inlined the base URL (cross-chunk boundary lost): ${inlined.join(", ")}`);
  });
}
