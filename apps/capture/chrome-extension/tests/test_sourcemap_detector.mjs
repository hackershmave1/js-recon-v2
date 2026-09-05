// D45c — source-map detection beyond the inline comment: the `SourceMap:`/`X-SourceMap`
// response header, and the conventional `<file>.js.map` probe URL. Pure module, no chrome.
import assert from 'node:assert';
import { SourceMapDetector } from '../modules/sourcemap-detector.js';

const d = new SourceMapDetector();

// --- detectFromHeaders ---
// Absolute URL in the SourceMap header.
assert.equal(
  d.detectFromHeaders({ sourcemap: 'https://cdn.app/main.js.map' }, 'https://cdn.app/main.js'),
  'https://cdn.app/main.js.map'
);
// Legacy X-SourceMap, relative → resolved against the JS file URL.
assert.equal(
  d.detectFromHeaders({ 'x-sourcemap': 'main.js.map' }, 'https://app.test/static/main.js'),
  'https://app.test/static/main.js.map'
);
// SourceMap wins over X-SourceMap when both are present.
assert.equal(
  d.detectFromHeaders({ sourcemap: 'a.map', 'x-sourcemap': 'b.map' }, 'https://app.test/x.js'),
  'https://app.test/a.map'
);
// A data: URI header passes straight through.
assert.equal(
  d.detectFromHeaders({ sourcemap: 'data:application/json;base64,e30=' }, 'https://app.test/x.js'),
  'data:application/json;base64,e30='
);
// Absent / blank / non-object → null (never a wild probe).
assert.equal(d.detectFromHeaders({}, 'https://app.test/x.js'), null);
assert.equal(d.detectFromHeaders({ sourcemap: '   ' }, 'https://app.test/x.js'), null);
assert.equal(d.detectFromHeaders(null, 'https://app.test/x.js'), null);

// The inline comment stays authoritative: detect() resolves it; the caller only consults
// headers when detect() returns null (documented ordering).
assert.equal(
  d.detect('console.log(1)\n//# sourceMappingURL=real.js.map', 'https://app.test/x.js'),
  'https://app.test/real.js.map'
);
assert.equal(d.detect('console.log(1)', 'https://app.test/x.js'), null);

// --- conventionalMapUrl ---
// Strips query + fragment, appends .map.
assert.equal(
  d.conventionalMapUrl('https://app.test/static/main.js?v=2#x'),
  'https://app.test/static/main.js.map'
);
assert.equal(
  d.conventionalMapUrl('https://app.test/a/b.chunk.js'),
  'https://app.test/a/b.chunk.js.map'
);
// Directory-ish or unparseable → null (no pointless probe).
assert.equal(d.conventionalMapUrl('https://app.test/static/'), null);
assert.equal(d.conventionalMapUrl('not a url'), null);

console.log('ok - sourcemap-detector header + conventional probe (D45c)');
