// D45b1 — runtime request observation wiring (endpoint confirmation). background.js +
// workspace-client are `chrome`-coupled, so these are structural source assertions; the pure
// filter/normalizer is covered behaviourally in test_observation_filter.mjs.
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const bg = fs.readFileSync(path.resolve(__dirname, '../background.js'), 'utf8');
const wc = fs.readFileSync(path.resolve(__dirname, '../modules/workspace-client.js'), 'utf8');

// --- background: an xmlhttprequest observer that records { method, url } ONLY ---
assert.match(bg, /recordObservation\(details\)[\s\S]*?types:\s*\["xmlhttprequest"\]/, 'registers an xhr onCompleted observer → recordObservation');
assert.match(bg, /recordObservation\(details\)\s*\{/, 'has a recordObservation handler');
// Client-side gate (server ingest scope is inert for pre-fetched captures): scope + denylist +
// telemetry-path + api-ish content-type.
assert.match(bg, /recordObservation[\s\S]*?this\.isInScope\(rawUrl\)/, 'observation scope-gated client-side');
assert.match(bg, /recordObservation[\s\S]*?this\.shouldSkipUrl\(rawUrl/, 'observation denylist-gated');
assert.match(bg, /recordObservation[\s\S]*?isTelemetryPath\(rawUrl\)/, 'observation drops same-origin telemetry paths');
assert.match(bg, /recordObservation[\s\S]*?isApiIshObservation\(/, 'observation drops non-API content-types');
// The observation base is method+url; a request body (D45b2) is attached separately when present.
assert.match(bg, /const obs = \{ method, url \};/, 'observation base is method+url');
assert.match(bg, /this\.observations\.push\(obs\)/, 'observation is recorded');
// Deduped + capped + durable across an MV3 teardown.
assert.match(bg, /observationKeys\.has\(key\)/, 'observations deduped by method+url');
assert.ok(bg.includes('OBSERVATION_CAP'), 'observations are capped');
assert.match(bg, /await this\.rehydrateObservations\(\)/, 'observations rehydrated on init (survive teardown before Analyze)');
assert.match(bg, /from '\.\/modules\/observation-filter\.js'/, 'imports the observation filter');

// --- workspace-client: observations ride the analyze/start body ---
assert.match(wc, /getObservations/, 'workspace-client accepts a getObservations dep');
assert.match(wc, /observations:\s*this\.getObservations\(\)/, 'analyze/start body includes observations');

console.log('test_observation_capture: ok');
