# Thorough endpoint recovery — design (js-recon parity)

Status: §4 adversarial design review DONE (PROCEED-WITH-CHANGES on Phases 1–2). User decision
2026-08-21: build ALL THREE phases including Phase 3 SES execution — hardened (see below).
Phase 1 BUILT + verified on real sites. Date: 2026-08-21.

## Why

QA against three real production webpack sites (Asana in depth, Figma + Stripe
corroborating) proved our shipped webpack enhancements make **zero** difference on real
bundles:

- P4 chunk enumeration → 0 URLs (real Next.js/webpack writes `__webpack_require__.u` as a
  **ternary chain** `id===e?"…":…`, not the `{id:"hash"}[e]` subscript-map we fold).
- 2b cross-chunk resolver → 0 modules harvested (real webpack 5 emits **arrow**
  `{584:(e,t,n)=>{…}}` / **function-expression** module registries, not the
  method-shorthand `{389(e,t,n){…}}` our `_modulegraph` matches).
- Every real unresolved sink on Asana (18/18) is a *parameter* URL — `fetch(o,…)`,
  `c.open("GET",a,…)`, `fetch(t.build("fetch",r))` — not `fetch(BASE+PATH)`.

Root cause: the resolver + enumerator were validated against a synthetic fixture whose
shapes (method-shorthand modules, subscript-map `.u`, sink-site concat) don't match real
webpack. Benchmark for the target benefit: [js-recon](https://github.com/js-recon/js-recon).

## What js-recon actually does (source read; corrects the initial read)

- **Chunk enumeration is STATIC.** `lazyLoad/shared/webpackChunkParsers.ts::extractWebpackChunkUrls`
  (:303) runs four parsers: `extractObjectMapChunkEntries` (:11, numeric `{id:"name"}[e]||e`),
  `extractIfChainChunkFilenames` (:71, block-body `if(id===e)return"x.js"`),
  `extractStringKeyedChunkMap` (:181, `"a/"+id+"."+{name:hash}[id]+".js"`) — all pure Babel
  AST — plus `extractExpressionBodyChunkFilenames` (:126) which is the ONLY SES user and only
  for expression-body arrows ending in `".js"`, executed per integer literal *scraped from the
  arrow's own source* (`source.match(/\d+/g)`, :148) — a lazy shortcut that also emits bogus
  hash-digit URLs. **None of the four is a static ternary parser**; the ternary (Asana/Figma/
  Stripe) falls to the SES shortcut only because they never wrote a static ternary folder.
- **Taint-flow** (`analyze/helpers/engineHelpers/taintFlow.ts`) is a *boolean* source→sink
  reachability engine for client-side VULN rules (`sinkConsumesTaint` :233 returns bool) — it
  does NOT reconstruct URL strings. It is not their endpoint-URL resolver.
- SES helper (`utility/runSandboxed.ts`): `lockdown()` + bare `Compartment({console})`, no
  cpu/mem/time bound — fine on a local operator box; NOT for a multi-tenant server.
- puppeteer-extra + stealth headless load (`lazyLoad/*`) triggers dynamic chunks; sourcemap
  recovery; babel/esquery AST.

## §4 review outcome + user decision (folded)

- **Phases 1–2 = PROCEED-WITH-CHANGES** (folded below). These reach js-recon endpoint-recovery
  parity with PURE STATIC analysis — the review proved js-recon's SES use is a narrow shortcut
  for shapes we fold statically (a static ternary/if-chain folder is *more precise* — no bogus
  hash-digit URLs). Execution is NOT required for parity.
- **Phase 3 (SES sandbox execution) — user decision (2026-08-21): BUILD IT, hardened.** The
  review's RECONSIDER stands as a HOW, not a whether: js-recon's `lockdown()`+bare `Compartment`
  (no cpu/mem/time bound) is unsafe on our multi-tenant, secret-bearing worker (SES = language
  boundary not OS; memory/CPU DoS unmitigated per endojs/ses; `engines.py:88` `env=None`
  inherits worker secrets). So Phase 3 is built the HARDENED way (OS sandbox, not SES-alone),
  sequenced LAST (after Phases 1–2 ship), and owes its own design spike + §4 security gate +
  ADR-0006 posture amendment BEFORE any exec code. Marginal recall over static: only genuinely
  obfuscated/computed builders (which js-recon itself doesn't reliably handle either).

## Phase 1 — real webpack shapes (static, safe) — FOUNDATION

Parity with js-recon's four static shapes + the module-registry forms. Verified node types
(live tree-sitter probe): ternary = `ternary_expression`(condition/consequence/alternative);
`===` test = `binary_expression` op `===`; arrow/fn-expr module = `pair(key=number,
value=arrow_function|function_expression)`.

- `findings/chunkenum.py`: fold the **ternary chain** `id===e?"url":…` AND the block-body
  **if-chain** `(e)=>{if(id===e)return"url";…}` into the existing id→url map, alongside
  today's subscript-map/param-concat forms.
  - F9a: accept the constant on **either** side of `===` (`id===e` and `e===id`).
  - F9b: **drop the default/catch-all arm** (trailing `:"x"`, `return e+".js"`, `||e`) — it
    is not tied to a specific id; emitting it would invent a URL for an unmatched id
    (breaks the "never a guessed URL" contract). Only ids with an exact `===`/map-hash fold.
  - Iterate the chain (no recursion) + reuse the `_MAX_URL_SPAN` body cap + a chain-arm cap.
- `findings/_modulegraph.py`: `_webpack_module_defs` also yields numeric-keyed **arrow** and
  **function-expression** property modules, not just `method_definition`. The `require.d`
  export gate + build-scoped ids + poison-safe aliases operate on the body, agnostic to the
  declaration form → no new FP risk (F8).
- F11a: `_enumerate_and_seed_chunks` already re-runs `egress.validate_target` per hop and is
  asset-capped — but now that enumeration actually fires, confirm the per-run seeded-chunk
  cap holds (fan-out amplification, still SSRF-safe).
- Verify on the REAL Asana/Figma/Stripe runtimes: enum > 0, modules harvested > 0.

## Phase 2 — bounded URL constant-propagation (static, safe) — THE RECOVERY LEVER

NOT a `taintFlow.ts` port (that's boolean reachability, F5). A NARROW, honest constant/
value-propagation pass so a sink whose URL is an indirected value resolves to its constructed
string — targeting the real shapes: local single-assignment `const u="…"; fetch(u)`; a URL
built then passed to one request wrapper; a member/const already in `CrossModuleIndex`.

- Honesty (F6): tree-sitter has no scope API; we already use the blunt `_declared_names`
  poison set + `_base_env` *deliberately excludes same-file local consts today*. Phase 2 only
  resolves a value when its binding is **single + unshadowed** (poison-safe); anything
  ambiguous stays `unattributed`. **Re-prove 0-FP on the 2.7 MB real-minified corpus** — do
  not assume it. Reversing the "local consts excluded" guard is itself a tracked design change.
- DoS (F7): land DEBT D21's **one-shot AST work budget** in `extract()` as a prerequisite;
  **index once, resolve by lookup — no per-sink re-traversal** (js-recon's 8 passes + per-sink
  pass is O(n²); forbidden here). Reuse span-cap discipline (avoid `node.parent`/`node.text`
  blowups per the DoS-hardening history).
- New `findings/_dataflow.py`; plugs into `_base_env._resolve_url` as an extra resolver lane.
- **Quantify recall honestly** on the cited 18/18 Asana sinks before claiming success — some
  (`fetch(t.build("fetch",r))`) are builder-method calls that stay unresolved by design.

## Phase 3 — SES sandbox execution engine (EXECUTION, hardened) — LAST, GATED (DEBT D29)

js-recon's `execFunc(builderSource, chunkId)` shape, for the residual builders static folding
can't crack (obfuscated / computed `.u`). Built the HARDENED way, because it executes
adversary-controlled bundle code while our worker is multi-tenant + secret-bearing:

- **OS-level isolation, NOT SES-alone.** A disposable sandbox with a **PID namespace** (whole-tree
  reap — `setsid`+`killpg` is insufficient, a grandchild `setsid()` escapes it; model
  `discover/harness.py`, not `engines.py`), a **network namespace with no interfaces** (the real
  no-egress guarantee), and a **cgroup `memory.max` + pids limit** (`--max-old-space-size`/`ulimit
  -v` bound neither RSS nor CPU/time). SES `lockdown()` is defense-in-depth INSIDE that, never the
  sole boundary.
- **Minimal explicit env** — never inherit the worker's secret-bearing env (`engines.py:88`
  `env=None` is the anti-pattern). Pin the whole `ses`/`@endo` tree with integrity hashes.
- Execute ONLY the extracted `.u` builder function (not the whole bundle), time-boxed, over a
  bounded id set; **cap enumerated URL count + length**; every result still routes through
  `egress.validate_target` (content can't widen scope).
- **Posture change:** we become a tool that executes untrusted target code server-side → an
  ADR-0006 amendment + ADR-0005 note, and its OWN §4 adversarial security gate BEFORE any exec
  code. On multi-tenant SaaS, gate behind a deploy-time flag / single-tenant deployment; static
  Phases 1–2 remain the default path.

## Invariants (all phases)
- Never emit a guessed URL — uncertain → `unattributed` (REQ-C2). 0 FP on 2.7 MB real code
  today; must stay 0 (re-proved, not assumed).
- DoS caps on every walk; one-shot work budget for Phase 2. Untrusted input, shared worker.
- Derived/enumerated URLs are UNTRUSTED → every fetch re-validates via `egress`; scope never
  from content. No posture change (static only).

## Verification
Each phase proven on the REAL Asana/Figma/Stripe bundles (old→new delta) + fast-lane unit
tests + recon-range fixture extended with real-shape variants (fixture-parity bug is itself
tracked). §4 code review (gate 2) before merge.
