# Slice 3b — secret reveal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop serving secret plaintext from the findings API and re-derive it just-in-time on an audited reveal endpoint that slices the run's source blob at the stored byte offsets (REQ-S2).

**Architecture:** Storage model A — the plaintext is never stored; the DB keeps only `provider:sha256` + byte offsets, and the source blob is the single at-rest copy. Read redaction (findings query), a write-side change (analyze stops writing `evidence` for secrets), and a new `recon/probe/reveal.py` service behind a new `POST …/reveal` route. No migration.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy 2.0, Postgres (RLS), Redis, MinIO/S3 (boto3). `pytest` (colocated `*_test.py`).

Design source: `docs/superpowers/specs/2026-07-22-slice3b-secret-reveal-design.md`.

## Global Constraints

- **Package root:** `src/recon`; `pythonpath = ["src"]`; tests are `*_test.py` colocated with source.
- **Test runner:** `./.venv/Scripts/python.exe -m pytest`. Integration tests carry `pytestmark = pytest.mark.integration` and need live infra — `docker compose up -d` (Postgres + Redis + MinIO); the session `migrated` fixture applies `alembic upgrade head` automatically. Every test in this plan touches Postgres and/or object storage, so all are integration-marked.
- **No migration, no backfill (spec Q4/Q5):** the schema is unchanged. Pre-3b rows keep inert plaintext in `evidence`; redaction is enforced **read-side, keyed on `finding.type == "secret"`** (never on "evidence is null").
- **Byte-space invariant (spec §6, gate findings 2 & 3):** offsets are computed in analyze against `raw.decode("utf-8","replace")`. Reveal MUST slice the **same** space: `raw.decode("utf-8","replace").encode("utf-8")[start:end]`, never the raw blob bytes.
- **Never-plaintext invariants (spec §9):** the value crosses the wire only on the reveal `200` body; audit events / logs / SSE never carry it; the integrity re-check refuses (`409`) on any drift and never returns a guessed value.
- **Tenancy (REQ-S1):** all app reads/writes go through `tenant_session(tenant_id)`; RLS is the isolation boundary.
- **Naming (§7):** verb-noun functions, no abbreviations, business-domain names.
- **Commits:** Conventional Commits, isolated per task, on `main`. Do not push (the user pushes explicitly).

---

### Task 1: Write side — analyze stops storing secret plaintext

`analyze._record_secret` currently writes `evidence=secret.snippet` (the raw token). Drop it: keep offsets, write `evidence=None`. Safe because `evidence` is not part of `occurrence_hash` ([store.py:39-52](../../../src/recon/findings/store.py)), so idempotency is unchanged.

**Files:**
- Modify: `src/recon/findings/analyze.py` (`_record_secret`, ~lines 217-235)
- Test: `src/recon/findings/analyze_secret_redaction_test.py`

**Interfaces:**
- Consumes: `analyze._record_secret(session, tenant_id, run_id, path, source, secret)` (existing private helper), `recon.findings.kingfisher.RawSecret`.
- Produces: no signature change; a SECRET `finding_occurrence` row now has `evidence IS NULL` with offsets set.

- [ ] **Step 1: Write the failing test**

Create `src/recon/findings/analyze_secret_redaction_test.py`:

```python
import pytest

from recon.db import models
from recon.db.base import tenant_session
from recon.findings import analyze
from recon.findings.kingfisher import RawSecret
from recon.sessions import service as sessions_service

pytestmark = pytest.mark.integration


def test_record_secret_stores_offsets_but_not_plaintext():
    tenant = sessions_service.create_tenant("wsec-1")
    view = sessions_service.create_session(
        tenant, name="e", scope_hosts=["acme.io"], authorized_by="t"
    )
    token = "sk_" + "live_" + "AKIAEXAMPLE1234567890"
    source = f'const k = "{token}";\n'
    secret = RawSecret(
        rule_id="stripe", rule_name="Stripe", snippet=token,
        line=1, column_start=source.index(token),
    )
    with tenant_session(tenant) as session:
        run = models.Run(tenant_id=tenant, session_id=view.id, state="done")
        session.add(run)
        session.flush()
        run_id = str(run.id)
        analyze._record_secret(session, tenant, run_id, "input.js", source, secret)

    with tenant_session(tenant) as session:
        occurrence = session.query(models.FindingOccurrence).one()
        assert occurrence.evidence is None  # model A: no plaintext at rest
        assert occurrence.offset_start is not None and occurrence.offset_end is not None
        # the stored offsets bound the token in the source's byte space
        sliced = source.encode("utf-8")[occurrence.offset_start:occurrence.offset_end]
        assert sliced.decode("utf-8") == token
```

- [ ] **Step 2: Run test to verify it fails**

Ensure infra up: `docker compose up -d`
Run: `./.venv/Scripts/python.exe -m pytest src/recon/findings/analyze_secret_redaction_test.py -v`
Expected: FAIL with `assert occurrence.evidence is None` (evidence currently holds the token).

- [ ] **Step 3: Drop the plaintext write**

In `src/recon/findings/analyze.py`, replace the body of `_record_secret` (currently ~lines 217-235) with:

```python
def _record_secret(session, tenant_id: str, run_id: str, path: str, source: str, secret: RawSecret) -> int:
    # value = provider:sha256(token) — the raw token is never hashed in cleartext.
    value = normalize.normalize_secret_value(secret.snippet, secret.rule_id)
    offset = kingfisher.byte_offset(source, secret.line, secret.column_start)
    offset_end = offset + len(secret.snippet.encode("utf-8")) if offset is not None else None
    # REQ-S2 (storage model A): the raw secret is NOT stored. We keep only the
    # identity hash (finding.value) + byte offsets; the plaintext is re-derived
    # just-in-time from the source blob on an audited reveal (recon.probe.reveal),
    # so the platform is never a concentrated store of live credentials. Offsets
    # are computed against source == raw.decode("utf-8","replace"); reveal MUST
    # slice that same byte space (see recon.probe.reveal).
    return _write(
        session, tenant_id, run_id, FindingType.SECRET, value, path,
        occurrence=store.Occurrence(
            source_path=_SOURCE_NAME, line=secret.line, col=secret.column_start,
            offset_start=offset, offset_end=offset_end,
            engine="kingfisher", confidence=secret.confidence,
            verified=True if secret.validation_status == "Active" else None,
        ),
        attributes={"rule": secret.rule_id, "name": secret.rule_name},
    )
```

(The only change is removing the `evidence=secret.snippet,` argument and updating the NOTE comment; `store.Occurrence.evidence` defaults to `None`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/findings/analyze_secret_redaction_test.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add src/recon/findings/analyze.py src/recon/findings/analyze_secret_redaction_test.py
git commit -m "feat(secret): stop storing secret plaintext in occurrence.evidence (REQ-S2 model A, slice 3b)"
```

---

### Task 2: Read side — redact secret evidence + add `revealable`

`GET /runs/{id}/findings` must stop serving `evidence` for SECRET occurrences and add a per-finding `revealable` flag. Redaction happens in the read model so the view object never carries secret plaintext (defense in depth).

**Files:**
- Modify: `src/recon/findings/queries.py` (`FindingView`, `_occurrence_view`, `_finding_view`, `list_findings`)
- Modify: `src/recon/api/findings_router.py` (emit `revealable`)
- Test: `src/recon/findings/queries_reveal_redaction_test.py`

**Interfaces:**
- Consumes: `models.Run.input_ref`, `recon.domain.FindingType`.
- Produces: `FindingView.revealable: bool` (default `False`); SECRET `OccurrenceView.evidence` is always `None`.

- [ ] **Step 1: Write the failing tests**

Create `src/recon/findings/queries_reveal_redaction_test.py`:

```python
import pytest

from recon.db import models
from recon.db.base import tenant_session
from recon.domain import FindingType
from recon.findings import normalize, queries, store
from recon.sessions import service as sessions_service

pytestmark = pytest.mark.integration

_TOKEN = "sk_" + "live_" + "SECRETVALUE00"


def _run(tenant, session_id, *, input_ref):
    with tenant_session(tenant) as session:
        run = models.Run(
            tenant_id=tenant, session_id=session_id, state="done", input_ref=input_ref
        )
        session.add(run)
        session.flush()
        return str(run.id)


def _add_secret(tenant, run_id, *, offsets):
    value = normalize.normalize_secret_value(_TOKEN, "stripe")
    with tenant_session(tenant) as session:
        store.record_finding(
            session, tenant_id=tenant, run_id=run_id, finding_type=FindingType.SECRET,
            value=value, path="input.js",
            occurrence=store.Occurrence(
                source_path="input.js", line=1, col=0,
                offset_start=offsets[0] if offsets else None,
                offset_end=offsets[1] if offsets else None,
                evidence=_TOKEN,  # a legacy-style plaintext row: must be redacted at read
                engine="kingfisher",
            ),
            attributes={"rule": "stripe", "name": "Stripe"},
        )
    return value


def test_secret_evidence_redacted_and_revealable_true():
    tenant = sessions_service.create_tenant("rd-1")
    _t, session_id = tenant, sessions_service.create_session(
        tenant, name="e", scope_hosts=["acme.io"], authorized_by="t"
    ).id
    run_id = _run(tenant, session_id, input_ref=f"{tenant}/x/input/deadbeef")
    secret_hash = _add_secret(tenant, run_id, offsets=(10, 30))

    result = queries.list_findings(tenant, run_id)
    secret = next(f for f in result.findings if f.finding_hash == secret_hash)
    assert secret.revealable is True
    assert all(o.evidence is None for o in secret.occurrences)  # redacted at read


def test_secret_not_revealable_without_offsets_or_blob():
    tenant = sessions_service.create_tenant("rd-2")
    session_id = sessions_service.create_session(
        tenant, name="e", scope_hosts=["acme.io"], authorized_by="t"
    ).id

    run_no_blob = _run(tenant, session_id, input_ref=None)
    h1 = _add_secret(tenant, run_no_blob, offsets=(10, 30))
    r1 = queries.list_findings(tenant, run_no_blob)
    assert next(f for f in r1.findings if f.finding_hash == h1).revealable is False

    run_no_offsets = _run(tenant, session_id, input_ref=f"{tenant}/y/input/beef")
    h2 = _add_secret(tenant, run_no_offsets, offsets=None)
    r2 = queries.list_findings(tenant, run_no_offsets)
    assert next(f for f in r2.findings if f.finding_hash == h2).revealable is False


def test_endpoint_evidence_is_preserved():
    tenant = sessions_service.create_tenant("rd-3")
    session_id = sessions_service.create_session(
        tenant, name="e", scope_hosts=["acme.io"], authorized_by="t"
    ).id
    run_id = _run(tenant, session_id, input_ref=None)
    with tenant_session(tenant) as session:
        store.record_finding(
            session, tenant_id=tenant, run_id=run_id, finding_type=FindingType.ENDPOINT,
            value="GET /orders", path="input.js",
            occurrence=store.Occurrence(
                host="api.acme.io", raw_url="/orders",
                evidence='fetch("/orders")', engine="vespasian",
            ),
            attributes={"method": "GET", "kind": "fetch"},
        )
    result = queries.list_findings(tenant, run_id)
    endpoint = next(f for f in result.findings if f.type == "endpoint")
    assert endpoint.occurrences[0].evidence == 'fetch("/orders")'
    assert endpoint.revealable is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/findings/queries_reveal_redaction_test.py -v`
Expected: FAIL with `AttributeError: 'FindingView' object has no attribute 'revealable'`

- [ ] **Step 3: Redact + compute `revealable` in the read model**

In `src/recon/findings/queries.py`:

Add the import (near the top, after the models import):

```python
from recon.domain import FindingType
```

Add a defaulted `revealable` field to `FindingView` (append as the last field so existing constructors keep working):

```python
    occurrences: list[OccurrenceView]
    triage: TriageView | None = None
    revealable: bool = False
```

Pass the run's `input_ref` into `_finding_view` from `list_findings` (change only the list comprehension):

```python
            findings=[
                _finding_view(
                    finding, triage_by_hash.get(finding.finding_hash), run.input_ref
                )
                for finding in findings
            ],
```

Replace `_finding_view` and `_occurrence_view` with:

```python
def _finding_view(
    finding: Finding,
    triage_row: FindingTriage | None = None,
    run_input_ref: str | None = None,
) -> FindingView:
    # REQ-S2: a secret's raw evidence is never served; the value comes only from the
    # audited reveal endpoint. Endpoint/param evidence (a code snippet) is kept.
    is_secret = finding.type == FindingType.SECRET.value
    occurrences = [
        _occurrence_view(occurrence, redact_evidence=is_secret)
        for occurrence in sorted(
            finding.occurrences,
            key=lambda o: (o.source_path or "", o.offset_start or 0, o.occurrence_hash),
        )
    ]
    revealable = bool(
        is_secret
        and run_input_ref
        and any(
            o.offset_start is not None and o.offset_end is not None for o in occurrences
        )
    )
    return FindingView(
        finding_hash=finding.finding_hash,
        type=finding.type,
        value=finding.value,
        path=finding.path,
        severity=finding.severity,
        attributes=dict(finding.attributes or {}),
        first_stage=finding.first_stage,
        occurrences=occurrences,
        triage=_triage_view(triage_row),
        revealable=revealable,
    )


def _occurrence_view(
    occurrence: FindingOccurrence, redact_evidence: bool = False
) -> OccurrenceView:
    return OccurrenceView(
        host=occurrence.host,
        raw_url=occurrence.raw_url,
        source_path=occurrence.source_path,
        line=occurrence.line,
        col=occurrence.col,
        offset_start=occurrence.offset_start,
        offset_end=occurrence.offset_end,
        evidence=None if redact_evidence else occurrence.evidence,
        engine=occurrence.engine,
        confidence=occurrence.confidence,
        verified=occurrence.verified,
    )
```

- [ ] **Step 4: Emit `revealable` in the findings router**

In `src/recon/api/findings_router.py`, add a `revealable` key to the per-finding dict (right after `"first_stage": finding.first_stage,`):

```python
                "first_stage": finding.first_stage,
                "revealable": finding.revealable,
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/findings/queries_reveal_redaction_test.py -v`
Expected: PASS (3 passed)

Confirm the existing findings suite still passes (additive change):
Run: `./.venv/Scripts/python.exe -m pytest src/recon/findings/queries_test.py src/recon/findings/queries_triage_test.py -v`
Expected: PASS (unchanged)

- [ ] **Step 6: Commit**

```bash
git add src/recon/findings/queries.py src/recon/api/findings_router.py src/recon/findings/queries_reveal_redaction_test.py
git commit -m "feat(secret): redact secret evidence on the findings read + add revealable flag (slice 3b)"
```

---

### Task 3: Reveal service (`recon/probe/reveal.py`)

The core: metadata read (RLS) → blob slice in analyze's byte space → integrity re-check → committed audit for **every** attempt → structured outcome. HTTP-agnostic (the router maps outcomes to status codes in Task 4).

**Files:**
- Create: `src/recon/probe/reveal.py`
- Test: `src/recon/probe/reveal_test.py`

**Interfaces:**
- Consumes: `storage.get_blob`, `models.Run`/`Finding`/`FindingOccurrence`, `FindingType.SECRET`, `normalize.normalize_secret_value`, `events.log.record_event`, `kingfisher.byte_offset` (tests only).
- Produces: `DENIAL_STATUS: dict[str, int]`; `RevealOutcome(revealed: bool, value: str | None = None, denial: str | None = None)`; `reveal_secret(tenant_id: str, run_id: str, finding_hash: str, *, actor: str | None = None, reason: str | None = None) -> RevealOutcome | None` (`None` ⇒ run/secret invisible or not a secret).

- [ ] **Step 1: Write the failing tests**

Create `src/recon/probe/reveal_test.py`:

```python
import json

import pytest

from recon import storage
from recon.db import models
from recon.db.base import tenant_session
from recon.domain import FindingType
from recon.findings import kingfisher, normalize, store
from recon.probe import reveal
from recon.sessions import service as sessions_service

pytestmark = pytest.mark.integration


def _new_run(tenant, session_id):
    with tenant_session(tenant) as session:
        run = models.Run(tenant_id=tenant, session_id=session_id, state="done")
        session.add(run)
        session.flush()
        return str(run.id)


def _set_input_ref(tenant, run_id, input_ref):
    with tenant_session(tenant) as session:
        session.get(models.Run, run_id).input_ref = input_ref


def _add_secret_finding(tenant, run_id, *, value, source, token, offsets="auto"):
    if offsets == "auto":
        offset_start = kingfisher.byte_offset(source, 1, source.index(token))
        offset_end = offset_start + len(token.encode("utf-8"))
    else:
        offset_start, offset_end = (None, None) if offsets is None else offsets
    with tenant_session(tenant) as session:
        result = store.record_finding(
            session, tenant_id=tenant, run_id=run_id, finding_type=FindingType.SECRET,
            value=value, path="input.js",
            occurrence=store.Occurrence(
                source_path="input.js", line=1, col=0,
                offset_start=offset_start, offset_end=offset_end, engine="kingfisher",
            ),
            attributes={"rule": "stripe", "name": "Stripe"},
        )
        return result.finding_hash


def _events(tenant, run_id, event_type):
    with tenant_session(tenant) as session:
        return (
            session.query(models.RunEvent)
            .filter_by(run_id=run_id, type=event_type)
            .all()
        )


def _seed(tenant, session_id, *, token, source=None, offsets="auto", value=None, input_ref="auto"):
    source = source if source is not None else f'const k = "{token}";\n'
    value = value if value is not None else normalize.normalize_secret_value(token, "stripe")
    run_id = _new_run(tenant, session_id)
    if input_ref == "auto":
        input_ref = storage.put_blob(tenant, run_id, "input", source.encode("utf-8"))
    _set_input_ref(tenant, run_id, input_ref)
    finding_hash = _add_secret_finding(
        tenant, run_id, value=value, source=source, token=token, offsets=offsets
    )
    return run_id, finding_hash


def test_reveal_happy_path_returns_value_and_audits_without_leaking():
    tenant = sessions_service.create_tenant("rv-1")
    session_id = sessions_service.create_session(
        tenant, name="e", scope_hosts=["acme.io"], authorized_by="t"
    ).id
    token = "sk_" + "live_" + "PLAINTEXT000"
    run_id, finding_hash = _seed(tenant, session_id, token=token)

    outcome = reveal.reveal_secret(tenant, run_id, finding_hash, actor="tester", reason="validate")
    assert outcome is not None and outcome.revealed is True
    assert outcome.value == token

    (event,) = _events(tenant, run_id, "secret.revealed")
    assert event.payload["finding_hash"] == finding_hash
    assert "value" not in event.payload
    assert token not in json.dumps(event.payload)  # audit never carries the secret


def test_reveal_aligns_offsets_through_invalid_utf8_bytes():
    # A stray non-UTF-8 byte before the token: analyze computed offsets on the
    # decode("utf-8","replace") string, so reveal must slice that same space.
    tenant = sessions_service.create_tenant("rv-2")
    session_id = sessions_service.create_session(
        tenant, name="e", scope_hosts=["acme.io"], authorized_by="t"
    ).id
    token = "sk_" + "live_" + "MULTIBYTE00"
    raw = b"// \xff\nconst k = \"" + token.encode("utf-8") + b"\";\n"
    source = raw.decode("utf-8", "replace")
    line = 2
    col = source.split("\n")[1].index(token)
    offset = kingfisher.byte_offset(source, line, col)
    value = normalize.normalize_secret_value(token, "stripe")

    run_id = _new_run(tenant, session_id)
    input_ref = storage.put_blob(tenant, run_id, "input", raw)
    _set_input_ref(tenant, run_id, input_ref)
    with tenant_session(tenant) as session:
        result = store.record_finding(
            session, tenant_id=tenant, run_id=run_id, finding_type=FindingType.SECRET,
            value=value, path="input.js",
            occurrence=store.Occurrence(
                source_path="input.js", line=line, col=col,
                offset_start=offset, offset_end=offset + len(token.encode("utf-8")),
                engine="kingfisher",
            ),
            attributes={"rule": "stripe", "name": "Stripe"},
        )
        finding_hash = result.finding_hash

    outcome = reveal.reveal_secret(tenant, run_id, finding_hash)
    assert outcome.revealed is True and outcome.value == token


def test_reveal_integrity_mismatch_refuses_and_audits_denied():
    tenant = sessions_service.create_tenant("rv-3")
    session_id = sessions_service.create_session(
        tenant, name="e", scope_hosts=["acme.io"], authorized_by="t"
    ).id
    token = "sk_" + "live_" + "REALTOKEN00"
    # Store the identity of a DIFFERENT token, so slicing the blob won't hash-match.
    wrong_value = normalize.normalize_secret_value("sk_live_OTHER", "stripe")
    run_id, finding_hash = _seed(tenant, session_id, token=token, value=wrong_value)

    outcome = reveal.reveal_secret(tenant, run_id, finding_hash)
    assert outcome.revealed is False and outcome.denial == "integrity"
    assert reveal.DENIAL_STATUS["integrity"] == 409
    assert len(_events(tenant, run_id, "secret.reveal_denied")) == 1


def test_reveal_missing_input_ref_is_source_gone():
    tenant = sessions_service.create_tenant("rv-4")
    session_id = sessions_service.create_session(
        tenant, name="e", scope_hosts=["acme.io"], authorized_by="t"
    ).id
    run_id, finding_hash = _seed(
        tenant, session_id, token="sk_" + "live_" + "X0", input_ref=None
    )
    outcome = reveal.reveal_secret(tenant, run_id, finding_hash)
    assert outcome.revealed is False and outcome.denial == "source_gone"
    assert reveal.DENIAL_STATUS["source_gone"] == 410
    assert len(_events(tenant, run_id, "secret.reveal_denied")) == 1


def test_reveal_purged_blob_is_source_gone():
    tenant = sessions_service.create_tenant("rv-5")
    session_id = sessions_service.create_session(
        tenant, name="e", scope_hosts=["acme.io"], authorized_by="t"
    ).id
    run_id, finding_hash = _seed(
        tenant, session_id, token="sk_" + "live_" + "Y0",
        input_ref="doesnotexist/run/input/deadbeef",
    )
    outcome = reveal.reveal_secret(tenant, run_id, finding_hash)
    assert outcome.revealed is False and outcome.denial == "source_gone"


def test_reveal_offsetless_secret_is_denied():
    tenant = sessions_service.create_tenant("rv-6")
    session_id = sessions_service.create_session(
        tenant, name="e", scope_hosts=["acme.io"], authorized_by="t"
    ).id
    run_id, finding_hash = _seed(
        tenant, session_id, token="sk_" + "live_" + "Z0", offsets=None
    )
    outcome = reveal.reveal_secret(tenant, run_id, finding_hash)
    assert outcome.revealed is False and outcome.denial == "no_offsets"
    assert reveal.DENIAL_STATUS["no_offsets"] == 422
    assert len(_events(tenant, run_id, "secret.reveal_denied")) == 1


def test_reveal_unknown_or_other_tenant_returns_none_without_audit():
    tenant = sessions_service.create_tenant("rv-7")
    other = sessions_service.create_tenant("rv-7-other")
    session_id = sessions_service.create_session(
        tenant, name="e", scope_hosts=["acme.io"], authorized_by="t"
    ).id
    run_id, finding_hash = _seed(tenant, session_id, token="sk_" + "live_" + "Q0")

    assert reveal.reveal_secret(tenant, run_id, "f" * 64) is None  # no such finding
    assert reveal.reveal_secret(other, run_id, finding_hash) is None  # RLS: run invisible
    assert _events(tenant, run_id, "secret.reveal_denied") == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/probe/reveal_test.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'recon.probe.reveal'`

- [ ] **Step 3: Implement the reveal service**

Create `src/recon/probe/reveal.py`:

```python
"""Just-in-time secret reveal (REQ-S2, storage model A).

The plaintext is never stored — it lives only in the run's source blob. A reveal
reads that blob, slices the bytes the occurrence recorded, re-checks the
``provider:sha256`` against the finding identity (refuse on ANY drift), and returns
the value. Every attempt is audit-logged in its own committed transaction, so a
denial is durably recorded even though the API layer then raises.
"""

from __future__ import annotations

from dataclasses import dataclass

from botocore.exceptions import ClientError
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from recon import storage
from recon.db import models
from recon.db.base import tenant_session
from recon.domain import FindingType
from recon.events.log import record_event
from recon.findings import normalize

# Denial code -> HTTP status. The taxonomy lives with the service that produces it;
# the router (recon.api.probe_router) maps the code to a response.
DENIAL_STATUS: dict[str, int] = {
    "no_offsets": 422,   # the secret has no byte location — un-revealable (rare)
    "source_gone": 410,  # the source blob is absent (never set, or purged)
    "integrity": 409,    # the slice no longer hashes to the finding identity
}


@dataclass(frozen=True)
class RevealOutcome:
    revealed: bool
    value: str | None = None
    denial: str | None = None  # one of DENIAL_STATUS when not revealed


@dataclass(frozen=True)
class _Target:
    """Plain data captured under RLS so the blob/slice work holds no DB connection."""

    input_ref: str | None
    rule: str
    value: str
    offset_start: int | None
    offset_end: int | None
    source_path: str | None
    line: int | None


def reveal_secret(
    tenant_id: str,
    run_id: str,
    finding_hash: str,
    *,
    actor: str | None = None,
    reason: str | None = None,
) -> RevealOutcome | None:
    """Re-derive one secret's plaintext from the source blob. ``None`` when the run
    or the SECRET finding is invisible to the tenant (RLS) — the router maps that to
    404, and nothing is audited (there is nothing to reveal)."""
    target = _load_target(tenant_id, run_id, finding_hash)
    if target is None:
        return None

    outcome = _derive(target)
    event_type = "secret.revealed" if outcome.revealed else "secret.reveal_denied"
    with tenant_session(tenant_id) as session:  # own transaction -> commits on exit
        record_event(
            session,
            tenant_id=str(tenant_id),
            run_id=str(run_id),
            event_type=event_type,
            payload={
                "finding_hash": finding_hash,
                "actor": actor,
                "reason": reason,
                "denial": outcome.denial,
                "source_path": target.source_path,
                "line": target.line,
                "offset_start": target.offset_start,
                "offset_end": target.offset_end,
            },
        )
    return outcome


def _load_target(tenant_id: str, run_id: str, finding_hash: str) -> _Target | None:
    with tenant_session(tenant_id) as session:
        run = session.get(models.Run, run_id)
        if run is None:
            return None
        finding = session.scalars(
            select(models.Finding)
            .where(
                models.Finding.run_id == str(run_id),
                models.Finding.finding_hash == finding_hash,
                models.Finding.type == FindingType.SECRET.value,
            )
            .options(selectinload(models.Finding.occurrences))
        ).first()
        if finding is None:
            return None
        occurrence = _reveal_occurrence(finding.occurrences)
        return _Target(
            input_ref=run.input_ref,
            rule=str((finding.attributes or {}).get("rule", "")),
            value=finding.value,
            offset_start=None if occurrence is None else occurrence.offset_start,
            offset_end=None if occurrence is None else occurrence.offset_end,
            source_path=None if occurrence is None else occurrence.source_path,
            line=None if occurrence is None else occurrence.line,
        )


def _reveal_occurrence(occurrences):
    """The deterministic-first occurrence that carries byte offsets, or ``None``.

    All occurrences of one finding_hash decode to the same stripped token, so any
    offset-bearing one is correct; the ordering matches ``queries.py`` for stability."""
    candidates = [
        o for o in occurrences
        if o.offset_start is not None and o.offset_end is not None
    ]
    if not candidates:
        return None
    return sorted(
        candidates,
        key=lambda o: (o.source_path or "", o.offset_start or 0, o.occurrence_hash),
    )[0]


def _derive(target: _Target) -> RevealOutcome:
    if target.offset_start is None or target.offset_end is None:
        return RevealOutcome(revealed=False, denial="no_offsets")
    if not target.input_ref:
        return RevealOutcome(revealed=False, denial="source_gone")
    try:
        raw = storage.get_blob(target.input_ref)
    except ClientError:
        return RevealOutcome(revealed=False, denial="source_gone")
    # Slice in the SAME byte space the offsets were computed in: analyze decodes the
    # blob with utf-8/replace before byte_offset, so a stray non-UTF-8 byte would
    # shift raw-byte offsets. Re-encoding the replaced string reproduces that space.
    data = raw.decode("utf-8", "replace").encode("utf-8")
    sliced = data[target.offset_start:target.offset_end].decode("utf-8", "replace")
    if normalize.normalize_secret_value(sliced, target.rule) != target.value:
        return RevealOutcome(revealed=False, denial="integrity")
    return RevealOutcome(revealed=True, value=sliced)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/probe/reveal_test.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add src/recon/probe/reveal.py src/recon/probe/reveal_test.py
git commit -m "feat(secret): just-in-time reveal service — blob slice + integrity + committed audit (slice 3b)"
```

---

### Task 4: Reveal API route + wiring

Add `POST /runs/{run_id}/findings/{finding_hash}/reveal` to the existing probe router (already registered in `app.py`). Thin: it maps `reveal_secret`'s outcome to a status code.

**Files:**
- Modify: `src/recon/api/probe_router.py` (add `RevealRequest` + the route + `reveal` import)
- Test: `src/recon/api/probe_reveal_router_test.py`

**Interfaces:**
- Consumes: `reveal.reveal_secret`, `reveal.DENIAL_STATUS` (Task 3), `get_tenant_id` (existing).
- Produces: route `POST /runs/{run_id}/findings/{finding_hash}/reveal`; `200 {finding_hash, value}` / `404` / `409` / `410` / `422`.

- [ ] **Step 1: Write the failing tests**

Create `src/recon/api/probe_reveal_router_test.py`:

```python
import pytest
from fastapi.testclient import TestClient

from recon import storage
from recon.api.app import create_app
from recon.db import models
from recon.db.base import tenant_session
from recon.domain import FindingType
from recon.findings import kingfisher, normalize, store
from recon.sessions import service as sessions_service

pytestmark = pytest.mark.integration


@pytest.fixture()
def client():
    return TestClient(create_app())


def _headers(tenant):
    return {"X-Tenant-Id": tenant}


def _seed_revealable(tenant, session_id, token):
    source = f'const k = "{token}";\n'
    value = normalize.normalize_secret_value(token, "stripe")
    with tenant_session(tenant) as session:
        run = models.Run(tenant_id=tenant, session_id=session_id, state="done")
        session.add(run)
        session.flush()
        run_id = str(run.id)
    input_ref = storage.put_blob(tenant, run_id, "input", source.encode("utf-8"))
    offset = kingfisher.byte_offset(source, 1, source.index(token))
    with tenant_session(tenant) as session:
        session.get(models.Run, run_id).input_ref = input_ref
    with tenant_session(tenant) as session:
        result = store.record_finding(
            session, tenant_id=tenant, run_id=run_id, finding_type=FindingType.SECRET,
            value=value, path="input.js",
            occurrence=store.Occurrence(
                source_path="input.js", line=1, col=0,
                offset_start=offset, offset_end=offset + len(token.encode("utf-8")),
                engine="kingfisher",
            ),
            attributes={"rule": "stripe", "name": "Stripe"},
        )
        return run_id, result.finding_hash


def test_reveal_route_returns_value(client, authorized_session):
    tenant, session_id = authorized_session
    token = "sk_" + "live_" + "ROUTEVALUE0"
    run_id, finding_hash = _seed_revealable(tenant, session_id, token)
    resp = client.post(
        f"/runs/{run_id}/findings/{finding_hash}/reveal",
        json={"actor": "tester", "reason": "validate"},
        headers=_headers(tenant),
    )
    assert resp.status_code == 200
    assert resp.json()["value"] == token


def test_reveal_route_offsetless_is_422(client, authorized_session):
    tenant, session_id = authorized_session
    with tenant_session(tenant) as session:
        run = models.Run(tenant_id=tenant, session_id=session_id, state="done",
                         input_ref="t/r/input/x")
        session.add(run)
        session.flush()
        run_id = str(run.id)
        result = store.record_finding(
            session, tenant_id=tenant, run_id=run_id, finding_type=FindingType.SECRET,
            value=normalize.normalize_secret_value("sk_live_NOPE", "stripe"), path="input.js",
            occurrence=store.Occurrence(source_path="input.js", line=1, col=0, engine="kingfisher"),
            attributes={"rule": "stripe", "name": "Stripe"},
        )
        finding_hash = result.finding_hash
    resp = client.post(
        f"/runs/{run_id}/findings/{finding_hash}/reveal", json={}, headers=_headers(tenant)
    )
    assert resp.status_code == 422


def test_reveal_route_unknown_run_is_404(client, tenant):
    resp = client.post(
        "/runs/00000000-0000-0000-0000-000000000000/findings/" + "a" * 64 + "/reveal",
        json={}, headers=_headers(tenant),
    )
    assert resp.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/api/probe_reveal_router_test.py -v`
Expected: FAIL (404 on the reveal route for the happy-path test — route not registered).

- [ ] **Step 3: Add the route**

In `src/recon/api/probe_router.py`, extend the import to include `reveal`:

```python
from recon.probe import reconstruct, reveal, serialize, triage
```

Add the request model next to `TriageRequest`:

```python
class RevealRequest(BaseModel):
    actor: str | None = None
    reason: str | None = None
```

Add the route (after `set_finding_triage`):

```python
@router.post("/runs/{run_id}/findings/{finding_hash}/reveal")
def reveal_secret_value(
    run_id: str,
    finding_hash: str,
    body: RevealRequest | None = None,
    tenant_id: str = Depends(get_tenant_id),
) -> dict:
    body = body or RevealRequest()
    outcome = reveal.reveal_secret(
        tenant_id, run_id, finding_hash, actor=body.actor, reason=body.reason
    )
    if outcome is None:
        raise HTTPException(status_code=404, detail="run or secret not found")
    if not outcome.revealed:
        raise HTTPException(
            status_code=reveal.DENIAL_STATUS[outcome.denial],
            detail=f"cannot reveal secret: {outcome.denial}",
        )
    return {"finding_hash": finding_hash, "value": outcome.value}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/api/probe_reveal_router_test.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/recon/api/probe_router.py src/recon/api/probe_reveal_router_test.py
git commit -m "feat(secret): POST reveal route mapping outcomes to 200/404/409/410/422 (slice 3b)"
```

---

### Task 5: Real-Kingfisher offset round-trip (design-gate de-risk)

The gate's HIGH concern: the derived byte offset (`byte_offset`, whose docstring says "1-based column" while the code treats column 0-based) is unverified against real Kingfisher output. This test runs the **real** engine end-to-end and proves a reveal round-trips. Docker-only; skips when the binary is absent.

**Files:**
- Test: `src/recon/probe/reveal_roundtrip_test.py`

**Interfaces:**
- Consumes: `analyze.analyze_run`, `kingfisher.scan`, `queries.list_findings`, `reveal.reveal_secret`, `storage.put_blob`.

- [ ] **Step 1: Write the test**

Create `src/recon/probe/reveal_roundtrip_test.py`:

```python
import pytest

from recon import storage
from recon.db import models
from recon.db.base import tenant_session
from recon.findings import analyze, kingfisher, normalize, queries
from recon.probe import reveal
from recon.sessions import service as sessions_service

pytestmark = pytest.mark.integration


def test_reveal_roundtrips_real_kingfisher_offsets(redis, authorized_session, engines_required):
    tenant, session_id = authorized_session
    # Split literals so no secret-shaped token is committed; kingfisher reassembles.
    token = "sk_" + "live_" + "4eC39HqLyjWDarjtT1zdp7dc" + "ABCDEF0123"
    js = f'const apiKey = "{token}";\nfetch("/api/ping");\n'
    if kingfisher.scan(js.encode("utf-8")).status == "unavailable":
        if engines_required:
            pytest.fail("kingfisher binary required (RECON_REQUIRE_ENGINES) but unavailable")
        pytest.skip("kingfisher binary not available")

    with tenant_session(tenant) as session:
        run = models.Run(tenant_id=tenant, session_id=session_id, state="done")
        session.add(run)
        session.flush()
        run_id = str(run.id)
    input_ref = storage.put_blob(tenant, run_id, "input", js.encode("utf-8"))
    with tenant_session(tenant) as session:
        session.get(models.Run, run_id).input_ref = input_ref

    analyze.analyze_run(redis, tenant_id=tenant, run_id=run_id)

    result = queries.list_findings(tenant, run_id)
    secret = next(f for f in result.findings if f.type == "secret")
    assert secret.revealable is True

    # The round-trip: real Kingfisher line/column -> byte_offset -> blob slice ->
    # provider:sha256 must match. A 409 here means byte_offset's column convention
    # is wrong for the real engine (see contingency below), NOT that reveal is broken.
    outcome = reveal.reveal_secret(tenant, run_id, secret.finding_hash)
    assert outcome is not None and outcome.revealed is True
    assert token in outcome.value
    assert normalize.strip_secret_delimiters(outcome.value) == token
```

- [ ] **Step 2: Run against the real binary (Docker)**

Run in the container (real Kingfisher), forcing engine presence:
```bash
docker compose up -d
docker compose run --rm -e RECON_REQUIRE_ENGINES=1 --user root api \
  ./.venv/bin/python -m pytest src/recon/probe/reveal_roundtrip_test.py -v
```
Expected: PASS (1 passed).

**Contingency (expected per the design gate):** if this fails with `outcome.denial == "integrity"`, the derived offset is off — Kingfisher's `column_start` convention does not match `byte_offset`'s `[:column]` assumption ([kingfisher.py:129-148](../../../src/recon/findings/kingfisher.py)). Fix `byte_offset`'s column handling (most likely a 1-based → 0-based `column - 1` adjustment), re-run this test **and** `src/recon/findings/kingfisher_test.py` (adjust its offset fixtures to the corrected convention), until both are green. Keep the fix to `byte_offset` only; occurrence-hash churn from an offset change is per-run and harmless.

- [ ] **Step 3: Commit**

```bash
git add src/recon/probe/reveal_roundtrip_test.py
git commit -m "test(secret): real-Kingfisher offset round-trip guards the JIT reveal (slice 3b)"
```
(Include any `byte_offset` fix from the contingency in this commit with an explanatory body.)

---

## Review gates (after the tasks, per workflow §4)

1. **Adversarial design review** — **already run** at the design stage; findings folded into the spec (§10) and this plan. No second design gate needed unless the build deviates from the spec.
2. **Higher-model code review** — hand the full slice-3b diff to a more capable model before the slice is considered done. Focus: the audit-commit-before-raise transaction structure (gate finding 1), the byte-space slice (gate findings 2 & 3), redaction completeness (no secret leaks via any response/log/event), and RLS scoping of the reveal read.

Then run the full suite against live infra (`./.venv/Scripts/python.exe -m pytest -m ""`) and in-container with `RECON_REQUIRE_ENGINES=1`; state each gate verdict + evidence to the user; fix highs before closing. Finally update the `slice3-progress` memory + `HANDOFF-recon-backend.md` + the slice-2 deferred-debt doc (retarget the S2-reveal row to "done"; add the offset-less-secret re-scan, the pre-3b evidence backfill, and SSE-publish-of-reveal-audit as tracked debt).

## Self-review notes (checked against the spec)

- **Spec coverage:** §2.1 read redaction → Task 2; §2.2 reveal endpoint → Tasks 3 (service) + 4 (route); §2.3 audit → Task 3 (`record_event` in a committed txn; `secret.revealed`/`secret.reveal_denied`, value-free payload) + tested in Tasks 3/4; §2.4 retention → not built (deferred, per spec); §2.5 write side → Task 1; §6 byte-space + §11 integration round-trip → Tasks 3 (`test_reveal_aligns_offsets_through_invalid_utf8_bytes`) + 5 (real binary); §9 invariants → asserted across Tasks 2/3 (redaction, value-free audit, fail-closed integrity, committed denial audit). No migration (Q4/Q5) — confirmed no Task creates one.
- **Placeholder scan:** none — every code/test step carries complete code and exact commands.
- **Type consistency:** `RevealOutcome`/`DENIAL_STATUS`/`reveal_secret` (Task 3) are consumed unchanged by the router (Task 4) and the round-trip test (Task 5); `FindingView.revealable` (Task 2) is emitted by the router (Task 2) and read by Tasks 3-test/5; `store.Occurrence` and `store.record_finding` signatures match their existing definitions ([store.py](../../../src/recon/findings/store.py)); `finding.type == FindingType.SECRET.value` matches how `store` persists the type (`StrEnum` → `"secret"`).
- **One deliberate spec choice:** `revealable` is a **per-finding** flag (a secret is revealable if the run has a blob and any occurrence has offsets), not per-occurrence — simplest client contract; the reveal service picks the concrete occurrence deterministically (Task 3 `_reveal_occurrence`).
