---
status: accepted
date: 2026-08-08
---

# 7. Single analysis core (v1 convergence, no dual-core union)

## Context and Problem Statement

`js-recon-v2` is a requirements-driven rewrite that absorbed an older v1 app ("JS Security
Extractor": its own FastAPI backend and SPA, plus jsluice + REP-style regex extractors and
an asset-provenance graph). Convergence forced a decision: keep v1's analysis core beside
the new one (union them), or retire it? An earlier draft called the two cores
"complementary" and proposed unioning them.

## Considered Options

* **Single v2 analysis core** — Vespasian (tree-sitter) + Kingfisher + Sourcemapper only;
  retire v1's backend/UI and do **not** union its jsluice/REP extractors.
* **Union the cores** — carry v1's jsluice + REP-style extractors forward alongside v2's
  and merge findings.

## Decision Outcome

Chosen option: **single v2 analysis core; v1 retired, not unioned.** v1's backend and SPA
were deleted (the platform already ships Vespasian + Kingfisher + Sourcemapper, a Sources
viewer, and the findings/OpenAPI pipeline per the v2 requirements). v1-only tooling
(jsluice, REP regex extractors, the asset-provenance graph) appears in zero REQ-* items and
was left behind. v1's one durable capability — runtime, post-authentication capture —
survives as the MV3 extension feeding the platform's ingest contract, not as a second
analysis core.

### Consequences

* Good — one analysis pipeline to reason about, test, and secure; no reconciliation of two
  overlapping finding sets and no dead v1 surface to maintain.
* Bad — any genuinely-unique recall from jsluice / REP regexes is not captured; if a
  concrete gap appears it must be re-introduced as a REQ-backed v2 capability, deliberately.
* Neutral — the name "Vespasian" here is a *static* reimplementation; the upstream
  Praetorian Vespasian is traffic-based (a known naming overlap, not the same tool).

### Confirmation

`docs/ARCHITECTURE.md` "Convergence history (v1 retired)" — the rejected "complementary /
union" assumption is called out there explicitly. The v1 `api/` and `web/` directories
were deleted in the convergence ("removed `apps/capture/{api,web}`") — no second backend
or SPA remains; the MV3 `chrome-extension/` is the only carried-forward *app*. The
extension→platform ingest contract is documented in `docs/ARCHITECTURE.md` and pinned by
`apps/platform/src/recon/api/capture_contract_test.py`.

## More Information

Recorded retroactively 2026-08-08 (DEBT D10). This is the repo's one real *supersession* —
it reverses the earlier "complementary cores" draft assumption (no prior ADR existed to
mark `superseded-by`). Off-repo context in session memory `apps-convergence-spike`.
