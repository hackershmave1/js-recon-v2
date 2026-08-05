import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { existsSync } from "node:fs";
const root = new URL("../", import.meta.url);
const key = JSON.parse(await readFile(new URL("answer-key.json", root)));
const read = (rel) => readFile(new URL(rel, root), "utf8");

test("every should_find src file exists", () => {
  for (const e of key.should_find) assert.ok(existsSync(new URL(e.src, root)), `${e.src} missing`);
});
test("planted endpoint constructs present", async () => {
  const p = await read("src/api/profile.js");
  assert.match(p, /fetch\(\s*["'`]\/api\/v1\/profile/);
  assert.match(p, /\/api\/v1\/users\/\$\{/);
  assert.match(p, /\/api\/v1\/search\?q=/);
  assert.match(p, /\/api\/v1\/orders/);
  assert.match(await read("src/api/cart.js"), /\.open\(\s*["']PUT["']\s*,\s*["']\/api\/v1\/cart\/42/);
  const inv = await read("src/api/inventory.js");
  assert.match(inv, /axios\.create\(\s*\{\s*baseURL:\s*["']\/api\/v2["']/);
  assert.match(inv, /axios\.delete\(/);
  const soc = await read("src/api/social.js");
  assert.match(soc, /\$\.getJSON\(/);
  assert.match(soc, /\$\.ajax\(/);
  assert.match(await read("src/api/live.js"), /new WebSocket\(/);
  const bs = await read("src/api/blindspots.js");
  assert.match(bs, /new EventSource\(/);
  assert.match(bs, /fetch\(\s*["'`]\/api\/v1\/["'`]\s*\+/);
  const tp = await read("src/api/thirdparty.js");
  assert.match(tp, /google-analytics\.com/);
  assert.match(tp, /api\.stripe\.com/);
  assert.match(tp, /ingest\.sentry\.io/);
});
test("planted secrets present incl legal-comment stripe key", async () => {
  const s = await read("src/secrets.js");
  assert.match(s, /sk_live_/);
  assert.match(s, /AKIA[0-9A-Z]{16}/);
  assert.match(s, /ghp_/);
  assert.match(s, /xoxb-/);
  assert.match(s, /\/\*![\s\S]*sk_live_[\s\S]*\*\//);
});
