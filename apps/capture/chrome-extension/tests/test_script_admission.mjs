// The ordered rule for one script request (ADR-0010): a test per rule and per ordering that matters.
import assert from 'node:assert/strict';
import { ADMIT, admitDependency, admitScript } from '../modules/script-admission.js';

const base = { pageKnown: true, pageInScope: true, scriptInScope: false };
const verdict = (over) => admitScript({ ...base, ...over });

// 1) A page outside the scope is not ours, whatever the script is.
assert.equal(verdict({ pageInScope: false }), ADMIT.IGNORE_PAGE);
assert.equal(verdict({ pageInScope: false, scriptInScope: true }), ADMIT.IGNORE_PAGE, 'even an in-scope host');
assert.equal(verdict({ pageInScope: false, thirdPartyFile: true }), ADMIT.IGNORE_PAGE, 'page rule is first');

// 2) Third party: a file pattern always; a host entry never drops a host the researcher scoped.
assert.equal(verdict({ thirdPartyFile: true }), ADMIT.DROP_THIRD_PARTY);
assert.equal(verdict({ thirdPartyFile: true, scriptInScope: true }), ADMIT.DROP_THIRD_PARTY, 'file pattern on a scoped host');
assert.equal(verdict({ thirdPartyHost: true }), ADMIT.DROP_THIRD_PARTY);
assert.equal(verdict({ thirdPartyHost: true, scriptInScope: true }), ADMIT.CAPTURE, 'the researcher typed this host');

// 3) A host in scope is captured and analyzed.
assert.equal(verdict({ scriptInScope: true }), ADMIT.CAPTURE);

// 4) The ignore list: dropped (never fetched), but only after scope and third party.
assert.equal(verdict({ ignored: true }), ADMIT.DROP_IGNORED);
assert.equal(verdict({ ignored: true, scriptInScope: true }), ADMIT.CAPTURE, 'scope outranks the ignore list');

// 5) Page unknown (worker request, tabId -1): never quarantined; in-scope hosts behave as before.
assert.equal(verdict({ pageKnown: false, pageInScope: false }), ADMIT.DROP_UNKNOWN_PAGE);
assert.equal(verdict({ pageKnown: false, pageInScope: false, scriptInScope: true }), ADMIT.CAPTURE);
assert.equal(verdict({ pageKnown: false, pageInScope: false, ignored: true }), ADMIT.DROP_IGNORED);

// 6) Anything else is quarantined.
assert.equal(verdict({}), ADMIT.QUARANTINE);

// "Capture every tab": the researcher said everything is in.
assert.equal(admitScript({ captureEverything: true, pageKnown: true, pageInScope: false, scriptInScope: false }), ADMIT.CAPTURE);

// A dependency child (a URL found inside a captured script) is followed only when the rule captures
// it (DEBT D44a): an in-scope script's `import "https://other.example/x.js"` must not be fetched and
// uploaded. The child inherits the parent's (in-scope) page, so only host and third-party rules apply.
const child = (over) => admitDependency({ scriptInScope: false, ...over });
assert.equal(child({ scriptInScope: true }), ADMIT.CAPTURE, 'an in-scope chunk is still followed');
assert.equal(child({}), ADMIT.QUARANTINE, 'an unknown host is not followed (not CAPTURE)');
assert.equal(child({ ignored: true }), ADMIT.DROP_IGNORED);
assert.equal(child({ thirdPartyHost: true }), ADMIT.DROP_THIRD_PARTY);
assert.equal(child({ thirdPartyFile: true, scriptInScope: true }), ADMIT.DROP_THIRD_PARTY);
assert.equal(child({ thirdPartyHost: true, scriptInScope: true }), ADMIT.CAPTURE, 'the researcher typed this host');
assert.equal(child({ captureEverything: true }), ADMIT.CAPTURE, '"Capture every tab" still follows everything');

console.log('ok - script admission: ordered rule');
