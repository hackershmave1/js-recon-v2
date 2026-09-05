// D45a — inline <script> capture wiring. background.js + content-script.js are DOM/`chrome`-
// coupled browser glue that can't be imported in Node, so (like test_mv3_listeners.mjs) these
// are structural source assertions. The no-noise + no-flood behaviour of the pure relevance
// filter is covered behaviourally in test_inline_relevance.mjs.
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const cs = fs.readFileSync(path.resolve(__dirname, '../content-script.js'), 'utf8');
const bg = fs.readFileSync(path.resolve(__dirname, '../background.js'), 'utf8');

// --- content-script: read INLINE (srcless) <script> bodies, not just script[src] ---
assert.ok(cs.includes("querySelectorAll('script:not([src])')"), 'scans inline (srcless) <script> nodes');
assert.ok(cs.includes("action: 'inlineScriptDetected'"), 'reports inline bodies on a dedicated channel');
assert.ok(/isCapturableInlineScript/.test(cs), 'gates inline capture on a JS-type check');
assert.ok(/text\/javascript|application\/javascript|'module'/.test(cs), 'inline type gate allows classic/module JS only (no JSON/importmap islands)');
// Reading textContent from the mutation-added node ref is robust to a self-removing bootstrap
// (review Finding C): textContent still works on a detached node.
assert.match(cs, /addedNodes[\s\S]*?isCapturableInlineScript[\s\S]*?textContent/, 'observer reads inline textContent from the added node');
assert.match(cs, /scanLoadedScripts\(\)\s*\{[\s\S]*?scanInlineScripts\(\);/, 'initial scan also reads inline scripts');

// --- background: route + gate + no-noise + flood cap ---
assert.ok(bg.includes("'inlineScriptDetected'"), 'inline message is fire-and-forget + routed');
assert.match(bg, /handleInlineScript\(request, sender\)\s*\{/, 'has an inline handler');
assert.match(bg, /handleInlineScript[\s\S]*?isInScope\(pageUrl\)/, 'inline scope-gates on the page url');
assert.match(bg, /handleInlineScript[\s\S]*?isRelevantInlineScript\(request\.content\)/, 'inline applies the content relevance filter (no-noise linchpin)');
assert.ok(bg.includes('INLINE_PER_PAGE_CAP'), 'inline capture is per-page capped (no SPA flood)');
assert.match(bg, /#inline-\$\{ordinal\}/, 'synthetic URL keyed on ordinal (position), not content hash');
assert.match(bg, /typeof metadata\.inlineContent === 'string'[\s\S]*?content: metadata\.inlineContent/, 'processFile uses inline content without a network fetch');
assert.match(bg, /captureSourceMaps && typeof metadata\.inlineContent !== 'string'/, 'inline skips source-map detection/probe');
assert.match(bg, /import \{ isRelevantInlineScript \} from '\.\/modules\/inline-relevance\.js'/, 'imports the inline relevance filter');

console.log('test_inline_capture: ok');
