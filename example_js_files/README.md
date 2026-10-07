# js-api-recon fixture corpus

13 synthetic JS files covering the ways endpoints show up in real bundles, plus the expected
categorisation for each under the factored field model (`resolution` / `at_sink` / `scope` /
`kind` / `base_source`) rather than the flat 5-value `type` enum.

```
fixtures/       the JS files to run the detector against
EXPECTED.md     per-file breakdown with reasoning and suggested detector rules
expected.json   the same, machine-readable — 109 findings, for regression testing
```

## Coverage

| File | Framework / shape | What it exercises |
|---|---|---|
| 01 | Vite + React | const-folded base URL, URL-constants module, declared-but-uncalled endpoints |
| 02 | Webpack 5 minified | mangled identifiers, wrapper-function sinks, `__webpack_require__.p` |
| 03 | Next.js App Router | same-origin relatives, `NEXT_PUBLIC_*` env base, `__BUILD_MANIFEST` route table |
| 04 | Angular | `environment` object base, HttpInterceptor URL rewriting |
| 05 | Nuxt / Vue | `baseURL` as a sibling option, not string concat |
| 06 | axios | `axios.create({baseURL})` — two instances, one resolvable |
| 07 | vanilla | multi-hole templates, `new URL`, lookup tables, pure member access |
| 08 | react-router | route tables, OAuth authorize URL, swagger.json |
| 09 | mixed | WebSocket, EventSource, sendBeacon, XHR, importScripts, form.action, GraphQL |
| 10 | third-party SDKs | scope demotion, plus a first-party base leaked via vendor config |
| 11 | SheetJS-style vendor | XML namespaces, schema URIs, regex literals, license comments |
| 12 | obfuscated | base64, hex escapes, `join`, `fromCharCode`, reversed strings |
| 13 | service worker | precache route lists, workbox regexes, remote config.json |

## Using it as a regression suite

```bash
js-api-recon scan fixtures/ --format json > actual.json
# compare against expected.json — diff on (url, resolution, at_sink, bucket)
```

Two metrics worth tracking separately:

- **Recall on `PROBE` + `NEEDS_HOST`** (84 of 109) — missing one of these is a missed endpoint.
- **Precision on `DROP`** (17 of 109, most of them in file 11) — every false positive here is
  noise the analyst has to read past.

Bucket totals: PROBE 46 · NEEDS_HOST 38 · NEEDS_RUNTIME 8 · DROP 17.

## Caveats

The hosts and paths are invented. `accenture.com` appears only because it was in the original
example; nothing here was scanned from a live property, and the `pk_live_` / DSN / client_id
values are fabricated placeholders shaped to trip secret detectors, not real credentials.

Files 02 and 11 are hand-written to *look* like minifier output. Real bundles have far more
noise per byte, so treat file 11 as a floor for the suppression rules, not a ceiling — worth
adding a real vendored `xlsx.full.min.js` and `moment.min.js` to the corpus once the rules pass.
