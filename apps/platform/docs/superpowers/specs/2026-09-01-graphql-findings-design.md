# GraphQL findings + schema-recon slice — design

Status: DRAFT (proposed 2026-09-01). §4 adversarial design-gate: **BUILD WITH CHANGES** —
must-fixes folded in below. Awaiting user go-ahead to build.
Grounded against live code (file:line below). Unqualified paths: `domain.py` =
`src/recon/domain.py`, `models.py` = `src/recon/db/models.py`; all else under `src/recon/`.
Supersedes enrichment slice C's **locked
decision 1** ("GraphQL = export-only annotation, not first-class findings",
[`2026-08-07-enrichment-design.md`](2026-08-07-enrichment-design.md)) — this slice is that
doc's own listed fast-follow ("First-class GraphQL findings + migration + FE").

## Goal

Promote the already-extracted GraphQL surface from an invisible export-only annotation to a
**first-class, located, countable finding** and a **dedicated workspace tab**, then leave the
data model shaped so a later **static** schema/probe pass fits without a rewrite. No new active
traffic — everything stays static (REQ, ADR-0006).

### Why now (dogfood, run `a552c014`, hackerone, 499 assets)

The foundation already recovers **128 operations** (55 query + 73 mutation) and throws them into
`x-recon-graphql-operations`, which no workspace surface reads. Measured gaps the promote must be
honest about:

| Signal | In the bundle | Recovered today |
|--------|---------------|-----------------|
| Operations | 128 | 128 ✅ (export-only) |
| Fragment definitions | ~1,355 (61 assets) | 27 parsed then **dropped** |
| Subscriptions | 24 (5 assets) | 0 |
| Source location | — | file-level only (no line/col) |

The 27/1,355 fragment gap is **tag-name gating**: `_call_document` requires the literal callee
`gql`/`graphql` (`graphql_ops.py:86`), but minifiers rename the tag to one char. Closing that is
high-ROI but is **deferred by user decision** (see below) to a fast-follow / DEBT item.

## Decisions (user, 2026-09-01)

1. **v1 = promote only (fastest).** Surface exactly what the current extractor already reaches
   (the 128 ops + the 27 already-parsed fragments), with location + count + UI. Content-based
   ("smart") tag detection that would recover the ~1,300 missed fragments and the subscriptions
   is **out of scope for v1** → a tracked DEBT fast-follow. v1 UI must not imply completeness.
2. **UI = both surfaces.** Emit `FindingType.GRAPHQL` findings (for count, dedup, identity,
   REQ-D5 diff) **and** add a dedicated "GraphQL" tab for the grouped, source-linked view.
3. **Deeper reach = static schema + sendable templates** (a later slice). Reconstruct a partial
   SDL from recovered ops/fragments and emit ready-to-send operation templates for Burp/manual —
   **the platform sends nothing** (charter unchanged). v1's data model captures the one extra
   field this needs (fragment `on_type`); `spreads`/variable names are re-derived in Phase 3, not
   stored in v1.

### Settled by existing convention (no decision needed)

- Finding **identity** is `sha256(v2, type, value)` — path-free (`normalize.py:330-344`); source
  path lives on the occurrence (the `Finding` docstring `models.py:295` is stale pre-v2 — do NOT
  re-add path to the hash). Location reuses `FindingOccurrence.{line,col,offset_start,offset_end,
  run_asset_id,source_path}` (`models.py:355-365`) exactly as `_record_endpoint` does.
- Adding a type is a one-line enum add (auto-widens the model CHECK) + a copy-of-0018 migration.
- GraphQL is a **distinct** type, so every `type == 'endpoint'` read model (OpenAPI paths,
  coverage counters REQ-C2, headline counts) excludes it automatically — same rationale as
  `ENDPOINT_GENERIC` (`domain.py:79-84`). A GraphQL op is not an HTTP endpoint and must never
  move the coverage numbers.

---

## Phase 1 — promote to located findings + tab

### 1. Data model — carry location + fragments (`findings/graphql_ops.py`)

Today `GraphQLOperation` is `(op_type, name, fields)` with **no location**, and
`parse_operations` drops every `FragmentDefinitionNode` (`graphql_ops.py:127`). Change:

```python
@dataclass(frozen=True)
class GraphQLDefinition:              # replaces GraphQLOperation; export maps .kind -> "op_type"
    kind: str            # "query" | "mutation" | "subscription" | "fragment"
    name: str | None
    fields: tuple[str, ...]          # top-level selection field names
    on_type: str | None = None       # fragment type condition ("on User"); None for ops
    line: int | None = None           # 1-based, of the gql`` document call-site in the JS
    col: int | None = None
    offset_start: int | None = None   # byte offsets of the document node
    offset_end: int | None = None
```

- `extract_documents` returns each document **with its tree-sitter node position** (`start_point`,
  `start_byte`, `end_byte` are already on the node — currently discarded at `graphql_ops.py:67-74`).
  Location is the **document call-site** (the `gql`…`` node), shared by every definition it holds —
  precise per-definition offsets are a fast-follow (needs graphql-core loc + byte math).
- `parse_operations` **stops** `continue`-ing past `FragmentDefinitionNode` (`:127`) and emits it
  as `kind="fragment"` with `on_type = defn.type_condition.name.value`. Operations keep S2 field
  filtering (`:129-133`). Soft-miss invariant T2 unchanged.
- `spreads`/`variables` are **deferred to Phase 3** (re-derived there), NOT stored in v1 — avoids
  a mypy-strict `Visitor` subclass for data no v1 surface reads (adversarial finding #7).
- Existing test `test_parse_ignores_fragment_definitions` **inverts** (fragments are now emitted).

### 2. Enum + migration

- `domain.py:65-90` — add `GRAPHQL = "graphql"` (auto-widens `ck_finding_type`, `models.py:304`).
- `migrations/versions/0019_finding_type_graphql.py` — verbatim copy of
  `0018_finding_type_page_route.py`: drop-then-add `ck_finding_type` with `_ALLOWED` gaining
  `'graphql'`, `down_revision = "0018_finding_type_page_route"`. Revision id = 25 chars ≤ 32
  (Alembic `version_num VARCHAR(32)` — [[alembic-migration-gotchas]]).

### 3. Finding value / identity (`findings/normalize.py`)

Path-free, stable, human-legible `value`:
- named operation: `"<kind> <name>"` (e.g. `"mutation CreateOrganizationApiToken"`).
- anonymous operation: `"<kind> · <sha256(print_ast(defn))[:12]>"` — a stable digest of the
  operation BODY. S2 filters selections to `FieldNode`, so an anonymous spread-only op has
  `fields=()`; a body digest stops every such op collapsing to `"query · "` (adversarial finding #2).
- fragment: `"fragment <name> on <on_type>"` (fragments are always named).

Two identical ops in different assets → **one** finding, **many** occurrences (path/line on the
occurrence). `fields`/`on_type` ride `attributes` (display-only, not identity). **Accepted
identity behavior:** two ops sharing a name but with different selections merge to one finding
(one `attributes.fields` wins) — op names are conventionally unique, mirroring the endpoint
same-path merge; stated, not fixed (adversarial finding #3).

### 4. Write site (`findings/analyze.py`)

- Add `_record_graphql` (mirrors `_record_endpoint` `:865-937`): for each located definition,
  `_write(...)` (`:1104`) with `finding_type=FindingType.GRAPHQL`, path-free `value`, a
  `store.Occurrence` carrying `line/col/offset_start/offset_end`, `engine="vespasian"`, and
  `attributes={"kind","name","fields","on_type"}`.
- Keep the export blob (`_record_graphql_operations`, `:1057`) — decision 2 is "both". Call
  `graphql_ops.collect_definitions(source)` **once** and fan out: the finding writer takes ALL
  definitions; the export blob takes **operations only** (`kind != "fragment"`) and still emits the
  JSON key `"op_type"` (map `defn.kind` → `"op_type"`), so `queries.graphql_operations`
  (`queries.py:402,413`) and the `x-recon-graphql-operations` annotation stay **byte-for-byte
  unchanged** (adversarial finding #1 — a `GraphQLOperation` alias would expose `.kind` and break
  `analyze.py:1085`, so there is NO alias). Wire at the existing call site (`analyze.py:618`).

### 5. Read + API

- Findings section: `queries.list_findings` (`:171-261`) picks up `type='graphql'` for free →
  Type facet + search work with no change; router (`findings_router.py`) already forwards
  `type`/`occurrences`.
- Dedicated tab: v1 reads the findings list client-side, filtered to `type='graphql'`, grouped by
  `kind` (same pattern FindingsPage already uses). A server-side `?type=` filter is a fast-follow.
- `graphql_operations` (`queries.py:378`) stays the **OpenAPI export's** source.

### 6. Web (`apps/platform/web`)

- Findings surface: `api/findingLabels.ts` add `graphql: "GraphQL"`; `features/findings/findings.css`
  + `features/overview/overview.css` add a `.fp-type-graphql`/`.ov-type-graphql` colour;
  `features/overview/OverviewPanel.tsx:45-64` add a `countType(findings,"graphql")` metric card.
- Dedicated tab: `shell/Sidebar.tsx:35-44` add a `NAV_ITEM`; `main.tsx:22-38` child route;
  `app.tsx` a `GraphQLRoute`; new `features/graphql/GraphQLPage.tsx` (+ `.test.tsx`) mirroring
  `TechPage` — operations grouped by kind, each row: name, top-level fields, and asset + line
  linking into the Sources view.
- **Honesty (T-honest):** the tab + count carry a one-line note that static detection currently
  resolves `gql`-tagged/inline documents only; minified/renamed tags are not yet resolved. Render
  it from a constant, not per-number prose ([[ui-redesign-workstream]] real-data-only rule).

---

## Phase 3 (later, static) — design intent v1 must fit

Reconstruct a partial schema and sendable templates **without traffic**: (1) build a bundle-wide
`name → fragment` table and resolve `spreads` transitively (graphql-core `concat_ast`); (2) infer
a partial SDL from usage (types from fragment `on_type`, fields from selections); (3) print
ready-to-send operation templates (`print_ast`) with variable stubs, for Burp/manual. v1 captures
`on_type` now (needed for the fragment `value`); `spreads`/`variables` are re-derived here
transitively from the fragment table, so v1 stores neither. Active introspection is a **separate, gated**
decision, explicitly not taken here.

## Invariants / traps

- **T1 (no active traffic).** Phases 1 + 3 are static; graphql-core parses offline (ADR-0006).
- **T2 (soft-miss).** A malformed/`${…}`-interpolated/deeply-nested document still returns `()` —
  analyze never fails on hostile JS (pinned by `graphql_ops_test`).
- **T3 (coverage honesty).** `GRAPHQL` is distinct → auto-excluded from `type=='endpoint'`
  coverage (REQ-C2). Verify no counter moves.
- **T4 (identity).** `value` is path-free; do not re-add path to `finding_hash` (v2).
- **T5 (idempotency).** Reuse `store.record_finding` → `(run_id,finding_hash)` +
  `(finding_id,occurrence_hash)` upserts; a re-analyze adds no dupes (REQ-A3).
- **T6 (promote-only honesty).** v1 recovers a fraction of fragments/subscriptions; UI must not
  claim completeness; the gap is a DEBT fast-follow, not silent.

## Fast-follows (out of scope, tracked)

- **Smart content-based tag detection** (DEBT) — detect a gql document by template/string
  *content* (leading `query|mutation|subscription|fragment`, validated by `parse()`), not callee
  name; recovers minified/renamed tags → the ~1,300 missed fragments + subscriptions. Highest ROI.
- Relay compiled-`DocumentNode` objects + persisted-query hash detection (other targets).
- Cross-bundle fragment stitching; precise per-definition offsets; server-side `?type=` filter.

## Acceptance

- New host-lane unit tests green (`graphql_ops` location + fragment emission; `normalize` value;
  `analyze` writes a located `GRAPHQL` finding + keeps the export blob); web vitest for the tab +
  count card; `RECON_REQUIRE_ENGINES=1` fast lane + `--cov-fail-under=60` hold.
- Re-run `a552c014`: 128 ops + 27 fragments appear as located findings and in the tab, with a
  visible line/asset; no endpoint coverage counter moves; OpenAPI export unchanged.
- Both §4 gates: adversarial design review of THIS doc; higher-model code review of the diff.

## Blast radius (verified file:line)

| # | File | Change |
|---|------|--------|
| 1 | `recon/domain.py:65-90` | `GRAPHQL = "graphql"` |
| 2 | `migrations/versions/0019_finding_type_graphql.py` (new) | widen `ck_finding_type` |
| 3 | `findings/graphql_ops.py` | location + fragments + `on_type`; `collect_definitions` |
| 4 | `findings/normalize.py` | path-free graphql `value` builder |
| 5 | `findings/analyze.py:1057,618` | `_record_graphql` (+export ops-only, keep `op_type` key), single parse — file already >300-line cap (pre-existing DEBT, not introduced here) |
| 6 | `findings/graphql_ops_test.py`, `analyze_test.py`, `normalize`/`store` tests | update/add |
| 7 | web `findingLabels.ts`, `findings.css`, `overview.css`, `OverviewPanel.tsx` | findings surface |
| 8 | web `Sidebar.tsx`, `main.tsx`, `app.tsx`, `features/graphql/GraphQLPage.tsx(+test)` | tab |
| 9 | `DEBT.md` | smart-detection fast-follow entry |

## §4 design-gate verdict (2026-09-01): BUILD WITH CHANGES

Adversarial review verified every anchor against live code + graphql-core 3.2.11: line numbers
accurate; `FragmentDefinitionNode.type_condition.name.value` correct (graphql-core
`language/ast.py:533-537,656-659`); tree-sitter call-site offsets are the right location source
(`_jsast.py:661-671`, matching `_record_endpoint`); copy-of-0018 migration sufficient (single
linear head, `0001` builds the CHECK from model metadata); `GRAPHQL` auto-excluded from every
endpoint read model (exact-match / explicit allowlist only — `reconstruct.py:146`,
`hosts.py:264-269`; REQ-C2 coverage derives from the extractor's counts, not finding types); "both
surfaces" has no double-count and the tab is lighter than the shipped Findings tab (shared
`useRunData`, subset filter). Three must-fixes, all folded in above:

1. **Export unchanged** (§ write site) — fan-out writes findings for all defs, but the export blob
   stays operations-only and keeps the `"op_type"` JSON key; no `GraphQLOperation` alias.
2. **Anonymous identity** (§ finding value) — anonymous op `value` uses a `print_ast` body digest,
   not `fields`.
3. **No `Visitor`** (§ data model, Phase 3) — `spreads`/`variables` deferred to Phase 3; keep
   `on_type`.

Second gate (higher-model code review of the diff) runs after build, per repo convention.
