import assert from 'node:assert/strict';
import {
  classifyAsset, isThirdParty, matchesPattern, matchesDenylist, countSecrets
} from '../modules/asset-classifier.js';

// classification
assert.equal(classifyAsset('https://app.acme.io/static/main.4f2a.chunk.js'), 'app');
assert.equal(classifyAsset('https://acme.io/wp-content/plugins/cf7/index.js'), 'cms');
assert.equal(classifyAsset('https://acme.io/wp-includes/jquery.js'), 'cms');
assert.equal(classifyAsset('https://cdn.acme.io/jquery.min.js'), 'lib');
assert.equal(classifyAsset('https://cdn.acme.io/vendors~app.8b1e.js'), 'lib');
assert.equal(classifyAsset('https://www.googletagmanager.com/gtag/js'), 'tracker');
assert.equal(classifyAsset('https://www.google-analytics.com/analytics.js'), 'tracker');

// third-party (same registrable domain → first party)
assert.equal(isThirdParty('https://cdn.acme.io/a.js', 'https://app.acme.io/'), false);
assert.equal(isThirdParty('https://cdn.other.com/a.js', 'https://app.acme.io/'), true);
assert.equal(isThirdParty('https://app.acme.io/a.js', 'https://app.acme.io/'), false);

// glob matcher
assert.equal(matchesPattern('https://x.doubleclick.net/a.js', '*.doubleclick.net'), true);
assert.equal(matchesPattern('https://acme.io/wp-content/x.js', '/wp-content/*'), true);
assert.equal(matchesPattern('https://acme.io/app/x.js', '/wp-content/*'), false);

// denylist (default profile)
assert.equal(matchesDenylist('https://x.doubleclick.net/ad.js', [], true), true);
assert.equal(matchesDenylist('https://acme.io/wp-content/plugins/p.js', [], true), true);
assert.equal(matchesDenylist('https://app.acme.io/main.js', [], true), false);
// custom rule, default profile off
assert.equal(matchesDenylist('https://app.acme.io/track.js', [{ pattern: '*/track.js' }], false), true);

// secret counter — count only, dedupe
assert.equal(countSecrets('const apiKey = "abc123secretvalue";'), 1);
assert.equal(countSecrets('sk_live_' + 'a'.repeat(30)), 1);
assert.equal(countSecrets('const a="x"; const b=2;'), 0);
const jwt = 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N';
assert.equal(countSecrets(jwt) >= 1, true);

console.log('test_asset_classifier: ok');
