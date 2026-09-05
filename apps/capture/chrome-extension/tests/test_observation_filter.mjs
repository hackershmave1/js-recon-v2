// D45b1 — the pure filter/normalizer for runtime API-call observations that confirm endpoints.
import assert from 'node:assert';
import { normalizeObservedUrl, isApiIshObservation, isTelemetryPath } from '../modules/observation-filter.js';

// --- normalizeObservedUrl: scheme://host/path, query + fragment dropped (matcher's shape) ---
assert.equal(normalizeObservedUrl('https://api.acme.io/v1/users?page=2#x'), 'https://api.acme.io/v1/users');
assert.equal(normalizeObservedUrl('http://app.test/graphql'), 'http://app.test/graphql');
assert.equal(normalizeObservedUrl('https://api.acme.io:8443/x/y'), 'https://api.acme.io:8443/x/y', 'keeps a non-default port');
// Non-http(s) or junk → null (never recorded).
assert.equal(normalizeObservedUrl('ws://api.acme.io/socket'), null);
assert.equal(normalizeObservedUrl('data:application/json,{}'), null);
assert.equal(normalizeObservedUrl('not a url'), null);

// --- isApiIshObservation: keep JSON/GraphQL/text/form/absent; drop media/font/css ---
assert.equal(isApiIshObservation('application/json'), true);
assert.equal(isApiIshObservation('application/graphql'), true);
assert.equal(isApiIshObservation('text/plain; charset=utf-8'), true);
assert.equal(isApiIshObservation(''), true, 'no content-type still counts as an API call');
assert.equal(isApiIshObservation(null), true);
assert.equal(isApiIshObservation('image/png'), false);
assert.equal(isApiIshObservation('font/woff2'), false);
assert.equal(isApiIshObservation('text/css'), false);
assert.equal(isApiIshObservation('video/mp4'), false);

// --- isTelemetryPath: drop same-origin beacons, KEEP real app endpoints ---
assert.equal(isTelemetryPath('https://app.test/collect'), true);
assert.equal(isTelemetryPath('https://app.test/v1/beacon'), true);
assert.equal(isTelemetryPath('https://app.test/api/sentry-tunnel'), true);
assert.equal(isTelemetryPath('https://app.test/gtm/config'), true);
// Real endpoints that merely resemble telemetry words must be KEPT (narrow filter).
assert.equal(isTelemetryPath('https://app.test/api/users'), false);
assert.equal(isTelemetryPath('https://app.test/api/metrics-dashboard'), false, '/metrics-dashboard is a feature, not a beacon');
assert.equal(isTelemetryPath('https://app.test/tracks/42'), false, '/tracks (music app) is not /track beacon');

console.log('ok - observation-filter (D45b1)');
