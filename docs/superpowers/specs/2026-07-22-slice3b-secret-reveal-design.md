# Slice 3b — secret reveal (design)

- **Date:** 2026-07-22
- **Status:** approved (brainstorming + adversarial design-review gate); ready for implementation plan
- **Slice:** 3b (the second half of slice 3; 3a shipped the REQ-P1 manual-probe handoff).
- **Primary REQ:** REQ-S2 (MUST). Touches REQ-S1 (tenant RLS), REQ-S3 (correlated
  audit). Does **not** discharge REQ-S4 (retention/purge) — that stays slice 5.

## 1. Context

Slice 2 stored each secret as a content-addressed finding whose identity is
`provider:sha256(token)` (never the token in the hash), plus a
`finding_occurrence` row that today **also holds the raw token in `evidence`**
(plaintext, RLS-only) and is served verbatim by `GET /runs/{run_id}/findings`
([findings_router.py:60](../../../src/recon/api/findings_router.py)). That is the
live REQ-S2 violation this slice closes.

REQ-S2 (MUST): "Secrets … stored as a one-way hash + location by default — never
plaintext; reveal is ephemeral, just-in-time, and audit-logged, so the platform
is not a concentrated store of third-party live credentials."

**Storage model — decided (approach A): don't store the plaintext; re-derive it
just-in-time.** The occurrence already records byte offsets into the run's source
blob (`run.input_ref`, object storage). Slicing the blob at `[offset_start:
offset_end]` reconstructs exactly what `evidence` held — verified: `evidence` is
`secret.snippet` and `offset_end = offset_start + len(snippet.encode("utf-8"))`
([analyze.py:217-235](../../../src/recon/findings/analyze.py)). So the DB keeps
hash + location only; the blob is the single at-rest copy of the plaintext.

## 2. Settled decisions

Decided during brainstorming; binding for 3b:

1. **Read redaction (Q1):** `GET /runs/{run_id}/findings` stops serving `evidence`
   for SECRET occurrences. It returns the finding's `provider:sha256` value +
   location (`source_path/line/col/offset_start/offset_end`) + a per-finding
   `revealable: bool`. **Endpoint/param occurrences keep their `evidence`** (a JS
   code snippet — analysis context, not a credential).
2. **Reveal endpoint (Q2):** `POST /runs/{run_id}/findings/{finding_hash}/reveal`
   — a **direct** authorized call (no one-time / TTL token handshake). The only
   auth gate today is `X-Tenant-Id`; a token separating "requested" from "viewed"
   enforces nothing until real per-user auth exists (YAGNI). POST, not GET, so the
   value never lands in access logs, browser history, or caches.
3. **Audit (Q3):** every reveal *attempt* writes a durable `run_event` —
   `secret.revealed` on success, `secret.reveal_denied` on refusal. Payload =
   `finding_hash` + location + optional free-text `reason`; it **never** contains
   the value or the raw token. Reuses `events.log.record_event`.
4. **Retention (Q4):** deferred to slice 5. No purge/TTL in 3b. With model A,
   retention collapses to the source-blob lifecycle (delete the blob ⇒ every secret
   in that run is un-revealable). REQ-S2 is fully satisfied without it.
5. **Write side (Q5):** `analyze._record_secret` stops writing `evidence` for
   SECRET occurrences (keeps offsets). **No backfill** of existing rows (no real
   prod data yet) — so read-side redaction (decision 1) is load-bearing and MUST
   key on `finding.type == secret`, not on "`evidence` is null".

## 3. Architecture & module layout

Additive; no worker/queue/stage changes and **no migration** (decisions 4 & 5).

```
src/recon/probe/
  reveal.py        # reveal_secret(): metadata read (RLS) -> blob slice + integrity
                   #   -> COMMITTED audit -> RevealResult | RevealDenied | None
  reveal_test.py   # colocated (§11)
src/recon/api/probe_router.py    # + POST /runs/{id}/findings/{hash}/reveal
src/recon/findings/queries.py    # redact secret evidence in the view + revealable
src/recon/api/findings_router.py # emit `revealable`
src/recon/findings/analyze.py    # _record_secret: evidence -> None for SECRET
```

Reused as-is: `storage.get_blob`, `normalize.normalize_secret_value`,
`events.log.record_event`, `db.base.tenant_session`.

## 4. Read redaction model (Q1)

Redaction happens in the **read model** (`findings/queries.py`), so the view
object itself never carries secret plaintext (defense in depth — the router can't
leak what the view doesn't hold):

- `_finding_view(finding, run_input_ref, …)` sets, for a SECRET occurrence,
  `OccurrenceView.evidence = None` (keyed on `finding.type == "secret"`). Endpoint
  and param occurrences are unchanged.
- `FindingView.revealable: bool` (default `False`) is set `True` for a SECRET
  finding when **`run.input_ref` is set AND at least one occurrence has both
  offsets**. `list_findings` already loads the `Run`, so `input_ref` is in hand.
- `findings_router` emits `revealable` and (for secrets) a `null` `evidence`.

`revealable: true` means "offsets + a source blob key exist", **not** "the blob is
guaranteed present" — a purged blob still reads `true` here and the reveal call
returns `410`. Accepted (checking object existence per read is a needless network
call; the reveal path is the authority).

## 5. Reveal endpoint model (Q2)

`reveal.reveal_secret(tenant_id, run_id, finding_hash, *, actor=None,
reason=None)` returns a structured outcome; the **router** maps it to HTTP. The
service never raises HTTP, so the audit write is decoupled from the response.

**Step 1 — metadata read (one short `tenant_session`, RLS):** load the run; select
the SECRET finding by `(run_id, finding_hash, type='secret')`; pick the reveal
occurrence deterministically (see below); capture `input_ref`, offsets,
`finding.value`, `attributes["rule"]`, `source_path`, `line`. Run or finding
invisible/absent ⇒ return `None` (router `404`, **no audit** — nothing to reveal;
64-hex hashes make enumeration infeasible, RLS scopes it).

**Step 2 — decide + slice (no DB connection held; blob I/O is out-of-transaction,
mirroring how analyze scans before its staging tx):**
- Chosen occurrence has no offsets ⇒ `DENIED(no_offsets, 422)`.
- `input_ref` is null ⇒ `DENIED(source_gone, 410)`.
- else `get_blob(input_ref)` (on `ClientError`/missing object ⇒ `DENIED(source_gone,
  410)`), then slice in **analyze's exact byte space** (see §6) and run the
  integrity re-check: `normalize_secret_value(slice_text, rule) == finding.value`?
  - mismatch ⇒ `DENIED(integrity, 409)` — refuse; never return a guessed value.
  - match ⇒ `REVEALED(value=slice_text)`.

**Step 3 — committed audit (own `tenant_session`, §7):** record `secret.revealed`
or `secret.reveal_denied`, then the session commits. This runs for **both**
outcomes and completes before the router raises anything.

**Occurrence selection (deterministic):** among the finding's occurrences, take the
first with non-null `offset_start` and `offset_end`, ordered by `(source_path or
"", offset_start or 0, occurrence_hash)` — the same ordering `queries.py` already
uses ([queries.py:160-163](../../../src/recon/findings/queries.py)). All
occurrences of one `finding_hash` decode to the same stripped token (else their
`value` — hence hash — would differ), so any offset-bearing one is correct.

**Router:** `POST …/reveal`, body `{actor?, reason?}`. `None` ⇒ `404`; `DENIED` ⇒
its status (`409`/`410`/`422`); `REVEALED` ⇒ `200 {finding_hash, value,
revealed_at}`.

## 6. Byte-space & integrity (folds in gate findings 2 & 3)

Offsets are computed in analyze against `source = raw.decode("utf-8","replace")`,
via `byte_offset` which returns `len(prefix.encode("utf-8"))`
([analyze.py:75,220](../../../src/recon/findings/analyze.py),
[kingfisher.py:129-148](../../../src/recon/findings/kingfisher.py)). Reveal MUST
slice the **same** space, not the raw blob:

```
raw    = storage.get_blob(input_ref)
source = raw.decode("utf-8", "replace")
data   = source.encode("utf-8")          # the exact bytes byte_offset indexed
slice_ = data[offset_start:offset_end].decode("utf-8", "replace")
```

Slicing raw bytes directly would misalign on any invalid-UTF-8 byte (U+FFFD is 3
bytes), 409-ing valid secrets. The re-check **fails closed**: a mis-slice cannot
match `provider:sha256(token)` without a SHA-256 collision, so a wrong value is
never returned — but a wrong *offset convention* would 409 every valid secret.
`byte_offset`'s docstring says "1-based column" while the code treats column as
0-based (`[:column]`); this is **unverified against real Kingfisher output**, so
§11 makes the real round-trip an integration MUST. If it fails, the fix is a
one-line `byte_offset` correction (occurrence_hash churn is per-run only, so
idempotency is unaffected).

## 7. Audit (Q3, REQ-S3)

`record_event` inside a dedicated `tenant_session` that commits (durable
`run_event`; not published to the Redis SSE fan-out — an audit record, not live
progress, and least-surface). Payload:

```json
{ "type": "secret.revealed",           // or "secret.reveal_denied"
  "payload": { "finding_hash": "…", "actor": "<label|null>",
               "source_path": "input.js", "line": 42,
               "offset_start": 1234, "offset_end": 1270,
               "reason": "<optional>",  "denial": "integrity|source_gone|no_offsets" } }
```

`when` = row `created_at`. **Invariant:** no field derives from the value. `actor`
is the same best-effort, self-asserted label as 3a triage — see §10.

## 8. Write side (Q5) & retention (Q4)

- `analyze._record_secret` builds its `store.Occurrence` with `evidence=None`
  (offsets/line/col/engine/confidence/verified kept). Safe: `evidence` is not part
  of `occurrence_hash` ([store.py:39-52](../../../src/recon/findings/store.py)), so
  idempotency/dedup is unchanged. Offset-less secrets are written `evidence=None`
  too (permanently `revealable:false`; not special-cased — keeping their evidence
  would re-introduce the at-rest plaintext S2 forbids).
- No migration, no backfill, no purge. Retention/TTL + tenant purge are slice 5.

## 9. Security invariants

1. Plaintext lives in exactly one place — the source blob. DB rows carry only
   `provider:sha256` + byte offsets.
2. The value crosses the wire only on the reveal `200` response. Audit events,
   logs, SSE, and coverage events never carry it.
3. Integrity re-check refuses (`409`) on any drift; a wrong value is never returned.
4. Every attempt is audited in a committed transaction before the response
   (success or refusal).
5. No user-controlled storage key reaches `get_blob`; `input_ref` is read under
   RLS and keys embed the tenant, so no cross-tenant blob read.

## 10. Adversarial design-review gate — findings folded in

Gate verdict: **directionally sound, not ship-ready as-drafted**; core posture is
right (re-check fails closed; angles E/D/false-accept/log-&-event-leakage cleared
with evidence). Changes incorporated above:

- **HIGH — denial audit rollback:** `tenant_session` rolls back on a raised
  exception; `record_event` only flushes. → §5/§7: service returns outcomes and
  commits the audit in its own transaction; router raises afterwards.
- **HIGH — offset convention unverified / byte-space mismatch:** → §6 + §11
  integration round-trip MUST.
- **MED — no-backfill makes read-side redaction load-bearing:** → §2.5/§4 redaction
  keys on `finding.type == secret`.
- **MED — occurrence selection underspecified:** → §5 deterministic offset-bearing pick.
- **MED/LOW — spoofable `X-Tenant-Id`, self-asserted `actor`:** accepted platform
  limitation (§10 residual); caps what the audit "who" is worth until per-user auth.
- **LOW — "return value once" misleading:** dropped; the value is re-derived per
  authorized call and never persisted ("ephemeral" = not stored).
- **LOW — kept endpoint snippet may embed a credential:** known residual; S2 targets
  SECRET-typed findings, and such a token is independently flagged as its own SECRET.

## 11. Testing (colocated, §11) & gates (§4)

- `reveal_test.py` (host): happy-path round-trip (blob slice == token, integrity
  passes); **multi-byte UTF-8** secret aligns; integrity mismatch → `409`; missing
  `input_ref` → `410`; blob purged (`get_blob` raises) → `410`; offset-less → `422`;
  not-found / other-tenant (RLS) → `404` with **no** audit; **denial audit COMMITS**
  (assert the `secret.reveal_denied` row exists after a `409`/`410`/`422`); success
  audit carries **no** value.
- findings read tests: SECRET occurrence `evidence` is `null`; `revealable` true iff
  offsets + `input_ref`; endpoint `evidence` still present.
- analyze test: `_record_secret` writes `evidence=None`, offsets kept,
  `occurrence_hash` unchanged (idempotent re-run).
- **INTEGRATION (Docker, real Kingfisher)** — the gate's de-risk: plant a known
  secret, scan, and assert `byte_offset(line, column_start)` → `source.encode()[start:
  end]` decodes to the snippet and `normalize_secret_value` matches `finding.value`.
- **Gates:** adversarial design review — **done** (§10). Higher-model whole-branch
  code review after build. Built TDD, vertical slices, isolated Conventional Commits.

## 12. Out of scope (deferred, tracked)

- **REQ-S4 retention/TTL + tenant purge** → slice 5.
- **Re-scan to relocate offset-less secrets** (make them revealable) → follow-up.
- **Backfill/scrub of pre-3b `evidence` rows** → debt; folds into slice-5 purge.
- **Per-user auth** (a verifiable "who" for the reveal audit) → platform-wide, later.
- **SSE publish of the reveal audit** → persist-only for now.

## 13. Open questions

None blocking. The one risk — Kingfisher's column convention — is de-risked by the
mandatory integration round-trip (§11); if it fails, a one-line `byte_offset` fix.
