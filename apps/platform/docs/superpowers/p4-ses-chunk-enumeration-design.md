# P4 — webpack lazy-chunk URL enumeration design spec

Status: **§4 security review done (PROCEED-WITH-CHANGES). Sequencing decided: static-first.**
The active slice is pure-Python static template emulation (no execution, no posture change).
The `ses`/Node sandboxed-execution engine is **deferred** to its own hardening slice gated on
six must-fixes — tracked as **DEBT D29**.

## Decision & sequencing (2026-08-20)

Goal: close the endpoint-recall gap on static webpack/Next.js bundles — the lazy-loaded chunks
whose URLs are *computed at runtime* (and which hide additional API calls) are invisible to a
static pass and to katana. `js-recon` recovers them by *executing* the bundle's chunk-URL builder
inside a SES sandbox.

The user approved sandboxed execution as a posture change, then — after the §4 review showed the
true cost of doing it safely — chose to **sequence it behind a static-only slice**:

1. **Active — static template emulation (this spec, below).** Statically parse the standard
   webpack `__webpack_require__.u` chunk-URL builder + inline chunk→hash map and reconstruct the
   URLs by pure string substitution in Python. Covers the common case with **zero new attack
   surface and no posture change**.
2. **Deferred — Node-under-nsjail SES engine (DEBT D29).** Executes *arbitrary/obfuscated*
   builders that static substitution can't fold. Needs real OS isolation + the six §4 must-fixes
   + an ADR-0006 posture amendment before any exec-path code. The static extractor below is the
   input it reuses, so this is not throwaway.

## Active slice — static webpack chunk-URL enumeration (no execution)

### Approach
Standard webpack 5 runtime shape (minified, no-map):
`r.u = e => "static/chunks/" + e + "." + {100:"a1b2",200:"c3d4"}[e] + ".js"`, with the public
path in `r.p` and chunk ids also visible at `r.e(<id>)` call sites. Statically:
- locate the `.u` (and CSS variant `.miniCssF`) builder assignment,
- extract the literal template parts + the inline `{id:hash}` map + the chunk-id set,
- reconstruct each URL by **pure string concatenation** (mirror `_jsast._collapse_url`
  semantics — never `_join_base`, which fabricates slashes), prefixing `r.p` when present.

Exact minified shapes are pinned against the real recon-range webpack-nomap runtime chunk during
TDD (the fixture already ships a lazy `orders.js` chunk).

### Files
- **New** `findings/chunkenum.py` — static extraction + substitution (pure; no I/O, no exec).
- **New** `findings/chunkenum_test.py` — colocated fast-lane tests.
- **Touched** `findings/analyze.py` — invoke chunkenum during the bundle pass; hand enumerated
  URLs to the discovery seed path.
- **Touched** discovery seed path (`discover/`) — accept a new "chunk-enum" URL source, capped,
  routed through the same `egress.validate_target` re-validation as katana-discovered URLs.

### Security / honesty invariants (carry over from the §4 review — they apply to static too)
- **Route enumerated URLs exclusively through `seed_pending → _fetch_assets → _fetch_hops`**, so
  every hop re-runs `egress.validate_target` (ADR-0005). Content-derived URLs therefore cannot
  widen scope, and `data:`/`file:`/userinfo are rejected. Never fetch a chunk URL directly.
- **Cap enumerated count + per-URL length before seeding.** The inline chunk map is
  content-derived; a hostile/huge map must not flood the fetch queue. Reuse the crawl asset
  ceiling (`crawl_max_assets`) or a dedicated cap.
- **Fail-safe:** an unrecognized / computed / non-foldable builder enumerates **nothing** — no
  invented URLs. An out-of-scope enumerated URL is dropped by the guard, not fetched.

### Tests
- Fast-lane unit: extractor on representative minified `.u` shapes (with/without `r.p`, CSS
  variant); substitution correctness; fail-safe on a computed builder; cap enforcement.
- Integration: the real recon-range webpack-nomap build → the lazy chunk URL is enumerated and
  its endpoints picked up (score/harness caveat: no-map builds aren't scored, so assert via the
  analyze path, mirroring the cross-chunk slice's approach).

### Config / default
Respect the existing discovery caps; enable for analyze/capture of webpack bundles by default but
bounded by the cap above (static + guarded = low risk). No feature flag needed for the static path
(the flag belongs to the deferred exec engine).

## Deferred — Node-under-nsjail SES exec engine (DEBT D29)

Executes the extracted builder in a hardened Node subprocess (SES `lockdown` + zero-endowment
`Compartment`) via `findings/engines.py::run_engine`. The §4 review (verdict PROCEED-WITH-CHANGES)
established that this is **not** a `run_engine` drop-in like Kingfisher/Sourcemapper (those feed
*trusted* inputs to *trusted* binaries; this executes attacker code). Six must-fixes gate any
exec-path code — see DEBT D29 for the full contract:
1. network namespace, no interfaces (the real no-traffic guarantee; SES is JS-level only);
2. kill the whole process tree (`start_new_session` + `os.killpg`, or nsjail reaps);
3. explicit minimal env (`run_engine` defaults to inheriting the worker's secret-bearing env);
4. memory + pids caps (SES "does not protect availability"; the output cap is post-hoc);
5. cap enumerated URL count + length before seeding;
6. pin the whole `@endo` tree with integrity; SES is defense-in-depth, never the sole boundary.
Plus an ADR-0006 posture amendment (local sandboxed execution of extracted target code, no-network
sandbox) + an ADR-0005 note, and its own §4 gates.

## §4 review trail
Adversarial security design review (2026-08-20, opus subagent, evidence-backed): VERDICT
PROCEED-WITH-CHANGES. 2× CRITICAL (env-inheritance secret exfiltration; "no traffic" false without
an OS netns given `engines.py:15-20` grandchild-survives-timeout), 2× HIGH (availability/DoS;
uncapped enumerated URL list), MED (SES correctness-dependence + `@endo` pinning), and one SAFE
axis (no scope-widening, via `egress.validate_target`). LOW factual corrections folded: the `.u`
extractor is net-new (not a `_modulegraph` export-index reuse); the exec engine is not a
`run_engine` drop-in. Full findings in DEBT D29 + the review's must-fix list.
