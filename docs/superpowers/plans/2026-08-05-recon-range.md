# recon-range Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `recon-range` test-target app + answer key + scoring script that serve as the controlled verify vehicle gating the Phase 4 v1 delete.

**Architecture:** One shared vanilla-ESM source compiled by both Vite and Webpack, served same-origin (dependency-free static server), captured by the extension, analyzed by the platform, and scored by a Node script that diffs `GET /runs/{id}/findings` against a machine-readable `answer-key.json`. Every expected value is calibrated to what the extractor provably emits (see the spec).

**Tech Stack:** Node ≥18 (built-in `node --test` + global `fetch`), Vite 5, Webpack 5, axios (target dep). No test framework, no html-webpack-plugin, no static-server dep.

**Canonical spec (source of truth for exact values):** `docs/superpowers/specs/2026-08-05-recon-range-design.md`

## Global Constraints

- Everything lives under `test-targets/recon-range/`. No platform code is touched.
- Vanilla ESM `.js` only — no TypeScript, no UI framework.
- All recon-relevant code is in external chunks; **no inline `<script>` logic** (the extension cannot capture inline script).
- Both bundlers emit external chunks + a `.map` per chunk with a `//# sourceMappingURL=` comment and **non-empty `sourcesContent`** (else the platform silently analyzes the minified bundle).
- The `inventory.js` axios instance has a **unique, never-shadowed, never-reassigned** name (or the base-join silently drops — `extract.py:254-255`).
- Secrets are FAKE, well-formed, non-live; the comment secret is a preserved `/*! … */` legal comment; secret constants are referenced from `main.js` so minification/tree-shaking can't drop them.
- Expected endpoint `value`s are the exact strings from spec §4 (verbatim `${x}`, sorted `?name` query suffix, `param`-type findings separate from endpoints).
- Ports: Vite `:4173`, Webpack `:4174`. Tenant header for scoring is the **UUID** of the tenant named `capture-spike`, resolved out-of-band.
- Layout deviation from spec §3 (for Vite-root correctness): the two configs + the Vite `index.html` live at the target root, not under `build/`; build output goes to `dist/{vite,webpack}` (gitignored).

---

### Task 1: Build foundation — dual bundler + source-map invariants

Establishes the two-bundler pipeline with a minimal app (entry + 3 stub lazy chunks) that satisfies the source-map/chunk invariants. Later tasks fill the chunks with real calls.

**Files:**
- Create: `test-targets/recon-range/package.json`, `.gitignore`, `index.html`, `vite.config.js`, `webpack.config.js`
- Create: `test-targets/recon-range/src/main.js`, `src/api/inventory.js`, `src/api/social.js`, `src/api/live.js` (stubs)
- Create: `test-targets/recon-range/scripts/serve.mjs`, `scripts/write-index.mjs`
- Test: `test-targets/recon-range/scripts/build-invariants.test.mjs`

**Interfaces:**
- Produces: two build outputs `dist/vite/assets/*.js(.map)` and `dist/webpack/*.js(.map)`; npm scripts `build:vite`, `build:webpack`, `serve:vite`, `serve:webpack`, `test`.

- [ ] **Step 1: Write the failing invariant test** — `scripts/build-invariants.test.mjs`

```js
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
```

- [ ] **Step 2: Run it, verify it fails** — `cd test-targets/recon-range && node --test scripts/build-invariants.test.mjs` → FAIL (no `dist/`).

- [ ] **Step 3: Create `package.json`**

```json
{
  "name": "recon-range",
  "private": true,
  "type": "module",
  "engines": { "node": ">=18" },
  "scripts": {
    "build:vite": "vite build",
    "build:webpack": "webpack --config webpack.config.js && node scripts/write-index.mjs dist/webpack",
    "build": "npm run build:vite && npm run build:webpack",
    "serve:vite": "node scripts/serve.mjs dist/vite 4173",
    "serve:webpack": "node scripts/serve.mjs dist/webpack 4174",
    "score": "node scripts/score-cli.mjs",
    "test": "npm run build && node --test scripts/"
  },
  "dependencies": { "axios": "^1.7.0" },
  "devDependencies": { "vite": "^5.4.0", "webpack": "^5.94.0", "webpack-cli": "^5.1.4" }
}
```

- [ ] **Step 4: Create `.gitignore`**

```
node_modules/
dist/
```

- [ ] **Step 5: Create `index.html` (Vite entry — ESM)**

```html
<!doctype html>
<html>
  <head><meta charset="utf-8" /><title>recon-range</title></head>
  <body>
    <main id="app"><h1>recon-range</h1><p>scroll down</p></main>
    <script type="module" src="/src/main.js"></script>
  </body>
</html>
```

- [ ] **Step 6: Create `vite.config.js`**

```js
import { defineConfig } from "vite";
export default defineConfig({
  build: { outDir: "dist/vite", sourcemap: true, emptyOutDir: true, minify: "esbuild" },
});
```

- [ ] **Step 7: Create `webpack.config.js` (classic-script output; JSONP chunk loads are captured)**

```js
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
const __dirname = dirname(fileURLToPath(import.meta.url));
export default {
  mode: "production",
  entry: resolve(__dirname, "src/main.js"),
  devtool: "source-map",
  output: {
    path: resolve(__dirname, "dist/webpack"),
    filename: "main.js",
    chunkFilename: "[name].chunk.js",
    clean: true,
  },
  optimization: { runtimeChunk: false },
};
```

- [ ] **Step 8: Create the static server `scripts/serve.mjs` (dependency-free; serves `.map` as JSON)**

```js
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { join, extname, normalize } from "node:path";
const dir = process.argv[2];
const port = Number(process.argv[3] || 8080);
const TYPES = { ".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript", ".map": "application/json", ".json": "application/json", ".css": "text/css" };
createServer(async (req, res) => {
  try {
    let p = decodeURIComponent(req.url.split("?")[0]);
    if (p.endsWith("/")) p += "index.html";
    const file = join(dir, normalize(p).replace(/^(\.\.[/\\])+/, ""));
    const body = await readFile(file);
    res.writeHead(200, { "content-type": TYPES[extname(file)] || "application/octet-stream" });
    res.end(body);
  } catch { res.writeHead(404); res.end("not found"); }
}).listen(port, () => console.log(`serving ${dir} on http://localhost:${port}`));
```

- [ ] **Step 9: Create `scripts/write-index.mjs` (webpack classic-script index)**

```js
import { writeFile } from "node:fs/promises";
import { join } from "node:path";
const dir = process.argv[2];
const html = `<!doctype html><html><head><meta charset="utf-8"><title>recon-range</title></head><body><main id="app"><h1>recon-range</h1><p>scroll down</p></main><script src="main.js"></script></body></html>`;
await writeFile(join(dir, "index.html"), html);
console.log(`wrote ${dir}/index.html`);
```

- [ ] **Step 10: Create `src/main.js` (static imports of stubs → guarantee 3 lazy chunks; scroll sentinels)**

```js
const lazy = [
  { name: "inventory", load: () => import("./api/inventory.js") },
  { name: "social", load: () => import("./api/social.js") },
  { name: "live", load: () => import("./api/live.js") },
];
function whenVisible(el, cb) {
  new IntersectionObserver((entries, obs) => {
    if (entries.some(e => e.isIntersecting)) { obs.disconnect(); cb(); }
  }).observe(el);
}
for (const { name, load } of lazy) {
  const s = document.createElement("div");
  s.style.height = "1200px";
  s.textContent = name;
  document.body.appendChild(s);
  whenVisible(s, () => load().then(m => { const fn = Object.values(m).find(v => typeof v === "function"); if (fn) fn(); }));
}
```

- [ ] **Step 11: Create the three stub lazy chunks** — `src/api/inventory.js`, `src/api/social.js`, `src/api/live.js`, each:

```js
export function init() { /* filled in Task 3 */ }
```

- [ ] **Step 12: Install + build both** — `cd test-targets/recon-range && npm install && npm run build`
Expected: `dist/vite/assets/` and `dist/webpack/` each contain `main*.js` + ≥3 chunk `.js` + a `.map` per chunk.

- [ ] **Step 13: Run the invariant test, verify it passes** — `node --test scripts/build-invariants.test.mjs` → PASS. If a bundler inlined a chunk or omitted `sourcesContent`, fix the config before proceeding.

- [ ] **Step 14: Commit**

```bash
git add test-targets/recon-range
git commit -m "feat(recon-range): dual-bundler build foundation with sourcesContent maps"
```

---

### Task 2: Answer key + structural consistency test

**Files:**
- Create: `test-targets/recon-range/answer-key.json`
- Test: `test-targets/recon-range/scripts/answer-key.test.mjs`

**Interfaces:**
- Produces: `answer-key.json` consumed by `planted-presence.test.mjs` (Task 3) and `score.mjs` (Task 4). Shape: `{ should_find:[{id,src,method,operation?,value?,host?,params:[{location,name}]}], known_blind_spots:[{id,src,expect,probe?,endpoint?}], secrets:{must:{provider:count},info:[provider]}, coverage_asserts:{min_unattributed,source_map_ok:[],require_sources_recovered} }`.

- [ ] **Step 1: Write the failing consistency test** — `scripts/answer-key.test.mjs`

```js
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
const key = JSON.parse(await readFile(new URL("../answer-key.json", import.meta.url)));

test("ids unique across should_find + blind_spots", () => {
  const ids = [...key.should_find, ...key.known_blind_spots].map(x => x.id);
  assert.equal(new Set(ids).size, ids.length);
});
test("each should_find has method and (operation or host)", () => {
  for (const e of key.should_find) {
    assert.ok(e.method, `${e.id} missing method`);
    assert.ok(e.operation || e.host, `${e.id} needs operation or host`);
  }
});
test("secrets.must has positive counts", () => {
  for (const [p, n] of Object.entries(key.secrets.must)) assert.ok(n >= 1, `${p} must>=1`);
});
```

- [ ] **Step 2: Run it, verify it fails** — `node --test scripts/answer-key.test.mjs` → FAIL (no `answer-key.json`).

- [ ] **Step 3: Create `answer-key.json`** (values transcribed from spec §4 — keep in sync if the spec changes)

```json
{
  "target": "recon-range",
  "spec": "docs/superpowers/specs/2026-08-05-recon-range-design.md",
  "should_find": [
    { "id": "ep-profile", "src": "src/api/profile.js", "method": "GET", "operation": "/api/v1/profile", "value": "GET /api/v1/profile", "params": [] },
    { "id": "ep-user", "src": "src/api/profile.js", "method": "GET", "operation": "/api/v1/users/${userId}", "value": "GET /api/v1/users/${userId}", "params": [] },
    { "id": "ep-search", "src": "src/api/profile.js", "method": "GET", "operation": "/api/v1/search", "value": "GET /api/v1/search?limit&q", "params": [ { "location": "query", "name": "q" }, { "location": "query", "name": "limit" } ] },
    { "id": "ep-order", "src": "src/api/profile.js", "method": "POST", "operation": "/api/v1/orders", "value": "POST /api/v1/orders", "params": [ { "location": "body", "name": "sku" }, { "location": "body", "name": "qty" } ] },
    { "id": "ep-cart", "src": "src/api/cart.js", "method": "PUT", "operation": "/api/v1/cart/{id}", "value": "PUT /api/v1/cart/{id}", "params": [] },
    { "id": "ep-secure", "src": "src/api/secure.js", "method": "GET", "operation": "/api/v1/secure", "value": "GET /api/v1/secure", "params": [] },
    { "id": "ep-inv", "src": "src/api/inventory.js", "method": "GET", "operation": "/api/v2/inventory", "value": "GET /api/v2/inventory", "params": [] },
    { "id": "ep-checkout", "src": "src/api/inventory.js", "method": "POST", "operation": "/api/v2/checkout", "value": "POST /api/v2/checkout", "params": [ { "location": "body", "name": "token" } ] },
    { "id": "ep-session", "src": "src/api/inventory.js", "method": "DELETE", "operation": "/api/v1/session", "value": "DELETE /api/v1/session", "params": [] },
    { "id": "ep-config", "src": "src/api/social.js", "method": "GET", "operation": "/api/v1/config", "value": "GET /api/v1/config", "params": [] },
    { "id": "ep-feedback", "src": "src/api/social.js", "method": "POST", "operation": "/api/v1/feedback", "value": "POST /api/v1/feedback", "params": [ { "location": "body", "name": "msg" } ] },
    { "id": "ep-ws", "src": "src/api/live.js", "method": "WSS", "operation": "/ws/live", "value": "WSS /ws/live", "params": [] },
    { "id": "ep-ga", "src": "src/api/thirdparty.js", "method": "GET", "host": "www.google-analytics.com", "params": [] },
    { "id": "ep-stripe", "src": "src/api/thirdparty.js", "method": "POST", "host": "api.stripe.com", "operation": "/v1/tokens", "params": [] },
    { "id": "ep-sentry", "src": "src/api/thirdparty.js", "method": "POST", "host": "o0.ingest.sentry.io", "params": [] }
  ],
  "known_blind_spots": [
    { "id": "bs-eventsource", "src": "src/api/blindspots.js", "expect": "no_finding", "probe": "/api/v1/stream" },
    { "id": "bs-concat", "src": "src/api/blindspots.js", "expect": "unattributed" },
    { "id": "bs-variable", "src": "src/api/blindspots.js", "expect": "unattributed" },
    { "id": "bs-wrapper", "src": "src/api/blindspots.js", "expect": "no_trace", "probe": "/api/v1/hidden" },
    { "id": "bs-headers", "src": "src/api/secure.js", "expect": "headers_invisible", "endpoint": "ep-secure" }
  ],
  "secrets": { "must": { "stripe": 2, "aws": 1 }, "info": [ "github", "slack", "hmac" ] },
  "coverage_asserts": { "min_unattributed": 2, "source_map_ok": [ "capture", "inline" ], "require_sources_recovered": true }
}
```

- [ ] **Step 4: Run it, verify it passes** — `node --test scripts/answer-key.test.mjs` → PASS.

- [ ] **Step 5: Commit**

```bash
git add test-targets/recon-range/answer-key.json test-targets/recon-range/scripts/answer-key.test.mjs
git commit -m "feat(recon-range): calibrated answer key + consistency test"
```

---

### Task 3: Planted API surface + secrets

Replace the Task 1 stubs and add the remaining modules so the built JS realizes every answer-key row. Runtime calls fail harmlessly; only the static shapes matter (plus the secret strings surviving into the bundle).

**Files:**
- Create/Modify: `src/main.js` (wire eager modules + secret retention), `src/api/{profile,cart,thirdparty,secure,blindspots}.js`, `src/secrets.js`
- Modify: `src/api/{inventory,social,live}.js` (fill the stubs)
- Test: `test-targets/recon-range/scripts/planted-presence.test.mjs`

**Interfaces:**
- Consumes: `answer-key.json` (Task 2).
- Produces: the captured JS surface the platform analyzes.

- [ ] **Step 1: Write the failing presence test** — `scripts/planted-presence.test.mjs`

```js
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
```

- [ ] **Step 2: Run it, verify it fails** — `node --test scripts/planted-presence.test.mjs` → FAIL.

- [ ] **Step 3: Write `src/secrets.js`** (FAKE values; avoid canonical vendor example keys)

```js
export const KEYS = {
  stripe: "sk_live_51ReconRange7fA2kQ9mZ0xY3bV6nP1dC8sT",
  awsId: "AKIA2E4RECONRANGE7QX",
  awsSecret: "rR0nR4nGe7fEx4mpLe0000wJalr000bPxRfiCYz9kLmN",
  github: "ghp_ReconRange0123456789abcdef0123456789abcd",
  slack: "xoxb-2222222222-3333333333-ReconRangeFakeToken0",
  hmac: "9f8e7d6c5b4a39281706f5e4d3c2b1a0ffeeddccbbaa9988",
};
/*! recon-range leftover key (proves whole-file scanning of preserved comments):
    sk_live_51ReconRangeCOMMENT9zX8wV7uT6sR5qP4nM3kJ2h */
```

- [ ] **Step 4: Write `src/api/profile.js`**

```js
export async function loadProfile() {
  await fetch("/api/v1/profile");
  const userId = window.__uid || "me";
  await fetch(`/api/v1/users/${userId}`);
  const q = "shirt";
  await fetch(`/api/v1/search?q=${q}&limit=20`);
  await fetch("/api/v1/orders", { method: "POST", body: JSON.stringify({ sku: "A1", qty: 2 }) });
}
```

- [ ] **Step 5: Write `src/api/cart.js`**

```js
export function updateCart() {
  const xhr = new XMLHttpRequest();
  xhr.open("PUT", "/api/v1/cart/42");
  xhr.send();
}
```

- [ ] **Step 6: Write `src/api/thirdparty.js`**

```js
export function pingThirdParties() {
  fetch("https://www.google-analytics.com/g/collect?v=2&tid=G-XXXX");
  fetch("https://api.stripe.com/v1/tokens", { method: "POST" });
  fetch("https://o0.ingest.sentry.io/api/1/envelope/", { method: "POST" });
}
```

- [ ] **Step 7: Write `src/api/secure.js`**

```js
export function loadSecure(token, sig, ts) {
  return fetch("/api/v1/secure", {
    headers: { Authorization: `Bearer ${token}`, "X-Signature": sig, "X-Timestamp": ts },
  });
}
```

- [ ] **Step 8: Fill `src/api/inventory.js`** (unique unshadowed instance name)

```js
import axios from "axios";
const inventoryApi = axios.create({ baseURL: "/api/v2" });
export async function loadInventory() {
  await inventoryApi.get("/inventory");
  await inventoryApi.post("/checkout", { token: "t" });
  await axios.delete("/api/v1/session");
}
```

- [ ] **Step 9: Fill `src/api/social.js`** (local `$` stub keeps runtime happy; shape is jQuery)

```js
const $ = {
  getJSON: (url) => fetch(url).then((r) => r.json()),
  ajax: (opts) => fetch(opts.url, { method: opts.method, body: JSON.stringify(opts.data) }),
};
export function loadSocial() {
  $.getJSON("/api/v1/config");
  $.ajax({ url: "/api/v1/feedback", method: "POST", data: { msg: "hi" } });
}
```

- [ ] **Step 10: Fill `src/api/live.js`**

```js
export function openLive() {
  return new WebSocket("wss://api.recon-range.test/ws/live");
}
```

- [ ] **Step 11: Write `src/api/blindspots.js`**

```js
function pickUrl() { return "/api/v1/dynamic"; }
function makeClient() { return { get: (p) => fetch(p) }; }
export function blindSpots() {
  new EventSource("/api/v1/stream");
  const resource = "widgets";
  fetch("/api/v1/" + resource);
  const u = pickUrl();
  fetch(u);
  makeClient().get("/api/v1/hidden");
}
```

- [ ] **Step 12: Update `src/main.js`** — add eager imports + pin secrets so they survive minification. Prepend to the existing file:

```js
import { loadProfile } from "./api/profile.js";
import { updateCart } from "./api/cart.js";
import { pingThirdParties } from "./api/thirdparty.js";
import { loadSecure } from "./api/secure.js";
import { blindSpots } from "./api/blindspots.js";
import { KEYS } from "./secrets.js";

window.__reconKeys = KEYS;
loadProfile();
updateCart();
pingThirdParties();
loadSecure("tok", "sig", "123");
blindSpots();
```

(Keep the existing lazy-chunk/sentinel block below. The lazy stubs' `export function init()` is now replaced by the real `loadInventory`/`loadSocial`/`openLive`; the sentinel loader picks the first exported function, so rename or keep a single named export per lazy module.)

- [ ] **Step 13: Run presence test + rebuild + invariants** —
`node --test scripts/planted-presence.test.mjs` → PASS;
`npm run build` then `node --test scripts/build-invariants.test.mjs` → PASS (still ≥3 chunks with maps).

- [ ] **Step 14: Sanity-check the built bundle carries the secret strings** —
grep the built main chunk for `sk_live_` twice (const + comment): `node -e "const fs=require('fs');const d=fs.readdirSync('dist/webpack');const f=d.find(x=>x.startsWith('main'));const c=fs.readFileSync('dist/webpack/'+f,'utf8');console.log((c.match(/sk_live_/g)||[]).length)"` → expect `2`. If `0–1`, the comment was stripped or the const tree-shaken; fix retention before proceeding.

- [ ] **Step 15: Commit**

```bash
git add test-targets/recon-range/src test-targets/recon-range/scripts/planted-presence.test.mjs
git commit -m "feat(recon-range): planted API surface, blind spots, and secrets"
```

---

### Task 4: Scoring script (pure lib + CLI)

**Files:**
- Create: `test-targets/recon-range/scripts/score.mjs` (pure), `scripts/score-cli.mjs` (fetch + args)
- Test: `test-targets/recon-range/scripts/score.test.mjs`

**Interfaces:**
- Consumes: the `GET /runs/{id}/findings` payload shape (`{findings:[{type,value,attributes,occurrences:[{host,source_path}]}], coverage:{source_map,sources_recovered,unattributed}}`) and `answer-key.json`.
- Produces: `scoreFindings(payload, key) -> { pass, missedEndpoints, missedParams, secretMisses, covFail, provCounts, blindViolations, unexpected }`.

- [ ] **Step 1: Write failing tests** — `scripts/score.test.mjs`

```js
import { test } from "node:test";
import assert from "node:assert/strict";
import { scoreFindings } from "./score.mjs";

const key = {
  should_find: [
    { id: "ep-profile", method: "GET", operation: "/api/v1/profile", params: [] },
    { id: "ep-order", method: "POST", operation: "/api/v1/orders", params: [{ location: "body", name: "sku" }] },
    { id: "ep-ga", method: "GET", host: "www.google-analytics.com", params: [] },
  ],
  known_blind_spots: [{ id: "bs-wrapper", probe: "/api/v1/hidden" }],
  secrets: { must: { stripe: 2, aws: 1 }, info: ["github"] },
  coverage_asserts: { min_unattributed: 2, source_map_ok: ["capture"], require_sources_recovered: true },
};
const good = {
  coverage: { source_map: "capture", sources_recovered: 3, unattributed: 2 },
  findings: [
    { type: "endpoint", value: "GET /api/v1/profile", attributes: { kind: "fetch", method: "GET" }, occurrences: [{ source_path: "src/api/profile.js" }] },
    { type: "endpoint", value: "POST /api/v1/orders", occurrences: [{ source_path: "src/api/profile.js" }] },
    { type: "param", value: "POST /api/v1/orders body:sku", attributes: { location: "body", name: "sku" }, occurrences: [] },
    { type: "endpoint", value: "GET /g/collect?tid&v", occurrences: [{ host: "www.google-analytics.com" }] },
    { type: "secret", value: "stripe:aaa", occurrences: [{ source_path: "input.js" }] },
    { type: "secret", value: "stripe:bbb", occurrences: [{ source_path: "input.js" }] },
    { type: "secret", value: "aws:ccc", occurrences: [{ source_path: "input.js" }] },
  ],
};

test("clean payload PASSes", () => {
  const r = scoreFindings(good, key);
  assert.equal(r.pass, true, JSON.stringify(r));
});
test("missing endpoint FAILs", () => {
  const p = { ...good, findings: good.findings.filter(f => f.value !== "GET /api/v1/profile") };
  const r = scoreFindings(p, key);
  assert.equal(r.pass, false);
  assert.deepEqual(r.missedEndpoints, ["ep-profile"]);
});
test("missing body param FAILs", () => {
  const p = { ...good, findings: good.findings.filter(f => f.type !== "param") };
  assert.equal(scoreFindings(p, key).pass, false);
});
test("only one stripe secret FAILs the count gate", () => {
  const p = { ...good, findings: good.findings.filter(f => f.value !== "stripe:bbb") };
  assert.deepEqual(scoreFindings(p, key).secretMisses, ["stripe>=2 (got 1)"]);
});
test("no recovered source_path FAILs the source-map gate", () => {
  const p = { ...good, findings: good.findings.map(f => f.type === "endpoint" ? { ...f, occurrences: [{ source_path: "input.js" }] } : f) };
  assert.ok(scoreFindings(p, key).covFail.some(x => x.includes("recovered")));
});
test("a resolved blind-spot probe is flagged (not a hard fail)", () => {
  const p = { ...good, findings: [...good.findings, { type: "endpoint", value: "GET /api/v1/hidden", occurrences: [] }] };
  const r = scoreFindings(p, key);
  assert.deepEqual(r.blindViolations, ["bs-wrapper"]);
  assert.equal(r.pass, true);
});
```

- [ ] **Step 2: Run, verify fail** — `node --test scripts/score.test.mjs` → FAIL (`score.mjs` not found).

- [ ] **Step 3: Implement `scripts/score.mjs`**

```js
const splitOp = (v) => { const [m, ...rest] = v.split(" "); const path = rest.join(" ").split("?")[0]; return [m, path]; };

export function scoreFindings(payload, key) {
  const findings = payload.findings || [];
  const cov = payload.coverage || {};
  const endpoints = findings.filter(f => f.type === "endpoint");
  const params = findings.filter(f => f.type === "param");
  const secrets = findings.filter(f => f.type === "secret");

  const endpointMatches = (e) => endpoints.some(f => {
    const [m, op] = splitOp(f.value);
    if (m !== e.method) return false;
    if (e.host) return (f.occurrences || []).some(o => o.host === e.host) && (!e.operation || op === e.operation);
    return op === e.operation;
  });
  const paramMatches = (p) => params.some(f => f.attributes && f.attributes.location === p.location && f.attributes.name === p.name);

  const results = key.should_find.map(e => ({ id: e.id, found: endpointMatches(e), params: (e.params || []).map(p => ({ ...p, found: paramMatches(p) })) }));
  const missedEndpoints = results.filter(r => !r.found).map(r => r.id);
  const missedParams = results.flatMap(r => r.params.filter(p => !p.found).map(p => `${r.id}:${p.location}:${p.name}`));

  const provCounts = {};
  for (const s of secrets) {
    const prov = (s.value || "").split(":")[0] || (s.attributes && s.attributes.rule ? String(s.attributes.rule).split(".")[1]?.toLowerCase() : "");
    if (prov) provCounts[prov] = (provCounts[prov] || 0) + 1;
  }
  const secretMisses = Object.entries(key.secrets.must)
    .filter(([p, n]) => (provCounts[p] || 0) < n)
    .map(([p, n]) => `${p}>=${n} (got ${provCounts[p] || 0})`);

  const covFail = [];
  if (!key.coverage_asserts.source_map_ok.includes(cov.source_map)) covFail.push(`source_map=${cov.source_map} not in ${key.coverage_asserts.source_map_ok.join("|")}`);
  if (key.coverage_asserts.require_sources_recovered && !(cov.sources_recovered > 0)) covFail.push(`sources_recovered=${cov.sources_recovered}`);
  if ((cov.unattributed || 0) < key.coverage_asserts.min_unattributed) covFail.push(`unattributed=${cov.unattributed} < ${key.coverage_asserts.min_unattributed}`);
  const recovered = [...endpoints, ...params].some(f => (f.occurrences || []).some(o => o.source_path && o.source_path !== "input.js"));
  if (!recovered) covFail.push("no recovered source_path (all input.js)");

  const blindViolations = (key.known_blind_spots || []).filter(b => b.probe && endpoints.some(f => splitOp(f.value)[1] === b.probe)).map(b => b.id);

  const known = new Set(key.should_find.filter(e => e.operation).map(e => `${e.method} ${e.operation}`));
  const hostSet = new Set(key.should_find.filter(e => e.host).map(e => e.host));
  const unexpected = endpoints.filter(f => {
    const [m, op] = splitOp(f.value);
    if (known.has(`${m} ${op}`)) return false;
    if ((f.occurrences || []).some(o => hostSet.has(o.host))) return false;
    return true;
  }).map(f => f.value);

  const pass = missedEndpoints.length === 0 && missedParams.length === 0 && secretMisses.length === 0 && covFail.length === 0;
  return { pass, missedEndpoints, missedParams, secretMisses, covFail, provCounts, blindViolations, unexpected };
}
```

- [ ] **Step 4: Run, verify pass** — `node --test scripts/score.test.mjs` → all PASS.

- [ ] **Step 5: Implement `scripts/score-cli.mjs`**

```js
import { readFile } from "node:fs/promises";
import { scoreFindings } from "./score.mjs";

const argv = process.argv.slice(2);
const args = {};
for (let i = 0; i < argv.length; i++) if (argv[i].startsWith("--")) args[argv[i].slice(2)] = argv[++i];

const base = args.base || "http://localhost:8000";
if (!args.run || !args.tenant) { console.error("usage: score-cli --run <id> --tenant <uuid> [--base http://localhost:8000]"); process.exit(2); }

const key = JSON.parse(await readFile(new URL("../answer-key.json", import.meta.url)));
const res = await fetch(`${base}/runs/${args.run}/findings`, { headers: { "X-Tenant-Id": args.tenant } });
if (!res.ok) { console.error(`findings fetch failed: ${res.status} ${res.statusText}`); process.exit(2); }
const r = scoreFindings(await res.json(), key);
console.log(JSON.stringify(r, null, 2));
console.log(r.pass ? "PASS" : "FAIL");
process.exit(r.pass ? 0 : 1);
```

- [ ] **Step 6: Commit**

```bash
git add test-targets/recon-range/scripts/score.mjs test-targets/recon-range/scripts/score-cli.mjs test-targets/recon-range/scripts/score.test.mjs
git commit -m "feat(recon-range): findings scoring lib + CLI"
```

---

### Task 5: README verify runbook + full-suite check

**Files:**
- Create: `test-targets/recon-range/README.md`

- [ ] **Step 1: Write `README.md`** — cover, in order: what recon-range is and that it gates the Phase 4 delete; `npm install`; per-bundler build + serve (`:4173` / `:4174`); the real-Chrome capture steps (workspace URL `http://localhost:8000`, add `localhost` to capture scope, Start → scroll to load all lazy chunks → Stop); trigger analysis + note the `run_id`; resolve the tenant UUID with the documented one-liner:

```bash
docker compose -f apps/platform/docker-compose.yml exec -T postgres psql -U recon -d recon -tAc "select id from tenant where name='capture-spike'"
```

then `npm run score -- --run <run_id> --tenant <tenant_uuid>`; interpret PASS/FAIL and the `blindViolations`/`unexpected` notes; repeat for the other bundler. Include the calibration caveats from spec §9 (blind spots are expected-missing; GitHub/Slack/HMAC informational; first run confirms fake-secret detection).

- [ ] **Step 2: Full-suite run** — `npm test` (builds both, then runs every `scripts/*.test.mjs`) → all PASS.

- [ ] **Step 3: Commit**

```bash
git add test-targets/recon-range/README.md
git commit -m "docs(recon-range): verify runbook"
```

---

## Self-Review

**1. Spec coverage:** §3 layout → Task 1 (+ noted deviation). §4.1 should_find → Task 2 key + Task 3 planting + Task 4 endpoint/param scoring. §4.2 blind spots → Task 3 planting + Task 4 `blindViolations`/`unattributed`. §4.3 secrets → Task 3 `secrets.js` + Task 4 provider-count gate (Stripe ≥2 encodes the comment-scan proof). §5 scoring → Task 4. §6 runbook → Task 5. §7 tests → build-invariants (T1), answer-key (T2), planted-presence (T3), score (T4). §2 capture/extractor constraints → encoded as build config + planting rules. No uncovered requirement.

**2. Placeholder scan:** the lazy stubs in Task 1 are explicitly replaced in Task 3 (not a placeholder deliverable — Task 1's deliverable is the build pipeline, which stubs legitimately satisfy). All test/impl steps carry real code. No TBD/TODO.

**3. Type consistency:** `scoreFindings` signature + return keys match between `score.test.mjs`, `score.mjs`, and `score-cli.mjs`. `answer-key.json` shape matches what `answer-key.test.mjs`, `planted-presence.test.mjs`, and `scoreFindings` read (`should_find[].{method,operation,host,params}`, `secrets.must`, `coverage_asserts.*`). The sentinel loader in `main.js` (Task 1) picks the first exported function — Task 3's lazy modules each export exactly one function (`loadInventory`/`loadSocial`/`openLive`), consistent.

**Fix applied inline:** Task 3 Step 12 notes the lazy modules now export their real single function (replacing `init`), so the Task 1 sentinel loader stays correct.
