# D47 — purge object-storage blobs on session delete (REQ-S4)

Status: REVIEWED — both §4 gates passed (design GO-WITH-CHANGES + code review SHIP-WITH-NITS, all
folded, §7) · 2026-09-06 · branch `feat/d47-blob-purge`
Owner: platform-security workstream · closes DEBT **D47** purge-path (Tier 2, supply-chain/security);
`REQ-S4` TTL-enforcement honestly deferred (§5)

## 1. Goal + the hard constraint

`REQ-S4` (**MUST**, `docs/REQUIREMENTS.md:88`) requires, for any custodied sensitive data
(secrets, tokens, reconstructed sources): "Explicit retention, purge, and breach-handling
policy … default TTLs and a **tenant-initiated purge path**."

Today deleting a session purges Postgres rows but **not** the object-storage bytes:

> `delete_session` … "Object-storage blobs are content-addressed and **not swept here; a
> GC pass is future work**." — `sessions/service.py:223-226`

So the sensitive bytes (raw JS, source maps, reconstructed sources, and — since **D45** —
captured request/response bodies carrying PII/secrets) are orphaned in the bucket forever:
unbounded storage growth **plus** a breach/compliance liability for a tool whose premise is
trustworthy secret handling. D45 widened this (opt-in response-body capture now writes PII
into never-purged blobs), which is why this is the next slice.

**The constraint:** the purge must be **complete** (leave no sensitive byte of the deleted
session) and **safe** (never delete another session's/run's live data, never leave a
dangling DB→blob reference).

## 2. Current architecture (what we build on)

**Blob key convention** (`storage.py:1-9,50-55`) — the fact the whole design rests on:

```
key = "{tenant_id}/{run_id}/{kind}/{sha256(content)}"
```

- **Run-scoped AND content-addressed.** Identical bytes captured in two different runs get
  **different** keys (the `run_id` segment differs). Dedup therefore happens only *within* a
  single `(tenant, run, kind)` — there is **no cross-run or cross-session blob sharing**.
  ⇒ deleting everything under prefix `{tenant_id}/{run_id}/` can never orphan or corrupt
  another run's data. (Evidence: `object_key` / `object_key_for_file`, `storage.py:50-71`.)
- **Every** blob kind lives under that run prefix — the 10 kinds in `BLOB_KINDS`
  (`storage.py:24-41`): `input, raw_js, source_map, reconstructed, report, assets, spec,
  capture-requests, graphql, fingerprint-signal`. Write sites all pass `(tenant_id, run_id,
  kind, …)` (`capture/stage.py:192,227`, `api/capture_router.py:731`, fetch/analyze/export …).

**Blob references are scattered — some are NOT columns.** `Run.input_ref`,
`Run.source_map_ref`, `RunAsset.input_ref`, `RunAsset.source_map_ref`, `*.spec_ref`
(`db/models.py:209,212,405,411,519,556`) are columns, but the D45 `capture-requests` ref
(`requests_ref`) lives in the **discover.assets event-payload JSON**
(`api/capture_router.py:757`, `capture/stage.py:207`), not a model column. ⇒ enumerating ref
*columns* to delete keys is **incomplete and fragile**. (The other three payload-JSON refs
are `assets_ref`, `signal_ref`, `graphql_ref`.) `Finding`/`FindingOccurrence` carry **no**
blob ref (evidence is inline `Text`/JSONB), and kinds `raw_js`/`reconstructed`/`report` are
declared but **never written** — so neither is anything to purge.

**The one cross-run subtlety — `spec`.** A full write-path audit confirmed every blob is
run-prefixed, but the `spec` blob (uploaded via `POST /runs/{run_id}/spec`) is referenced by
**session-scoped** rows (`SessionSpec.spec_ref`, `FindingSpecStatus.spec_ref`,
`db/models.py:519,556`) that survive re-runs. A *per-run* purge could orphan a spec a *live*
session still points at — but a **whole-session** purge (this slice) is safe: the spec's run
is a member of the session, swept with it, and its session-scoped rows cascade away too. (As a
bonus, re-uploading a spec already orphans the prior blob today — `spec/service.py:157-167`
calls it "a harmless orphaned blob" — which a session-delete prefix sweep reclaims.) This only
constrains the **deferred** per-run GC (§5), not this slice.

**Storage API today** (`storage.py`): `object_key`, `object_key_for_file`, `ensure_bucket`,
`put_blob`, `put_blob_from_path`, `get_blob`, `download_blob_to_path`. **No `delete`, no
`list`.**

**Delete surface:** `DELETE /sessions/{id}` → `service.delete_session` → 204/404
(`api/sessions_router.py:104-108`). Runs die **only** by FK CASCADE from the session
(`test_delete_session_with_runs_cascades`). There is **no** standalone run-delete endpoint;
the only run-scoped DELETEs remove *rules* (`/runs/{id}/base-url`, `/runs/{id}/wrappers`) and
touch no blobs. ⇒ `delete_session` is the single purge point.

**Transaction boundary:** `tenant_session` commits at `with`-block exit, rolls back on error
(`db/base.py:35-54`). **RLS** filters rows to the tenant via `app.current_tenant`; S3 has no
RLS, but the key prefix embeds `tenant_id`, so a prefix sweep is tenant-scoped by construction.

## 3. Design decisions (each: choice · alternative · why)

### (A) Prefix-delete, not ref-enumeration  ✅ chosen

Delete every object under `{tenant_id}/{run_id}/` via `list_objects_v2` (paginated) +
`delete_objects` (batched ≤1000/call), one sweep per run of the session.

- **Alternative — ref-enumeration:** read each ref column + event payload, delete those
  specific keys. Avoids needing `s3:ListBucket`, but must know *every* ref site incl. the
  event-payload JSON — incomplete (misses a blob whose ref write failed, or any future kind)
  and fragile. This is the same "we forgot a write path" gap D45 just widened.
- **Why prefix-delete:** `REQ-S4` is a **completeness** MUST. Run-scoped keys make a prefix
  sweep both *complete* (every kind, incl. non-column refs) and *safe* (the `run_id` in the
  prefix means it can't reach another run; the `tenant_id` means it can't reach another
  tenant). Cost is bounded by the run's object count (hundreds–low thousands), paginated.

### (B) Purge AFTER the DB commit, fail-forward  ✅ chosen

Collect the session's `run_ids` **inside** the txn → `session.delete(row)` (cascade) →
commit → then sweep each run's prefix.

- **Alternative — sweep before the DB delete:** a sweep-succeeds-then-DB-fails leaves rows
  pointing at deleted blobs → reveal/source reads 500 (dangling ref). Worse, user-visible.
- **Why after-commit:** a crash mid-sweep leaves *orphaned blobs* (storage waste, GC-
  recoverable, **not** user-visible) instead of *dangling refs* (broken reads). Fail toward
  the recoverable state. The two consistency states are asymmetric; we pick the safe one.

### (C) Synchronous, best-effort, logged  ✅ chosen

The sweep runs in the delete request; each run's sweep is wrapped so one S3 error can't
500 the delete (the rows are already gone) — a failure logs **loud + structured** (event
`blob_purge_failed` with `tenant_id`, `run_id`, `prefix`, `attempted`, `deleted`) via
`get_logger` (`observability.py`) so it is alertable. Two failure shapes both count as a
purge failure: (i) a raised `ClientError` (list/delete threw), and (ii) — the H1 catch —
`delete_objects` returning **HTTP 200 with a non-empty `Errors[]`** (per-key permission/
transient failures that an exception handler never sees); each errored key is logged.

- **Alternative — enqueue a purge job** (retriable via the queue): more robust for very
  large runs, but adds a new queue message type + worker handler + a "purge pending" state.
  YAGNI at current scale; a half-done async job is the same orphaned-blob state anyway.
- **Why sync:** delete is a deliberate operator action; the sweep is a few list pages + a
  couple `delete_objects` batches (sub-second to ~1-2 s even for a 2000-asset run). Best-
  effort + loud logging keeps a transient S3 blip from wedging the delete while staying
  observable. (Async/queued purge noted as a future refinement if runs grow.)

## 4. What gets built (vertical slice)

1. **`storage.delete_run_blobs(tenant_id, run_id) -> int`** — list under `{tenant_id}/{run_id}/`
   via the **`list_objects_v2` paginator** (required — a run can exceed 1000 objects; a bare
   call returns only page 1), `delete_objects` in batches of ≤1000, **inspect `Errors[]`** on
   each response (H1) and raise a `BlobPurgeError` if any key errored, returns count deleted.
   The trailing `/` makes the prefix exact (a `run_id` UUID can't be a prefix of another).
2. **`service.delete_session`** — collect `run_ids` before delete; after the `with` block
   commits, `for run_id in run_ids: try delete_run_blobs except Exception: log.error("blob_purge_failed", …structured…)`.
   Correct the docstring. Return value/semantics unchanged (True/False → 204/404).
3. **Tests:**
   - *fast lane* `storage_test.py`: `delete_run_blobs` via a fake `_s3_client` — assert the
     prefix, that it walks **>1000 objects across ≥2 list pages AND ≥2 delete batches** (M3,
     not just the boundary), and that a non-empty `Errors[]` raises `BlobPurgeError` (H1).
   - *fast lane* `sessions/service_test.py`: `delete_session` calls the sweep **once per
     non-empty run_id** for a session WITH runs (L1 — catches a collect-after-delete
     regression, which the run-less case cannot), swallows+`log.error`s a sweep exception
     (monkeypatch `storage.delete_run_blobs`), and a run-less session sweeps nothing.
   - *integration lane* `storage_integration_test.py`: put blobs across **two** runs, delete
     one run's prefix, assert only that run's objects are gone (proves safety + completeness
     against real MinIO). Extend `test_delete_session_with_runs_cascades` to assert the
     bucket prefix is emptied end-to-end.
4. **Docs:** fix the `delete_session` docstring; mark **D47** in `DEBT.md` as purge-path
   RESOLVED / TTL-enforcement DEFERRED (H2 — see §5); add a **retention & purge** note to
   `docs/OPERATING.md`: the tenant-initiated purge now purges bytes, the `s3:ListBucket` +
   `s3:DeleteObject` IAM requirement, and an explicit **warning against a raw age-based S3
   lifecycle rule** (it expires by object age decoupled from session liveness → dangling
   refs; any retention TTL must be liveness-aware / exceed max engagement lifetime). Update
   `docs/ARCHITECTURE.md` if it documents the delete flow.

## 5. Scope line (§4 review: GO-WITH-CHANGES, folded)

- **BUILD now:** purge-on-delete — the actionable `REQ-S4` "tenant-initiated purge path".
- **DOCUMENT now (H2, corrected):** the IAM requirement + a **warning** that automated TTL
  enforcement must be liveness-aware. **No raw S3-lifecycle recipe is shipped** — an age-based
  rule reintroduces the §3B dangling-ref failure (blob expired while its DB row survives →
  reveal 500). So `REQ-S4`'s "default TTLs" half is **honestly DEFERRED**, not claimed done.
- **DEFER (documented in DEBT.md):** (i) a scheduled GC that diffs bucket prefixes vs live
  `run_id`s — the REQ-S4 **backstop** for crash-/failure-orphaned blobs (M1/L2), reclaiming
  what a best-effort sweep failure or a delete-while-running leaves behind; it **must
  special-case `spec`** (diff against session-scoped `SessionSpec`/`FindingSpecStatus.spec_ref`,
  not just live runs) or a per-run sweep will corrupt a live session's spec reference (see §2);
  (ii) liveness-aware auto-purge on TTL expiry; (iii) async/queued purge for very large runs;
  (iv) an S3 list/delete **permission health probe** (M2 — naturally D53's "extend /healthz
  with S3"; until then a first failed purge surfaces loudly via the M1 log). **Ops/IAM
  dependency:** the prod S3 role needs `s3:ListBucket` + `s3:DeleteObject` on the artifact
  bucket (MinIO dev already full-access) — called out in OPERATING.md.

## 6. Adversarial targets (for the §4 design gate)

> A full write-path/ref audit (below) already CONFIRMS #1, #2, #4 (every blob run-prefixed;
> re-run reads the old blob at `runs/coordinator.py:238-239` and **re-stores** under the new
> `run_id` at `:157`, no aliasing; no non-session cascade owns a blob). The `spec` cross-run
> reference is the only edge, and it is safe for a whole-session delete (§2). The §4 reviewer
> confirmed #1–#4 against the code (verdict GO-WITH-CHANGES; see §7).

1. **Is any blob keyed OUTSIDE a run prefix?** (session-level, tenant-level, a second
   key-builder that doesn't embed `run_id`.) If yes, prefix-per-run misses it. Believed no —
   `object_key`/`object_key_for_file` are the only key builders and both require `run_id` —
   but verify there is no other `put_object`/key path.
2. **Cross-run/session sharing:** any place the *same* key is reused across runs (a copied
   ref, a re-run reusing a prior run's blob)? Believed no (run_id in key) — verify re-run
   (`/sessions/{id}/rerun`) creates a *new* run_id and re-stores, not aliases.
3. **Ordering under partial failure:** confirm collecting `run_ids` happens before
   `session.delete`, and the sweep after commit, so no path yields a dangling ref.
4. **Completeness vs cascade:** are there blobs owned by rows that cascade from something
   *other* than the session (so their run_id isn't among the session's runs)? Believed no.
5. **Prefix-collision / injection:** `tenant_id`/`run_id` are server-issued UUIDs (not user
   input), so the prefix can't be steered; confirm no path lets a caller set them.
6. **Test-lane placement:** DB-touching delete test is `integration` (the router test file is
   `pytestmark = pytest.mark.integration`); the sweep unit tests must stay fast-lane.

## 7. §4 design-review outcome — GO-WITH-CHANGES (folded)

An adversarial reviewer (opus) tried to disprove the design against the code. It **could not
break the completeness/safety core** (#1–#4 confirmed: single key-builder both requiring
`run_id`, `storage.py:50-71`; only S3 writer is `storage.py`; server-issued UUID prefixes not
caller-steerable, `sessions_router.py:105` + `models.py:43-46,185`; whole-session `spec`
delete safe incl. scope-change re-run forking a spec-less session, `coordinator.py:304-317`;
every blob-bearing row cascades from the session). Required + folded changes:

| # | Sev | Change | Folded into |
|---|-----|--------|-------------|
| H1 | HIGH | `delete_objects` returns 200 + `Errors[]` on partial failure — a `try/except` misses it. Inspect `Errors[]`, raise `BlobPurgeError`, log each key. | §3C, §4.1, §4.3 |
| H2 | HIGH | A documented-but-unapplied S3-lifecycle TTL isn't a TTL, and a raw age-based rule reintroduces the §3B dangling-ref failure. Drop the recipe; TTL-enforcement honestly DEFERRED; ship only the IAM note + liveness-aware warning. | §4.4, §5 |
| M1 | MED | Total sweep failure returns 204 with a log line — make it loud + structured (`tenant_id`, `run_id`, `prefix`, `attempted`, `deleted`) and position the deferred GC as the REQ-S4 backstop. | §3C, §5 |
| M2 | MED | Missing prod `s3:ListBucket`/`DeleteObject` fails every purge silently — defer a perms health-probe to D53's `/healthz`+S3; rely on the M1 loud log until then. | §5(iv) |
| M3 | MED | A run can exceed 1000 objects — **require** the `list_objects_v2` paginator; fast-lane fake must exercise >1000 across ≥2 list pages AND ≥2 delete batches. | §4.1, §4.3 |
| L1 | LOW | "run-less sweeps nothing" can't catch a collect-*after*-delete regression — add a with-runs test asserting one sweep per non-empty `run_id`. | §4.3 |
| L2 | LOW | Delete-while-running can orphan a blob a worker writes post-sweep — a known residual the deferred GC reclaims (noted, not guarded in v1). | §5(i) |
| L3 | LOW | (a) Considered the boto3 resource `bucket.objects.filter().delete()` (auto-paginate+batch) — **rejected**: it still needs `Errors[]` inspection (H1) and a second client type; the low-level paginator reuses the cached client and is trivially fakeable. (b) Citation drifts fixed (`spec/service.py:71-73`, `coordinator.py:157`, `stage.py:226`). | §2, §6, here |

**Code-review outcome (gate #2, opus): SHIP-WITH-NITS — folded.** Independently re-verified the two
load-bearing invariants against the code (single key-builder/writer both requiring `run_id`; the
`Run.session_id` `ondelete=CASCADE` + `passive_deletes=True` making the explicit pre-delete SELECT the
*only* way to learn the run ids) and found **no correctness or security bug**; count-math, batch-flush,
empty-page, ordering, try/except/else, tenancy, and logging (no secret leakage) all confirmed. Folded:
**M1** — a partial purge logged only the failure *count*, diverging from the folded H1 ("log each key").
`BlobPurgeError` now carries a bounded (`_PURGE_ERROR_SAMPLE_MAX=20`) `sample` of `{key, code}` — which
sensitive blobs survived and why — surfaced in the `blob_purge_failed` log; the cap also bounds memory
on a total-failure sweep. Test nits folded: the broad-except (raw-exception) branch is now covered
(parametrized failure test), plus a `delete_objects`-raises propagation test and an exact-`_DELETE_BATCH_MAX`
no-spurious-empty-call test.

