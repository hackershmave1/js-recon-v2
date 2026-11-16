"""Reference-counted blob delete within one (tenant, run) (ADR-0010 Confirmation).

Blobs are content-addressed (``storage.object_key``: ``{tenant}/{run}/{kind}/{sha256}``), so two
assets in one run with identical bytes share ONE blob, and a blob's derivatives live under the
same digest: ``formatted/`` (the beautified copy) and ``originals/`` (the recovered-originals
list), plus its source map (``source_map/`` under the map's own digest). Deleting one asset must
not remove bytes another asset in the run still points at, so a key is removed only when no other
row in the run references that digest.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from recon import storage
from recon.db import models

# Kinds stored under an INPUT digest alongside the input blob itself.
_INPUT_DERIVATIVES = ("input", "formatted", "originals")


def _digest(key: str | None) -> str | None:
    return key.rsplit("/", 1)[-1] if key else None


def referenced_digests(session: Session, run_id: str) -> dict[str, set[str]]:
    """The digests rows of this run still point at, per blob kind: ``input`` (run assets' inputs,
    which also keep their ``formatted``/``originals`` derivatives), ``source_map`` and
    ``quarantine``. Kind-aware on purpose: identical bytes held as a quarantined script and as a
    run asset are two different objects. Read inside the caller's transaction, AFTER its own
    rows are removed."""
    still: dict[str, set[str]] = {"input": set(), "source_map": set(), "quarantine": set()}
    for input_ref, map_ref in session.execute(
        select(models.RunAsset.input_ref, models.RunAsset.source_map_ref).where(
            models.RunAsset.run_id == run_id
        )
    ):
        if digest := _digest(input_ref):
            still["input"].add(digest)
        if digest := _digest(map_ref):
            still["source_map"].add(digest)
    for (ref,) in session.execute(
        select(models.QuarantinedAsset.content_ref).where(models.QuarantinedAsset.run_id == run_id)
    ):
        if digest := _digest(ref):
            still["quarantine"].add(digest)
    return still


def unreferenced_keys(
    session: Session,
    *,
    tenant_id: str,
    run_id: str,
    input_refs: Iterable[str | None] = (),
    map_refs: Iterable[str | None] = (),
    quarantine_refs: Iterable[str | None] = (),
) -> list[str]:
    """The object keys that are safe to delete now: for each given reference whose digest no
    remaining row of the run uses, the blob and (for an input) its ``formatted`` and ``originals``
    derivatives. Call after deleting the rows, in the same transaction, then pass the result to
    :func:`delete_keys` once it commits."""
    still = referenced_digests(session, run_id)
    keys: list[str] = []
    seen: set[str] = set()

    def add(kind: str, digest: str) -> None:
        key = f"{tenant_id}/{run_id}/{kind}/{digest}"
        if key not in seen:
            seen.add(key)
            keys.append(key)

    for ref in input_refs:
        digest = _digest(ref)
        if digest and digest not in still["input"]:
            for kind in _INPUT_DERIVATIVES:
                add(kind, digest)
    for ref in map_refs:
        digest = _digest(ref)
        if digest and digest not in still["source_map"]:
            add("source_map", digest)
    for ref in quarantine_refs:
        digest = _digest(ref)
        if digest and digest not in still["quarantine"]:
            add("quarantine", digest)
    return keys


def delete_keys(keys: list[str]) -> int:
    return storage.delete_keys(keys)
