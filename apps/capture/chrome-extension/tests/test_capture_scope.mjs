// capture-scope: the scope a tab's host stands for, the new-site noise filter, and parity of both
// lists with the server's Python lists they mirror.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  SHARED_SUFFIXES, NOISE_HOSTS, apexForHost, takesSubdomains, isNoiseHost, inScope,
} from '../modules/capture-scope.js';

const here = dirname(fileURLToPath(import.meta.url));
const recon = join(here, '..', '..', '..', 'platform', 'src', 'recon');
const quoted = (text) => new Set([...text.matchAll(/"([^"]+)"/g)].map((m) => m[1]));

// Parity: the JS mirrors must equal the Python sources.
{
  const egress = readFileSync(join(recon, 'fetch', 'egress.py'), 'utf8');
  const start = egress.indexOf('_PUBLIC_SUFFIX_DENYLIST = frozenset(');
  const end = egress.slice(start).search(/\r?\n\)\r?\n/);  // CRLF-safe: checkouts differ
  const block = egress.slice(start, start + end);
  assert.deepEqual([...SHARED_SUFFIXES].sort(), [...quoted(block)].sort(), 'SHARED_SUFFIXES drifted from egress.py');

  const noise = readFileSync(join(recon, 'findings', 'noise_hosts.py'), 'utf8');
  const nblock = noise.slice(noise.indexOf('_ANALYTICS = {'), noise.indexOf('DEFAULT_NOISE_HOSTS'));
  const py = new Set([...nblock.matchAll(/^\s+"([^"]+)",/gm)].map((m) => m[1]));
  assert.deepEqual([...NOISE_HOSTS].sort(), [...py].sort(), 'NOISE_HOSTS drifted from noise_hosts.py');
}

// apexForHost: registrable domain, one more label under a shared suffix, exact host otherwise.
assert.equal(apexForHost('app.acme.io'), 'acme.io');
assert.equal(apexForHost('www.acme.io'), 'acme.io');
assert.equal(apexForHost('a.b.acme.io'), 'acme.io');
assert.equal(apexForHost('acme.io'), 'acme.io');
assert.equal(apexForHost('foo.github.io'), 'foo.github.io');
assert.equal(apexForHost('app.acme.co.uk'), 'acme.co.uk');
assert.equal(apexForHost('d1.cloudfront.net'), 'd1.cloudfront.net');
assert.equal(apexForHost('x.y.s3.amazonaws.com'), 'y.s3.amazonaws.com');
assert.equal(apexForHost('127.0.0.1'), '127.0.0.1');
assert.equal(apexForHost('localhost'), 'localhost');
assert.equal(apexForHost('APP.Acme.IO.'), 'acme.io');

// takesSubdomains: never for IPs or single-label hosts.
assert.equal(takesSubdomains('acme.io'), true);
assert.equal(takesSubdomains('10.0.0.1'), false);
assert.equal(takesSubdomains('localhost'), false);

// inScope: an IP scope never matches a different IP that merely ends with it.
assert.equal(inScope('api.acme.io', ['acme.io']), true);
assert.equal(inScope('acme.io.evil.com', ['acme.io']), false);
assert.equal(inScope('10.0.0.1', ['0.0.1']), false);
assert.equal(inScope('127.0.0.1', ['127.0.0.1']), true);

// isNoiseHost: trackers and vendor CDNs never raise the new-site badge.
assert.equal(isNoiseHost('www.google-analytics.com'), true);
assert.equal(isNoiseHost('beta.example'), false);
assert.equal(isNoiseHost(''), false);

console.log('test_capture_scope: ok');
