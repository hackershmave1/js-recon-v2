# Slice 3a — manual-probe handoff (design)

- **Date:** 2026-07-22
- **Status:** approved (brainstorming); ready for implementation plan
- **Slice:** 3a (the first half of slice 3). Slice 3b — the REQ-S2 secret-reveal
  UX — is a separate, later design.
- **Primary REQ:** REQ-P1 (MUST). Touches REQ-D1 (triage state store), REQ-S1
  (tenant RLS), REQ-S3 (correlated logs/audit). Does **not** address REQ-S2/S4
  (deferred to 3b).

## 1. Context

Slice 2 turned "one JS file" into content-addressed findings (`endpoint`,
`secret`, `param`) with honest coverage. Slice 3 is the manual-probe handoff:
REQ-P1 says the platform sends **no** automated exploit traffic — it reconstructs
the request and hands the user a ready-to-fire artifact, which the user runs
manually and then **marks the finding confirmed**.

Slice 3 splits into two vertical sub-slices (user decision):

- **3a (this spec):** reconstruct probeable requests from findings, serialize
  export artifacts, and record finding-level triage / mark-confirmed.
- **3b (later):** the REQ-S2 secret-reveal UX (ephemeral, just-in-time,
  audit-logged) and its REQ-S4 retention tie-in. This is deferred because it
  requires reconciling the current slice-2 reality — the raw secret is stored as
  plaintext in `finding_occurrence.evidence` (RLS-only) — which is a security
  design problem deserving its own gates. 3a artifacts embed a
  `# add auth/headers here` placeholder rather than any secret material, which is
  exactly REQ-S2's never-plaintext-by-default posture; 3b later adds the JIT
  reveal that can substitute a live value.

## 2. Settled decisions

These were decided during brainstorming and are binding for 3a:

1. **Sequencing:** 3a (P1 handoff) first; 3b (S2 reveal) second.
2. **Reconstruction runs on-demand at read time**, behind a `ReconstructedRequest`
   interface — no new table, no new stage, no staleness. Slice 4 may materialize
   the *same shape* into a persisted `recon_object` when the threat model needs a
   stable-id projection; we do not build that table now (YAGNI).
3. **No extractor change in 3a.** We reconstruct from what Vespasian already
   extracts (method / path / query / body). Request headers (incl. `Authorization`)
   are **not** extracted; `Content-Type` is inferred by the serializer from body
   shape. Full static-header extraction is a deferred follow-up (pairs with the
   C2 wrapper work).
4. **Export formats:** `curl` + raw HTTP request only. Raw HTTP covers the Burp
   Repeater paste workflow (there is no separate single-request Burp format).
   Postman (Collection v2.1) and mitmproxy (flow / addon) are deferred; each drops
   in later as an isolated pure serializer over the same `ReconstructedRequest`
   with no rework.
5. **Triage is finding-level, session-scoped**, keyed `(session_id, finding_hash)`
   so a verdict survives re-runs (slice 5 re-scans continuously). "Mark request
   confirmed" sets `status = confirmed` on the operation's endpoint finding. The
   same mechanism generalizes to secrets and slice-4 threat-model findings.

## 3. Architecture & module layout

Everything in 3a is **additive**: no worker / queue / stage changes, and no
changes to `extract` / `analyze` / `store`. The read path is a new thin query in
the established `findings/queries.py` style; the only schema change is one new
table.

```
src/recon/probe/
  reconstruct.py   # ReconstructedRequest + reconstruct_run(): group findings by
                   #   operation key, union params/hosts. Pure over view data.
  serialize.py     # to_curl() / to_http(): pure serializers + Content-Type
                   #   inference + shell/CRLF-safe escaping.
  triage.py        # set_triage(): upsert keyed (session_id, finding_hash).
  queries.py       # read-models: reconstructed requests, triage lookup.
  reconstruct_test.py  serialize_test.py  triage_test.py   # colocated (§11)
src/recon/api/probe_router.py                 # GET /runs/{id}/requests, POST triage
src/recon/migrations/versions/0004_finding_triage.py
```

The operation-key grammar (splitting a finding `value` into its `METHOD + path`
operation vs. its params) lives with the value grammar in `findings/normalize.py`
(the module that owns that grammar) so it is not duplicated in `probe/`. Two new
pure helpers are added there, deriving the operation from a *stored* value per the
documented grammar (inverse of `endpoint_operation` / `normalize_param_value`):

- `operation_of_endpoint_value(value) -> str` = `value.split("?", 1)[0]`
  (endpoint value is `operation` + optional `?query`).
- `operation_of_param_value(value) -> str` = `value.rsplit(" ", 1)[0]`
  (param value is `f"{operation} {location}:{name}"`; the trailing token is
  `location:name`, which contains no space).

`normalize.py` is pure/dependency-free finding-identity logic, so importing it is
a shared-library import, not a feature→feature dependency.

## 4. Data model

One new table, `finding_triage` (migration `0004`), built with the same
`Base.metadata.create_all()` + FORCE-RLS + `tenant_isolation` policy + GRANT shape
as `0002`, and added to a new `TRIAGE_TABLES` tuple in `db/models.py`.

| column | type | notes |
|---|---|---|
| `id` | uuid pk | `gen_random_uuid()` |
| `tenant_id` | uuid fk→tenant | RLS scope (REQ-S1) |
| `session_id` | uuid fk→session | triage scope — survives re-runs |
| `finding_hash` | varchar(64) | stable finding identity (REQ-D3); **not** a run-scoped row id |
| `status` | varchar(16) | `open` / `confirmed` / `dismissed`; CHECK-constrained |
| `note` | text null | analyst free-text |
| `actor` | text null | best-effort supplied label until real auth lands (NOTE in code) |
| `created_at` / `updated_at` | timestamptz | `now()` server defaults |

`UNIQUE(session_id, finding_hash)` — one verdict per finding identity per
engagement.

**Why `(session_id, finding_hash)` and not `(run_id, finding_id)`:** findings are
per-run rows, but the same finding recurs with the *same hash* across runs (that
is what REQ-D5's diff relies on). Keying triage to the run would wipe every
verdict on each re-scan. Session + hash means "I confirmed this endpoint" sticks
across the whole engagement.

`finding_hash` is intentionally **not** a DB foreign key — triage outlives any
single run's `finding` rows by design, so the join is logical (on the hash), not
referential. Documented in the model.

**Write:** `set_triage()` = `INSERT … ON CONFLICT (session_id, finding_hash) DO
UPDATE SET status, note, actor, updated_at`. It also appends a `triage.updated`
`run_event` (durable, append-only) as a free audit trail of verdict changes,
reusing existing infrastructure (REQ-S3 correlation via run_id / session_id).

**Read:** `findings/queries.list_findings` gains a left-join to `finding_triage`
on `(session_id, finding_hash)`, so each `FindingView` carries
`triage: {status, note, actor, updated_at} | null`. A never-triaged finding reads
as `null` (≡ `open`), distinct from an explicit `open`.

## 5. Reconstruction model

`ReconstructedRequest` is a pure view, one per **operation** (`METHOD` + templated
path — the natural probe unit and the same grouping a future `recon_object` would
use):

```
ReconstructedRequest:
  operation:     "POST /api/orders/{id}"   # METHOD + templated path (the group key)
  method:        "POST"
  path:          "/api/orders/{id}"
  hosts:         ["api.acme.io", ...]       # distinct occurrence hosts; may be empty
  query_params:  [{name, example?}]         # PARAM findings, location=query
  body_params:   [{name}]                   # PARAM findings, location=body
  content_type:  "application/json" | None  # inferred: body present ⇒ json
  example_url:   "/api/orders/123?x=1"      # a representative occurrence.raw_url
  probeable:     true                        # false for websocket ops
  endpoint_hash: "…"                         # the finding_hash to triage/confirm
```

`reconstruct_run(tenant_id, run_id)`:

1. Group the run's `endpoint` + `param` findings by operation key, using
   `operation_of_endpoint_value` / `operation_of_param_value` (§3).
2. Union params, deduped by `location:name`.
3. Collect distinct hosts from the endpoint findings' occurrences (sorted for
   deterministic order).
4. Seed example values from a representative `raw_url` (the deterministic-first
   occurrence, reusing `queries.py`'s existing occurrence ordering) where present.
   Values we do not know (body values, path variables) render as explicit
   placeholders (`<id>`, `<name>`) — never invented (REQ-C2 honesty).

**Edge cases** (all honest, none guessed):

- **No host** (relative URL — base-URL resolution is the deferred C2 SHOULD): the
  artifact uses a `{{base_url}}` placeholder, **not** silently the session scope
  host (that could be wrong).
- **Multiple hosts:** the deterministic first is used; the rest listed in a comment.
- **WebSocket** (`WS` / `WSS`): listed with `probeable: false`, `artifacts: null`,
  reason `"websocket — not an HTTP request"`. curl / raw-HTTP do not apply.
- **No params:** a bare method+path artifact.
- **Unattributed calls:** produce no finding, so they are not reconstructable —
  that gap is the C2 coverage counter's domain, already surfaced on the findings read.

## 6. Serializers & security

`to_curl` and `to_http` are pure functions over one `ReconstructedRequest`. Each
emits a `# add auth/headers here` placeholder line (no secret material in 3a).
`Content-Type: application/json` is emitted when body params exist (they come from
an object literal / `JSON.stringify`, so JSON is the correct inference).

**Security — first-class, not an afterthought.** The analyzed JS is
attacker-influenced, and these artifacts are pasted into a **shell** (curl) and an
**HTTP client** (raw HTTP). Therefore:

- `to_curl` **shell-quotes every interpolated value** (`shlex.quote` on URL,
  header values, body) — the artifact must never be a shell-injection vector.
- `to_http` **rejects or percent-encodes CR / LF and control characters** in the
  path, query, and header values — no header injection / request smuggling in the
  emitted request.
- Both **cap** oversized URLs / bodies.

Sample curl output:

```
# POST /api/orders/{id}  (host: api.acme.io)
# add auth/headers here
curl -X POST 'https://api.acme.io/api/orders/123' \
  -H 'Content-Type: application/json' \
  --data '{"amount":"<amount>"}'
```

## 7. API surface

Thin routes (`Depends(get_tenant_id)`), delegating to `probe/queries.py` +
`probe/triage.py`, following the `findings_router` pattern. Registered in
`api/app.py`.

| route | behavior |
|---|---|
| `GET /runs/{run_id}/requests` | Reconstructed requests for the run, each with inline `artifacts: {curl, http}`. `404` if the run is invisible to the tenant (RLS); `200` + empty list if no attributed endpoints — the two stay distinct, as with findings. |
| `POST /runs/{run_id}/findings/{finding_hash}/triage` | Body `{status, note?, actor?}`. Derives `session_id` from the run, upserts `(session_id, finding_hash)`, returns the triage state. `400` on bad status; `404` if the run/finding is not visible. "Mark confirmed" = `status: confirmed` on the operation's `endpoint_hash`. |
| `GET /runs/{run_id}/findings` | Extended to include `triage` per finding (§4 join). |

## 8. Observability & audit (REQ-S3)

- Structured logs correlated by `run_id` / `session_id` for reconstruct + triage.
- The `triage.updated` `run_event` is the durable audit trail of verdict changes.
- No new metrics for MVP. Reconstruction is a bounded read-time pass
  (`selectinload`, single grouping pass); REQ-A1's <200 ms budget is about enqueue,
  not this read.

## 9. Testing (colocated, §11) & gates (§4)

- `reconstruct_test.py` — grouping, param union/dedup, host selection
  (none / one / many), example-value seeding, websocket, no-params.
- `serialize_test.py` — curl + raw-HTTP golden output, `Content-Type` inference,
  **shell-injection safety** (hostile URL/param → safely quoted), **CRLF /
  header-injection safety**, size caps, placeholder rendering.
- `triage_test.py` — upsert / transition, **triage survives a re-run** (new run,
  same hash → verdict still attached), RLS isolation (another tenant can neither
  read nor write), audit event emitted.
- `probe_router` API tests — happy path, `404` / `400`, findings-read includes triage.
- **Both review gates:** adversarial design review before build (focus: injection
  safety, reconstruction honesty, triage keying); higher-model code review after.
  Built TDD, in vertical slices, as isolated Conventional Commits.

## 10. Out of scope (deferred, tracked)

- **REQ-S2 secret reveal + REQ-S4 retention** → slice 3b (next). Reconcile the
  plaintext-in-`evidence` storage there.
- **Static request-header extraction** (Vespasian change) → follow-up.
- **Postman / mitmproxy exporters** → follow-up serializers.
- **Cross-file base-URL resolution + wrapper-teaching** (REQ-C2 SHOULDs) → their
  own thread.
- **`recon_object` materialization** (persisted per-operation projection) →
  slice 4, when the threat model needs a stable-id projection.

## 11. Open questions

None blocking. Header/auth completeness is a known, deliberate limitation of 3a
(the manual tester supplies their own session auth); it is recorded in §2.3 and §10.

## 12. As-built amendments (final-gate fixes)

Changes made after implementation, from the whole-branch code review + adversarial
design review (all landed, tested, re-verified):

- **Absolute-URL serialization (was a HIGH bug).** `example_url` is the raw JS
  literal and is frequently absolute; the serializer must not re-prepend
  `https://<host>` (which produced a double-scheme URL). As-built, a
  `_request_parts` helper yields `(base, origin_target, host)`: an absolute
  observed URL supplies its own scheme/host, and raw HTTP emits **origin-form**
  (`METHOD /path HTTP/1.1` + `Host:`), not absolute-form.
- **Operation-level triage.** `ReconstructedRequest.endpoint_hash` (single) became
  `endpoint_hashes: tuple[str, ...]` (all of the operation's endpoint findings,
  sorted), exposed on `GET /runs/{id}/requests`, so confirming an operation can
  verdict every underlying finding (query-variant endpoints included).
- **Honest Content-Type.** `application/json` is asserted only when body params
  exist **and** every contributing endpoint kind is `fetch`/`axios`; for jQuery
  (form-urlencoded) / unknown kinds the header is omitted rather than guessed
  (REQ-C2 honesty).
- **Triage write hardening.** `note`/`actor` are `COALESCE`d on upsert (a
  status-only update no longer clobbers a stored note/actor), and the returned
  `TriageState` re-reads the persisted row. A triage POST now returns `404` when
  the `finding_hash` does not exist in the run (matches the §7 contract).
