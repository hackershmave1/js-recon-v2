// The quarantine queue: a leaf, serial, deduplicated, bounded, best effort (ADR-0010).
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { QuarantineQueue } from '../modules/quarantine-queue.js';

function make(over = {}) {
  const fetched = [];
  const uploads = [];
  const held = [];
  const ignored = [];
  const q = new QuarantineQueue({
    fetchContent: async (url) => { fetched.push(url); return { success: true, content: `// ${url}` }; },
    upload: async (body) => {
      uploads.push(body);
      return { ok: true, data: { files: body.files.map((f) => ({ url: f.url, outcome: 'stored' })) } };
    },
    onHeld: (host) => held.push(host),
    onIgnored: (host) => ignored.push(host),
    ...over,
  });
  return { q, fetched, uploads, held, ignored };
}
const item = (url, host = 'cdn.acme-static.com', runId = 'run-1') => ({ url, host, runId, sessionId: 'ext-1' });

// Fetch, then upload one file for the host; the held callback fires once per stored file.
{
  const { q, fetched, uploads, held } = make();
  assert.equal(q.enqueue(item('https://cdn.acme-static.com/a.js')), true);
  await q.idle();
  assert.deepEqual(fetched, ['https://cdn.acme-static.com/a.js']);
  assert.deepEqual(uploads, [{ runId: 'run-1', sessionId: 'ext-1', host: 'cdn.acme-static.com',
    files: [{ url: 'https://cdn.acme-static.com/a.js', content: '// https://cdn.acme-static.com/a.js' }] }]);
  assert.deepEqual(held, ['cdn.acme-static.com']);
}

// The same url in the same capture is fetched once; another capture (run) may re-hold it.
{
  const { q, fetched } = make();
  assert.equal(q.enqueue(item('https://h/a.js')), true);
  assert.equal(q.enqueue(item('https://h/a.js')), false);
  assert.equal(q.enqueue(item('https://h/a.js', 'h', 'run-2')), true);
  await q.idle();
  assert.equal(fetched.length, 2);
}

// No run (not listening): nothing is queued.
{
  const { q, fetched } = make();
  assert.equal(q.enqueue(item('https://h/a.js', 'h', null)), false);
  await q.idle();
  assert.deepEqual(fetched, []);
}

// Bounded: a page with thousands of scripts cannot grow the queue without limit.
{
  const { q } = make({ maxQueued: 3, fetchContent: () => new Promise(() => {}) });
  const accepted = [1, 2, 3, 4, 5, 6].map((i) => q.enqueue(item(`https://h/${i}.js`)));
  assert.ok(accepted.filter(Boolean).length <= 4, 'one in flight plus the cap');
  assert.equal(accepted[5], false);
}

// Best effort: a failed fetch, a failed upload or a throw never stops the rest and never throws out.
{
  let n = 0;
  const { q, held } = make({
    fetchContent: async (url) => {
      n += 1;
      if (n === 1) return { success: false, error: 'HTTP 404' };
      if (n === 2) throw new Error('boom');
      return { success: true, content: 'x' };
    },
  });
  ['a', 'b', 'c'].forEach((n2) => q.enqueue(item(`https://h/${n2}.js`)));
  await q.idle();
  assert.deepEqual(held, ['cdn.acme-static.com'], 'only the third was held');
}

// The platform refusing an ignored host is reported, not counted as held.
{
  const { q, held, ignored } = make({
    upload: async (b) => ({ ok: true, data: { files: b.files.map((f) => ({ url: f.url, outcome: 'ignored' })) } }),
  });
  q.enqueue(item('https://h/a.js'));
  await q.idle();
  assert.deepEqual(held, []);
  assert.deepEqual(ignored, ['cdn.acme-static.com']);
}

// A leaf: the module never reaches for dependencies, chunks or source maps.
const code = readFileSync(new URL('../modules/quarantine-queue.js', import.meta.url), 'utf8')
  .split('\n').filter((l) => !l.trim().startsWith('//')).join('\n');
assert.ok(!/import /.test(code), 'no imports at all: nothing to follow');
assert.ok(!/dependenc|sourcemap|sourceMap|\.map\b|chunk/i.test(code));

console.log('ok - quarantine queue');
