import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const bridgePath = path.resolve(__dirname, '../modules/rep-plus-bridge.js');
const source = fs.readFileSync(bridgePath, 'utf8');
const transformed = source.replace('export class RepPlusBridge', 'class RepPlusBridge');

const sandbox = {
  console,
  URL,
  setInterval: () => 0,
  chrome: {
    storage: {
      local: {
        get: async () => ({})
      }
    },
    runtime: {
      sendMessage: () => {}
    }
  }
};

vm.createContext(sandbox);
vm.runInContext(`${transformed}\nthis.RepPlusBridge = RepPlusBridge;`, sandbox, { filename: bridgePath });

const RepPlusBridge = sandbox.RepPlusBridge;
const bridge = new RepPlusBridge();

const hints = bridge.extractScriptImportHints(
  {
    endpoints: [
      { url: '/_next/static/chunks/runtime.js' },
      { url: 'https://api.example.com/v1/users' },
      { endpoint: '../assets/client.mjs' },
      { url: '/_next/static/chunks/runtime.js' }
    ],
    parameters: [
      { value: '/scripts/loader.js?cache=1' },
      { path: 'chunk-vendors.js' },
      { value: '/api/orders' },
      { url: 'https://cdn.example.com/style.css' }
    ]
  },
  'https://example.com/app/main.js'
);

const resolvedUrls = JSON.parse(JSON.stringify(hints.map((item) => item.resolvedUrl).sort()));
assert.deepEqual(resolvedUrls, [
  'https://example.com/_next/static/chunks/runtime.js',
  'https://example.com/app/chunk-vendors.js',
  'https://example.com/assets/client.mjs',
  'https://example.com/scripts/loader.js?cache=1'
]);

for (const hint of hints) {
  assert.equal(hint.type, 'rep_plus_hint');
  assert.equal(hint.source, 'rep_plus');
}

const summary = bridge.summarize({
  source: 'rep_plus_storage',
  endpoints: [{}, {}],
  secrets: [{}],
  parameters: [{}, {}, {}]
});

assert.equal(summary.source, 'rep_plus_storage');
assert.equal(summary.endpointCount, 2);
assert.equal(summary.secretCount, 1);
assert.equal(summary.parameterCount, 3);

console.log('test_b003_rep_plus_bridge: ok');
