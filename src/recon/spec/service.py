"""Attach + classify service (design §6.3) — the write path that turns an
uploaded spec into `finding_spec_status` verdicts.

Two entry points share one internal classification step:

- `attach_and_classify` — the (a) path: `POST /runs/{run_id}/spec` stores the
  spec blob, upserts the session's `session_spec`, and classifies.
- `reclassify_run` — the (b) path: the analyze-finalize auto-reclassify hook
  (Task 11, REQ-D5 gate N3) re-runs classification from the session's
  ALREADY-attached spec, so new findings from a continuous rescan stay
  classified without a manual re-POST.

Both delegate to `_classify_session`, which selects the SESSION's distinct
endpoint findings — not just one run's — and upserts `finding_spec_status`
keyed `(session_id, finding_hash)`, mirroring `probe/triage.py:59-74`'s
upsert exactly (the same "verdict outlives any one run" shape as
`finding_triage`, §6.2). This makes both entry points idempotent: re-posting
a spec, or re-finalizing a run, re-tags the session's findings rather than
accumulating stale rows.

The `SpecSummary` each entry point RETURNS, however, is scoped to the calling
run's own endpoint findings, not the whole session (§5.4/§6.3: storage is
session-scoped, but the summary + self-audit ratio surfaced to a caller are
run-scoped) — `_run_scoped_summary` narrows `_classify_session`'s full
session-wide verdict map down to that run's hashes after the upsert.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from recon import storage
from recon.db import models
from recon.db.base import tenant_session
from recon.domain import FindingType
from recon.events.log import record_event
from recon.findings import base_url, normalize, queries
from recon.spec.classify import Classification, SpecSummary, classify_operation, summarize
from recon.spec.ingest import DocumentedOp, IngestedSpec, ingest_spec


def attach_and_classify(tenant_id: str, run_id: str, raw_spec: bytes) -> SpecSummary | None:
    """Attach `raw_spec` to `run_id`'s session and classify its endpoint
    findings against it.

    `None` if `run_id` is invisible to `tenant_id` (RLS) or does not exist —
    the router (Task 9) maps that to 404. A malformed/oversized/hardening-
    rejected spec's `SpecError` (from `ingest_spec`) PROPAGATES uncaught —
    the router maps it to 422; a spec is untrusted input, same footing as
    target JS (gate B4), so it must never be silently swallowed here.

    Note the blob is written to object storage BEFORE `ingest_spec` validates
    it: `put_blob` is content-addressed and idempotent, so an invalid upload
    leaves at most a harmless orphaned blob — no DB row is ever written for
    it, since the `session_spec` upsert only runs once validation succeeds.
    """
    with tenant_session(tenant_id) as session:
        run = session.get(models.Run, run_id)
        if run is None:
            return None
        session_id = str(run.session_id)

        spec_ref = storage.put_blob(tenant_id, run_id, "spec", raw_spec)
        ingested = ingest_spec(raw_spec)

        _upsert_session_spec(session, tenant_id, session_id, spec_ref, ingested)
        verdicts = _classify_session(session, session_id, spec_ref, ingested.documented)
        run_summary = _run_scoped_summary(session, run_id, verdicts)

        record_event(
            session,
            tenant_id=str(tenant_id),
            run_id=str(run_id),
            event_type="spec.classified",
            payload=_bucket_counts(run_summary),
        )
        return run_summary


def reclassify_run(tenant_id: str, run_id: str) -> SpecSummary | None:
    """Re-run classification for `run_id`'s session from its already-attached
    spec (the analyze-finalize auto-reclassify hook, Task 11 — REQ-D5 gate N3).

    A no-op (`None`) if `run_id` is invisible to `tenant_id` (RLS) or its
    session has no `session_spec` row at all — there is nothing stored to
    reclassify against, distinct from a spec with zero documented ops (which
    still runs and returns a real, all-shadow/unresolved summary).
    """
    with tenant_session(tenant_id) as session:
        run = session.get(models.Run, run_id)
        if run is None:
            return None
        session_id = str(run.session_id)

        session_spec = session.scalar(
            select(models.SessionSpec).where(models.SessionSpec.session_id == session_id)
        )
        if session_spec is None:
            return None

        ingested = ingest_spec(storage.get_blob(session_spec.spec_ref))
        verdicts = _classify_session(
            session, session_id, session_spec.spec_ref, ingested.documented
        )
        run_summary = _run_scoped_summary(session, run_id, verdicts)

        record_event(
            session,
            tenant_id=str(tenant_id),
            run_id=str(run_id),
            event_type="spec.classified",
            payload=_bucket_counts(run_summary),
        )
        return run_summary


def _upsert_session_spec(
    session: Session,
    tenant_id: str,
    session_id: str,
    spec_ref: str,
    ingested: IngestedSpec,
) -> None:
    """Upsert the session's one active spec pointer (unique on `session_id`,
    §6.1) — the `probe/triage.py:59-74` on_conflict pattern. Every attach
    (first or re-attach) OVERWRITES format/server_bases/operation_count/
    spec_ref unconditionally: unlike triage's note/actor, there is no
    "preserve the old value when this field is omitted" case here — a
    re-attach always means "this IS the session's new spec now"."""
    insert_stmt = pg_insert(models.SessionSpec).values(
        tenant_id=str(tenant_id),
        session_id=session_id,
        spec_ref=spec_ref,
        spec_format=ingested.format,
        server_bases=list(ingested.server_bases),
        operation_count=len(ingested.documented),
        actor=None,
    )
    upsert = insert_stmt.on_conflict_do_update(
        index_elements=["session_id"],
        set_={
            "spec_ref": spec_ref,
            "spec_format": ingested.format,
            "server_bases": list(ingested.server_bases),
            "operation_count": len(ingested.documented),
            "actor": insert_stmt.excluded.actor,
            "updated_at": func.now(),
        },
    )
    session.execute(upsert)


def _classify_session(
    session: Session,
    session_id: str,
    spec_ref: str,
    documented: Sequence[DocumentedOp],
) -> dict[str, Classification]:
    """Classify every distinct endpoint finding in the session against
    `documented`, upsert `finding_spec_status`, and return the per-hash
    verdicts so a caller can narrow them to one run's own summary.

    Selects DISTINCT `(finding_hash, value)` pairs joined across every run in
    the session, rather than one run's raw `Finding` rows: REQ-D5 continuous
    rescans re-emit the SAME `finding_hash` from each subsequent run once a
    finding recurs, and `finding_hash` already encodes `value`
    (`normalize.finding_hash` hashes type+value+path together), so two rows
    sharing a hash always share the same value — reclassifying each
    occurrence separately would be redundant, idempotent busywork, not a
    correctness bug, but the DISTINCT keeps the work proportional to the
    session's distinct endpoint set, not its run count.

    `Finding.tenant_id` is selected alongside rather than threaded in as a
    parameter: every row visible here already carries the one tenant RLS is
    scoping this transaction to (`tenant_session`'s GUC), so reading it off
    each row needs no extra argument and keeps this function's signature
    exactly the shape the design calls for.
    """
    rows = session.execute(
        select(models.Finding.tenant_id, models.Finding.finding_hash, models.Finding.value)
        .distinct()
        .join(models.Run, models.Run.id == models.Finding.run_id)
        .where(
            models.Run.session_id == session_id,
            models.Finding.type == FindingType.ENDPOINT.value,
        )
    ).all()

    # REQ-C2 gate B1: a stored Finding.value is always host-less (the host lives
    # only on its occurrences), so without this set an originally-ABSOLUTE op
    # would get re-based by a broad prefix rule and flipped to a false shadow.
    # host_bearing_hashes restricts the overlay below to genuinely relative
    # endpoints, mirroring reconstruct.py's `bool(request.hosts)` candidate gate.
    rules = queries.base_url_rules_in_session(session, session_id)
    host_bearing_hashes = {
        finding_hash
        for (finding_hash,) in session.execute(
            select(models.Finding.finding_hash)
            .distinct()
            .join(models.FindingOccurrence, models.FindingOccurrence.finding_id == models.Finding.id)
            .join(models.Run, models.Run.id == models.Finding.run_id)
            .where(
                models.Run.session_id == session_id,
                models.Finding.type == FindingType.ENDPOINT.value,
                models.FindingOccurrence.host.isnot(None),
            )
        ).all()
    }

    verdicts: dict[str, Classification] = {}
    for row_tenant_id, finding_hash, value in rows:
        operation = normalize.operation_of_endpoint_value(value)
        method, _sep, path = operation.partition(" ")
        resolved = base_url.resolve_operation(
            method, path or "/", (finding_hash,), finding_hash in host_bearing_hashes, rules
        )
        classification = classify_operation(f"{method} {resolved.path}", documented)
        verdicts[finding_hash] = classification

        insert_stmt = pg_insert(models.FindingSpecStatus).values(
            tenant_id=str(row_tenant_id),
            session_id=session_id,
            finding_hash=finding_hash,
            status=classification.status,
            reason=classification.reason,
            matched_operation=classification.matched_operation,
            spec_ref=spec_ref,
        )
        upsert = insert_stmt.on_conflict_do_update(
            index_elements=["session_id", "finding_hash"],
            set_={
                "status": classification.status,
                "reason": classification.reason,
                "matched_operation": classification.matched_operation,
                "spec_ref": spec_ref,
                "updated_at": func.now(),
            },
        )
        session.execute(upsert)

    return verdicts


def _run_scoped_summary(
    session: Session, run_id: str, verdicts: dict[str, Classification]
) -> SpecSummary:
    """Narrow `_classify_session`'s session-wide verdict map down to just
    `run_id`'s own endpoint findings, then bucket-count (§5.4/§6.3: storage is
    session-scoped, but the summary + self-audit ratio a caller sees are
    run-scoped)."""
    run_hashes = session.scalars(
        select(models.Finding.finding_hash).where(
            models.Finding.run_id == str(run_id),
            models.Finding.type == FindingType.ENDPOINT.value,
        )
    ).all()
    return summarize(verdicts[h] for h in run_hashes if h in verdicts)


def _bucket_counts(summary: SpecSummary) -> dict[str, int | float]:
    """Value-free per-bucket counts for the durable `spec.classified` event
    (REQ-S3) — counts and the self-audit ratio only, never a raw endpoint
    value or path."""
    return {
        "documented": summary.documented,
        "shadow": summary.shadow,
        "unresolved": summary.unresolved,
        "suffix_verify": summary.suffix_verify,
        "base_url_incompleteness_ratio": summary.base_url_incompleteness_ratio,
    }
