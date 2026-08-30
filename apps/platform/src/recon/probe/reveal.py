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
from recon.findings import engines, normalize
from recon.probe import sources

# Denial code -> HTTP status. The taxonomy lives with the service that produces it;
# the router (recon.api.probe_router) maps the code to a response.
DENIAL_STATUS: dict[str, int] = {
    "no_offsets": 422,  # the secret has no byte location — un-revealable (rare)
    "source_gone": 410,  # the source blob is absent (never set, or purged)
    "integrity": 409,  # the slice no longer hashes to the finding identity
}


@dataclass(frozen=True)
class RevealOutcome:
    revealed: bool
    value: str | None = None
    denial: str | None = None  # one of DENIAL_STATUS when not revealed


# A secret occurrence's source_path == this bundle sentinel means "slice the raw blob";
# anything else is a source-map-recovered original that reveal re-derives (D32-B1). Mirrors
# analyze._SOURCE_NAME / sources._UPLOAD_PATH (kept local to avoid an import cycle).
_BUNDLE_SOURCE_PATH = "input.js"


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
    # D32-B1: the source map for a recovered-source occurrence (its asset's map, or the
    # legacy run-level map). None for a raw-bundle occurrence (source_path == input.js).
    source_map_ref: str | None = None


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

    try:
        outcome = _derive(target)
    except Exception:
        # An unexpected failure reading/slicing the blob (e.g. a transient
        # botocore BotoCoreError) is still a reveal ATTEMPT and must be audited
        # (REQ-S3). Record a value-free denial, then re-raise so the API still
        # surfaces the 500 — we do not mask an infra fault as a normal outcome.
        _audit(
            tenant_id,
            run_id,
            finding_hash,
            target,
            actor,
            reason,
            event_type="secret.reveal_denied",
            denial="error",
        )
        raise

    _audit(
        tenant_id,
        run_id,
        finding_hash,
        target,
        actor,
        reason,
        event_type=("secret.revealed" if outcome.revealed else "secret.reveal_denied"),
        denial=outcome.denial,
    )
    return outcome


def _audit(
    tenant_id: str,
    run_id: str,
    finding_hash: str,
    target: _Target,
    actor: str | None,
    reason: str | None,
    *,
    event_type: str,
    denial: str | None,
) -> None:
    """Commit one durable, value-free audit row for a reveal attempt.

    Its own transaction, independent of anything the caller does next (including
    re-raising), so a denial is recorded even when the attempt then fails/errors."""
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
                "denial": denial,
                "source_path": target.source_path,
                "line": target.line,
                "offset_start": target.offset_start,
                "offset_end": target.offset_end,
            },
        )


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
                # D33-B: the opt-in suspected tier is revealed by the same audited,
                # offset-based machinery as a confirmed secret (same reveal contract).
                models.Finding.type.in_(
                    (FindingType.SECRET.value, FindingType.SECRET_SUSPECTED.value)
                ),
            )
            .options(selectinload(models.Finding.occurrences))
        ).first()
        if finding is None:
            return None
        # Slice Y: `queries.revealable` is True when ANY offset-bearing occurrence's
        # blob resolves, but the deterministic-first occurrence in sort order need
        # not be that one (e.g. it sits on an asset whose fetch is still
        # pending/failed while a sibling occurrence's asset is fetched). Committing
        # to the sort-order-first pick regardless of whether ITS blob exists would
        # falsely deny a finding the read-gate promised was revealable. So: walk the
        # candidates in the SAME deterministic order queries.py sorts by, and reveal
        # from the first one whose blob actually resolves — safe because every
        # occurrence of one finding_hash decodes to the same stripped token (see
        # _reveal_candidates), and the integrity re-check below still fails closed
        # on any wrong-bytes slice.
        candidates = _reveal_candidates(finding.occurrences)
        occurrence, input_ref = _first_resolvable_occurrence(
            session, run_id, run.input_ref, candidates
        )
        # D32-B1: for a recovered-source occurrence, reveal re-derives from the source MAP,
        # not the JS blob — resolve that map ref here (under RLS) so `_derive` holds no DB.
        source_map_ref = (
            _occurrence_map_ref(session, run_id, run.source_map_ref, occurrence)
            if occurrence is not None
            else None
        )
        return _Target(
            input_ref=input_ref,
            rule=str((finding.attributes or {}).get("rule", "")),
            value=finding.value,
            offset_start=None if occurrence is None else occurrence.offset_start,
            offset_end=None if occurrence is None else occurrence.offset_end,
            source_path=None if occurrence is None else occurrence.source_path,
            line=None if occurrence is None else occurrence.line,
            source_map_ref=source_map_ref,
        )


def _reveal_candidates(occurrences):
    """Every offset-bearing occurrence, deterministically sorted (source_path,
    offset_start, occurrence_hash) — the same order ``queries.py`` displays them
    in, for stability.

    All occurrences of one finding_hash decode to the same stripped token, so any
    one of them is a correct reveal source; which one actually HAS a readable
    blob is a separate question handled by ``_first_resolvable_occurrence``."""
    candidates = [o for o in occurrences if o.offset_start is not None and o.offset_end is not None]
    return sorted(
        candidates,
        key=lambda o: (o.source_path or "", o.offset_start or 0, o.occurrence_hash),
    )


def _first_resolvable_occurrence(session, run_id, run_input_ref, candidates):
    """The first candidate (in deterministic order) whose blob resolves, paired
    with that blob's ref — or the deterministic-first candidate with
    ``input_ref=None`` when none resolve (preserves the single-candidate
    behavior: the audit still logs a stable source_path/offset and ``_derive``
    still denies ``source_gone``), or ``(None, None)`` when there are no
    offset-bearing occurrences at all."""
    if not candidates:
        return None, None
    for occurrence in candidates:
        input_ref = _occurrence_blob_ref(session, run_id, run_input_ref, occurrence)
        if input_ref:
            return occurrence, input_ref
    return candidates[0], None


def _occurrence_blob_ref(session, run_id, run_input_ref, occurrence):
    """The blob ref one occurrence would reveal from: its own run_asset (looked
    up scoped to THIS run — defense-in-depth alongside RLS, matching
    ``queries.py``'s per-run asset map), or ``run_input_ref`` for a legacy
    occurrence (``run_asset_id`` NULL)."""
    if occurrence.run_asset_id is None:
        return run_input_ref
    asset = session.scalars(
        select(models.RunAsset).where(
            models.RunAsset.id == occurrence.run_asset_id,
            models.RunAsset.run_id == str(run_id),
        )
    ).first()
    return asset.input_ref if asset is not None else None


def _occurrence_map_ref(session, run_id, run_source_map_ref, occurrence):
    """The SOURCE-MAP blob ref for a recovered-source occurrence (D32-B1) — its own
    run_asset's map (scoped to THIS run, like ``_occurrence_blob_ref``), or the legacy
    run-level ``run.source_map_ref`` (``run_asset_id`` NULL). Mirrors
    ``recon.probe.sources._resolve_source_map_ref`` so reveal derives from the exact
    map the Sources viewer serves from."""
    if occurrence.run_asset_id is None:
        return run_source_map_ref
    asset = session.scalars(
        select(models.RunAsset).where(
            models.RunAsset.id == occurrence.run_asset_id,
            models.RunAsset.run_id == str(run_id),
        )
    ).first()
    return asset.source_map_ref if asset is not None else None


def _derive(target: _Target) -> RevealOutcome:
    if target.offset_start is None or target.offset_end is None:
        return RevealOutcome(revealed=False, denial="no_offsets")
    # D32-B1: a recovered-source occurrence is re-derived from its source map; a bundle
    # occurrence slices the raw blob. Both produce `data` in the SAME utf-8/replace byte
    # space the offsets were located in, then funnel into the ONE integrity-gated return
    # below — so any reproduction drift fails closed (409), never leaks wrong bytes.
    # Recovered iff BOTH: source_path is a real path (not the "input.js" bundle sentinel)
    # AND a source map is actually available. The map guard is load-bearing, not belt-and-
    # suspenders: a bundle secret on a MAPPED asset still has source_path "input.js" (so
    # the sentinel keeps it on the bundle path), and if a recovered secret's map is gone
    # we slice the bundle and fail the integrity check (409) rather than leak — either way
    # fail-closed. It also settles the "map author named a source input.js" collision the
    # same safe way.
    if _is_recovered(target):
        data = _recovered_byte_space(target)
    else:
        data = _bundle_byte_space(target.input_ref)
    if data is None:
        return RevealOutcome(revealed=False, denial="source_gone")
    sliced = data[target.offset_start : target.offset_end].decode("utf-8", "replace")
    if normalize.normalize_secret_value(sliced, target.rule) != target.value:
        return RevealOutcome(revealed=False, denial="integrity")
    return RevealOutcome(revealed=True, value=sliced)


def _is_recovered(target: _Target) -> bool:
    """A source-map-recovered occurrence (D32-B1): a real recovered `source_path` (not the
    ``input.js`` bundle sentinel) AND a source map to re-derive it from. See ``_derive``
    for why BOTH conditions matter (bundle-on-mapped-asset, purged map, name collision)."""
    return (
        target.source_path is not None
        and target.source_path != _BUNDLE_SOURCE_PATH
        and target.source_map_ref is not None
    )


def _bundle_byte_space(input_ref: str | None) -> bytes | None:
    """The raw bundle blob in the byte space analyze located offsets in: analyze decodes
    with utf-8/replace before byte_offset, so a stray non-UTF-8 byte would shift raw-byte
    offsets — re-encoding the replaced string reproduces that exact space. ``None`` when
    the blob is absent (→ source_gone)."""
    if not input_ref:
        return None
    try:
        raw = storage.get_blob(input_ref)
    except ClientError:
        return None
    return raw.decode("utf-8", "replace").encode("utf-8")


def _recovered_byte_space(target: _Target) -> bytes | None:
    """Reproduce a recovered original's byte space by re-deriving it from the source map
    exactly as analyze scanned it and the Sources viewer serves it (the shared
    ``sources.recover_file_text`` = one definition of the recovered bytes). ``None`` (→
    source_gone) if the map is absent/unparseable or no longer recovers this path; the
    integrity re-check in ``_derive`` still guards against any residual drift.

    KNOWN LIMITATION (DEBT D32, inline maps): a secret recovered from an INLINE ``data:``
    source map has no persisted ``source_map_ref`` (the map rode in the bundle and was
    never stored), so ``_is_recovered`` is False and reveal slices the bundle → integrity
    409. This is FAIL-CLOSED (never wrong bytes) and CONSISTENT with the Sources viewer,
    which likewise can't re-derive an inline map (``sources._resolve_source_map_ref``
    returns None). The finding is still surfaced; only the just-in-time plaintext reveal
    is unavailable. Rare in practice — inline maps bloat a bundle so production ships
    external ``.map`` files (this slice's target). Fix would persist the inline map."""
    if not target.source_map_ref or not target.source_path:
        return None
    try:
        # D37-L2 slice 2: recover_file_text streams the map blob to a temp file and reads
        # back only this one recovered file, so the API process never whole-loads the map
        # or the whole recovered tree just to re-derive one secret's byte space.
        text = sources.recover_file_text(target.source_map_ref, target.source_path)
    except engines.EngineError:
        return None  # unparseable map / absent binary — fail closed, not a 500
    return None if text is None else text.encode("utf-8")
