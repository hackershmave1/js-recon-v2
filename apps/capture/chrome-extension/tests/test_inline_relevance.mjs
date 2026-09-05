// D45a — the CONTENT-based inline-script relevance filter. The URL gate can't distinguish an
// inline GTM/analytics bootstrap (or a hydration-data push) from real app code, because the
// synthetic URL is the in-scope page either way — so this filter is the no-noise linchpin.
import assert from 'node:assert';
import { isRelevantInlineScript } from '../modules/inline-relevance.js';

// --- KEEP: real app code / config (endpoints + logic live here) ---
assert.equal(
  isRelevantInlineScript('window.__CONFIG__ = { apiBase: "/api/v1", token: "/oauth/token" };'),
  true,
  'inline app config with endpoints is kept',
);
assert.equal(
  isRelevantInlineScript('(function(){ fetch("/api/session").then(r=>r.json()); })();'),
  true,
  'inline app code making a real call is kept',
);
// An app that has its OWN /api/analytics endpoint must NOT be mistaken for an analytics bootstrap.
assert.equal(
  isRelevantInlineScript('fetch("/api/analytics/report", { method: "POST", body: payload });'),
  true,
  'a first-party /api/analytics call is kept (not a vendor bootstrap)',
);

// --- DROP: third-party analytics / tag-manager bootstraps ---
assert.equal(
  isRelevantInlineScript("(function(w,d,s,l,i){w[l]=w[l]||[];w[l].push({'gtm.start':+new Date()});})(window,document,'script','dataLayer','GTM-ABCD123');"),
  false,
  'GTM bootstrap is dropped',
);
assert.equal(isRelevantInlineScript('window.dataLayer = window.dataLayer || []; function gtag(){dataLayer.push(arguments);} gtag("js", new Date());'), false, 'gtag bootstrap dropped');
assert.equal(isRelevantInlineScript('!function(f,b,e,v,n,t,s){fbq("init","123456");}(window,document);'), false, 'facebook pixel dropped');
assert.equal(isRelevantInlineScript('Sentry.init({ dsn: "https://abc@o1.ingest.sentry.io/1" });'), false, 'Sentry.init dropped');

// --- DROP: serialized hydration DATA, not source (the SPA-route flood source) ---
assert.equal(isRelevantInlineScript('self.__next_f.push([1,"a:[\\"$\\",\\"div\\"]"]);'), false, 'Next.js RSC push dropped');
assert.equal(isRelevantInlineScript('window.__NUXT__ = { data: {}, state: {}, serverRendered: true };'), false, 'Nuxt hydration dropped');

// --- DROP: trivial / non-JS ---
assert.equal(isRelevantInlineScript('var x=1;'), false, 'too-short trivial script dropped');
assert.equal(isRelevantInlineScript('   '), false, 'blank dropped');
assert.equal(isRelevantInlineScript(null), false, 'non-string dropped');
assert.equal(isRelevantInlineScript('a plain sentence with no code tokens at all here'), false, 'non-JS text dropped');

console.log('ok - inline-relevance content filter (D45a)');
