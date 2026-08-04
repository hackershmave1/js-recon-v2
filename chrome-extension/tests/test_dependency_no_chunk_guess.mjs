#!/usr/bin/env node
/**
 * Regression: the DependencyExtractor must NOT invent webpack chunk URLs from numeric ids.
 *
 * The old heuristic matched a bare `e(123)` call — ubiquitous in minified code — and added
 * `/static/js/123.chunk.js` + `/chunks/123.js`. On any bundler that content-hashes chunk
 * names (webpack 5, Module Federation) those never resolve, so it flooded the fetch queue
 * and the Errors panel with 404s (observed live on honeybook). Real string dependencies must
 * still be extracted.
 */
import assert from 'node:assert/strict';
import { DependencyExtractor } from '../modules/dependency-extractor.js';

const dx = new DependencyExtractor();

// Minified-style source full of bare e(NN) calls + an explicit __webpack_require__, plus a
// real ESM import and a real require() that MUST survive.
const content = [
  'function e(n){return n}',
  'var a=e(579),b=e(816);__webpack_require__(135);',
  'import x from "./real-module.js";',
  'const y=require("some-package");'
].join('\n');

const deps = dx.extract(content, 'https://www.honeybook.com/static/js/main.abc123.js');
const urls = deps.map((d) => d.url);
const resolved = deps.map((d) => d.resolvedUrl);

// No invented chunk URLs (checked on both the raw and resolved forms).
const all = [...urls, ...resolved];
assert.ok(!all.some((u) => u.includes('/chunks/')), `must not guess /chunks/ URLs; got ${JSON.stringify(all)}`);
assert.ok(!all.some((u) => /\/static\/js\/\d+\.chunk\.js/.test(u)), `must not guess numeric .chunk.js URLs; got ${JSON.stringify(all)}`);

// Real dependencies are still discovered.
assert.ok(urls.includes('./real-module.js'), 'real ESM import must still be extracted');
assert.ok(urls.includes('some-package'), 'real require() must still be extracted');

console.log('✅ dependency-extractor no-chunk-guess regression passed');
