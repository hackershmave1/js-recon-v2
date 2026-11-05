# Telling third-party library code apart from the target app's own code

> Research note for issue #180, under map #174. Status: findings and a recommendation, nothing built.
> Date: 2026-11-05. Base: `main` @ `4ab4ea0`.
> Placeholders: `acme.io` / `@acme/*` stand in for any real target.

## Question

A capture's Sources tree mixes the target app's code with dependency code. For example, source-map
originals under `node_modules/.pnpm/@module-federation+runtime-core@…/dist/…` and
`node_modules/@statsig/client-core/src/*.js`, plus standalone files like `analytics.min.js`.
**How can we tell, offline and reliably, which code is the target's own and which comes from
third parties, so findings come from the core app?**

## TL;DR

1. **The best signal is the source-map `sources` path, and we already have it on every
   finding** (`FindingOccurrence.source_path`). When a map exists, analyze runs only on the
   recovered originals, not the bundle. Every major bundler keeps `node_modules` as a path
   segment, and that segment survives Sourcemapper's path rewriting.
2. **`ignoreList` is the specification's own "this is third-party" flag.** Rollup and Rolldown
   (which Vite builds use) fill it with `node_modules` paths by default. We never read it, because
   Sourcemapper drops the field. It is cheap to read from the map JSON ourselves, but it can't be
   the only signal: webpack emits it only when configured.
3. **Library fingerprints (retire.js, our enthec dataset) identify *which* library a page uses.
   They can't tell *which code* is library code.** retire.js file hashes match only unmodified,
   whole library files, and our tech-detect runs per host, not per file.
4. **Host lists (`noise_hosts`, capture scope) already handle CDN-served libraries** (the script's
   URL is on another host). They don't help with bundled code, which is the case that matters.
5. **Chunk names and webpack module ids are weak signals.** Production defaults use hashed or
   numeric ids. Treat them as fallbacks for bundles with no map.
6. **"Dependency" doesn't mean "not the target's code."** Private `@acme/*` packages and API SDKs
   live in `node_modules` too, so classify findings and show them lower. Don't delete them, and let
   an in-scope host override the classification (the same rule `visibility.py` already follows).

```
                      ┌────────────────────────── has a source map? ──────────────────────────┐
                      │ yes (originals analyzed)                                     no (bundle) │
                      ▼                                                                         ▼
   T1  map.ignoreList[i]  ─┐                                         T4  served from off-scope /
   T2  sources[i] path has  ├─▶ origin = "dependency"                    noise host? (already done)
       node_modules / .pnpm │      (+ package name/version parsed       T5  webpack module boundary +
       / .yarn / bower_…   ─┘       from the path)                          license banner / retire
   T3  else ─────────────────▶ origin = "app"                               filecontent ⇒ "dependency?"
                                                                         else ─▶ origin = "unknown"
                      │
                      ▼
   read-time overlay (like visibility.py): demote dependency findings by default,
   UNLESS an attributed host is first-party (an SDK calling acme.io's API stays shown)
```

## What the repo already does (primary source: this repo)

| Mechanism | Where | What it covers | Gap for this question |
|---|---|---|---|
| Recovered originals become the analysis units, and each finding records its original's path | `apps/platform/src/recon/findings/analyze.py` ~L980–1000 (`AnalysisUnits(names=…)`); `sourcemapper.py` `RecoveredFile.path` ("used as the finding path"); `db/models.py` `FindingOccurrence.source_path` | Every finding from a mapped bundle already knows its original file path | Nothing reads that path to classify origin. `coverage.files` explicitly counts "vendor libraries included" (`queries.py` `files_collected` docstring; `web/src/features/overview/OverviewPanel.tsx:68`) |
| Third-party analytics, telemetry and CDN **host** denylist | `findings/noise_hosts.py` (`_VENDOR_CDN` includes `cdnjs.cloudflare.com`, `unpkg.com`, `jsdelivr.net`…) | Hides a finding when **every** attributed host is a vendor host | Works on the host a finding points at, not on the code it came from. A Statsig SDK file calling `acme.io` isn't caught, and shouldn't be |
| Read-time, reversible visibility overlay | `findings/visibility.py` (`why_hidden`: `noise` / `junk_or_dynamic` / `off_scope`) | The single place every list and count surface asks "is this shown?" | No `origin` reason. This is where a "dependency" reason would go |
| Capture drops scripts **served** from off-scope hosts | `capture/stage.py` ~L261 `_in_scope` ("third-party CDNs") | CDN-hosted `<script src=https://cdn…/lib.js>` | Doesn't apply to code bundled into first-party chunks. DEBT notes that dependency-child chunks bypass the scope gate (DEBT.md ~L359) |
| Per-build webpack scoping | `findings/_modulegraph.py` `webpack_build_id` (the `webpackChunk<uniqueName>` global) | Keeps a third-party webpack widget's module ids apart from the app's | Separates **builds**, not app code from library code inside one build |
| Tech detection (enthec/webappanalyzer, pinned commit `1b9eee8…`) | `findings/techdetect/`, `techdetect_pass.py` ("per host") | Names the technologies a host uses | The output is per `(run, host, name)` in `run_technology`. It never says which **file or finding** is library code |
| Doc and demo hosts baked into vendored bundles | `findings/extract.py` ~L335 (`reactjs.org`, `redux.js.org`…) | Drops library doc URLs as junk | The same idea at host granularity |

## Approaches surveyed

### A. Source-map `sources` path (the strongest signal)

**What the spec says.** `sources` is "a list of original sources used by the mappings field … a
(potentially relative) URL". `sourceRoot` "is prepended to the individual entries in the sources
field" ([ECMA-426 §9](https://tc39.es/ecma426/)).

**What bundlers write:**
- webpack: the default `moduleFilenameTemplate` is `webpack://[namespace]/[resourcePath]`
  ([`lib/devtool/ModuleFilenameHelpers.js` L28–29](https://github.com/webpack/webpack/blob/main/lib/devtool/ModuleFilenameHelpers.js)).
  `devtoolNamespace` defaults to `output.uniqueName`
  ([`lib/config/defaults.js` L2149](https://github.com/webpack/webpack/blob/main/lib/config/defaults.js)).
  The resource path is project-relative, so a dependency comes out as
  `webpack://<ns>/./node_modules/<pkg>/…`.
- The package-manager layout is kept in that path. pnpm puts files at
  `node_modules/.pnpm/<name>@<version>/node_modules/<name>/…`
  ([pnpm: symlinked node_modules structure](https://pnpm.io/symlinked-node-modules-structure)).
  This matches the `@module-federation+runtime-core…` path in the capture: pnpm encodes the `/` of a
  scoped package as `+`. Yarn PnP keeps packages "within their zip archives" in `.yarn/cache`
  ([Yarn lexicon](https://yarnpkg.com/advanced/lexicon)), so paths contain `.yarn/`.

**What we receive after Sourcemapper.** The pinned Sourcemapper (`442aed2…`) does no `webpack://`
or `sourceRoot` handling. It prefixes `/`, strips `?%*|:"<>` (`cleanWindows`), runs
`filepath.Clean`, and writes `sourcesContent[i]`
([denandz/sourcemapper `main.go` @442aed2](https://github.com/denandz/sourcemapper/blob/442aed28d1841f32580dda91b4bea740c07bd2ad/main.go)).
So `webpack://acme/./node_modules/x/y.js` becomes `webpack/acme/node_modules/x/y.js`. **The
`node_modules` segment survives**, and a segment match on the stored `source_path` works without
re-reading the map.

**Accuracy and failure modes:**

| Case | Result | Why |
|---|---|---|
| npm, Yarn classic or pnpm dependency | ✅ correct | `node_modules` segment present |
| pnpm package name and version | ✅ recoverable | `.pnpm/<name>@<ver>` segment, so we can report "statsig client-core 3.x" |
| Yarn PnP | ⚠️ needs a `.yarn/` rule | no `node_modules` segment |
| Monorepo workspace package (`packages/ui`) | ✅ correctly "app" | webpack's default `resolve.symlinks: true` resolves to the real path, so no `node_modules` (convention; verify per bundler) |
| Private first-party package `@acme/api-client` in `node_modules` | ❌ wrongly "dependency" | the path can't tell first-party scope from third-party. **This is why the next step must demote, not delete.** Mitigation: a per-engagement "first-party package scopes" list (`@acme/*`) |
| Library copied into the app tree (`src/vendor/analytics.min.js`, `public/lib/…`) | ❌ wrongly "app" | needs approach C (content fingerprint) or a `vendor/`/`lib/`/`*.min.js` heuristic (weak) |
| Bundler runtime / virtual modules (`webpack/runtime/…`, `\0`-prefixed Rollup virtual ids) | ⚠️ neither | treat as "bundler", not app |
| `sourceRoot` set, or a custom `devtoolModuleFilenameTemplate` / `sourcemapPathTransform` | ⚠️ path may be absolute or rewritten | a segment match still works if `node_modules` remains anywhere in the path |
| Map has `sources` but no `sourcesContent` | n/a | nothing is recovered, and analyze falls back to the bundle (`analyze.py` "no sourcesContent") |

### B. Source-map `ignoreList` (the spec's own third-party flag)

ECMA-426 §9: "The `ignoreList` field is an optional list of indices of files that should be
considered third party code, such as framework code or bundler-generated code", with the
deprecated `x_google_ignoreList` as a fallback ([ECMA-426](https://tc39.es/ecma426/)).

Who emits it:
- **Rollup**: when `output.sourcemapIgnoreList` isn't specified, "by default it will put all
  files with `node_modules` in their path on the ignore list"
  ([Rollup options](https://rollupjs.org/configuration-options/)). Vite's production build goes
  through Rolldown/Rollup output options
  ([Vite build options](https://vite.dev/config/build-options): `build.rollupOptions` is an alias
  of `build.rolldownOptions`). Vite's *dev-server* `server.sourcemapIgnoreList` has the same
  `node_modules` default but "doesn't get its default value from the defined Rolldown options"
  ([Vite server options](https://vite.dev/config/server-options)). Assumption to verify on a
  fixture: a Vite production map carries `ignoreList` through Rolldown's default.
- **webpack**: `SourceMapDevToolPlugin` writes `ignoreList` **only if** the `ignoreList` option
  is configured ([`lib/devtool/SourceMapDevToolPlugin.js` L752–770, L837–838](https://github.com/webpack/webpack/blob/main/lib/devtool/SourceMapDevToolPlugin.js)).
  It is off by default.

**We currently lose it.** Sourcemapper's struct has no `ignoreList` field, so Go's JSON decoder
drops it ([`main.go` @442aed2](https://github.com/denandz/sourcemapper/blob/442aed28d1841f32580dda91b4bea740c07bd2ad/main.go)).
We still have the map file (`analyze.py` streams it to `in.map`), so reading `ignoreList` plus
`sources` ourselves is a small JSON read. It needs to stream for the maps of 10 MiB and more (D37-L2).

- ✅ Author intent, as defined by the spec, with no heuristics.
- ⚠️ Present only for Rollup/Vite builds by default. It also marks "bundler-generated code" as
  third-party. Use it as corroboration of A, not as a replacement.

### C. Known-library content fingerprints (retire.js-style)

retire.js `jsrepository-v4.json`
([RetireJS/retire.js `repository/jsrepository-v4.json`](https://github.com/RetireJS/retire.js/blob/master/repository/jsrepository-v4.json))
has per-library extractors: `uri`, `filename`, `filecontent` (e.g. `/\*!? jQuery v(§§version§§)`),
`filecontentreplace`, `hashes`, `func` and `ast`. Measured on the current file: **76 libraries,
71 with `filecontent` rules, but only 4 with `hashes` (18 hashes in total)**. The library set
is aimed at vulnerable legacy libraries (jQuery family, Angular 1, Dojo…). It has no statsig and no
module-federation entry.

- File hashes identify only **byte-identical, whole** library files. A bundled module is
  re-minified and concatenated, so hashes almost never match inside a bundle.
- `filecontent` banners work when the minifier keeps legal comments. terser-webpack-plugin's
  default `extractComments: true` **moves** `/^\**!|@preserve|@license|@cc_on/i` comments to
  `[file].LICENSE.txt`
  ([terser-webpack-plugin README](https://github.com/webpack/terser-webpack-plugin)). So the banner
  is usually *gone from the chunk*, and a sibling `<chunk>.js.LICENSE.txt` lists which libraries
  are inside without saying where they sit. That file is a useful signal that a chunk contains
  these libraries, but it isn't a code-region boundary.
- Our enthec dataset (pinned `1b9eee8…`): 7586 technologies, 3871 with `scriptSrc` (URL) patterns,
  785 with `scripts` (content) patterns, 3328 with `js` (window-global) patterns. Statsig is detected
  only via globals like `__STATSIG__`. This tells you **whether a host uses** a library, not which
  lines belong to it, and it runs per host (`techdetect_pass.py`).

Verdict: ✅ good for naming a library and its version, and for map-less standalone files like
`analytics.min.js` (a `filecontent` or `filename` match). ❌ Not a reliable separator for bundled
code.

### D. Host lists (where the script is served from)

The capture scope gate (`capture/stage.py` `_in_scope`) and `noise_hosts._VENDOR_CDN` already
cover libraries served from a CDN or another host. ✅ Precise for that case. ❌ Bundled
dependencies are served from the target's own host, so host lists can't see them. Also, findings
are hidden by the host they *point at*, which is a different question from where the code *came
from*.

### E. Bundler chunk naming and webpack module ids (fallback when there's no map)

- webpack's default `splitChunks.cacheGroups.defaultVendors.test` is `/[\\/]node_modules[\\/]/`,
  but production chunk naming defaults to `name: false`, and automatic names look like
  `vendors-main.js` ([SplitChunksPlugin](https://webpack.js.org/plugins/split-chunks-plugin/)).
  `optimization.chunkIds` / `moduleIds` default to `'deterministic'` (hashed numeric) in
  production and `'named'` in development
  ([optimization](https://webpack.js.org/configuration/optimization/)). So readable
  `node_modules_…` chunk or module names show up only in dev builds or with custom config.
- What we can still use: a map-less webpack chunk is a `{id: function(module, exports, require){…}}`
  table. We already parse it (`_modulegraph.py`), so a **module** is a real boundary, and a module
  whose body matches a retire.js `filecontent` or `ast` rule can be tagged "dependency?".
- ✅ Gives per-module granularity without a map. ⚠️ Low confidence: names and ids are hashed,
  library detection depends on the fingerprint DB's coverage, and Vite/Rollup scope-hoist modules
  into one scope, so there's no module table at all.

## Comparison

| Approach | Granularity | Offline | Precision | Recall (typical SPA) | Cost to adopt here |
|---|---|---|---|---|---|
| **A. `sources` path segment** | per original file | ✅ | high (fails on private `@acme/*` and copied-in vendor files) | high whenever a map exists | **tiny**: classify the stored `source_path` |
| **B. `ignoreList`** | per original file | ✅ | very high (author-declared) | Vite/Rollup only by default | small: read 2 fields from the map JSON |
| C. retire.js / enthec fingerprints | per library (or per whole file) | ✅ (vendored DB) | high for named versions | low inside bundles | medium: vendor and refresh another DB |
| D. host lists | per served script | ✅ | high | CDN-served libraries only | done already |
| E. chunk names / module ids | per chunk / module | ✅ | low | low in production | medium |

## Recommendation

1. **Add a derived `origin` classification** with values `app`, `dependency`, `bundler` and
   `unknown`, computed at read time from `FindingOccurrence.source_path` (approach A). Use a segment
   match on `node_modules`, `.pnpm`, `.yarn`, `bower_components`, `jspm_packages`, and parse
   `<name>@<version>` where available. Like `visibility.py`, keep it read-time and reversible:
   nothing is deleted and no re-analysis is needed. That lets it apply retroactively to runs we
   already have.
2. **Corroborate with `ignoreList`** (approach B) by reading `sources` and `ignoreList` from the
   map JSON during analyze, because Sourcemapper discards them. An index in `ignoreList` means
   `dependency` even when the path lacks `node_modules`. This catches custom path templates.
3. **Demote, don't hide outright.** Add a `dependency` reason to `why_hidden` (or a separate
   filter chip) that **yields to scope**: if any attributed host is first-party, the finding stays
   shown. This is the same "a third-party API call the app makes is real attack surface" rule the
   module already documents. Add a per-engagement first-party package-scope list (`@acme/*`) so
   private SDKs count as `app`.
4. **No map:** fall back to D (already done) plus a cheap `filename` / `filecontent` check against a
   vendored retire.js DB for standalone files like `analytics.min.js`. Mark everything else
   `unknown`, never `app`, so the UI can be honest about it.
5. Consider fingerprint-per-webpack-module (C+E) only if dogfooding shows that map-less bundles
   dominate. On its own evidence it's low-yield.

## Open questions and things to verify before building

- Do Vite 6+/Rolldown **production** maps carry `ignoreList` by default? The docs describe Rollup's
  default and Vite's dev-server default. Confirm on `test-targets/recon-range` (it has both
  `vite.config.js` and `webpack.config.js`).
- How common are first-party private packages in real targets? Answering that decides whether the
  `@acme/*` scope list ships in v1.
- Pairing with C8 (type catalogue) and the visibility overlay's known gap: the ApiSpec page reads
  findings directly (DEBT D74), so a new hidden reason won't reach it until D74 is fixed.
