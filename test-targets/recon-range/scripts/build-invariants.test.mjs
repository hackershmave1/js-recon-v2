import { test } from "node:test";
import assert from "node:assert/strict";
import { readdir, readFile } from "node:fs/promises";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const DISTS = ["dist/vite/assets", "dist/webpack"];
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

for (const dist of DISTS) {
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
