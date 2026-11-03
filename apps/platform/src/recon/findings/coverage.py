"""The analyze stage's honesty counters (REQ-C2) — one record for writing, merging and reading.

Analyze writes one ``analyze.coverage`` event per analyzed blob (``Coverage.to_payload``). A
run's coverage is read back from those events (``read_run_coverage``) and served to the
workspace (``Coverage.to_json``). ``unattributed`` cannot be recomputed from stored findings (an
un-attributed call yields no finding row), so the event log is the record of what analyze
actually reported.

Honesty rules for a run total (``merge``): never under-report. Counts add; a scanner that was
``"unavailable"`` on ANY part wins over ``"ok"``; ``curtailed`` / ``recovered_partial`` on any
part mark the whole run; a D32 ``"skipped"`` source map dominates, otherwise the newest part's
``source_map`` stands.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from recon.db.models import RunAsset, RunEvent

EVENT_TYPE = "analyze.coverage"


@dataclass(frozen=True, kw_only=True)
class FileCoverage:
    """Per-file counter: how many calls in one source file were attributed to an endpoint vs
    left un-attributed — so a reader can see *which* file has unmapped calls."""

    path: str
    attributed: int
    unattributed: int


@dataclass(frozen=True, kw_only=True)
class Coverage:
    attributed: int = 0
    unattributed: int = 0
    secrets: int = 0
    # D33-B: opt-in low-confidence "suspected secret" sightings, counted separately so the
    # precision-first `secrets` count is never inflated by the recall lane.
    secrets_suspected: int = 0
    # Cleartext info-disclosure sightings (internal-IP literals): a NON-secret family.
    internal_ips: int = 0
    # "ok" | "unavailable": an absent scanner must not read as "no secrets". A genuine engine
    # error/timeout raises before a Coverage is ever built.
    secrets_engine: str = "ok"
    # Source-map handling: none | uploaded | inline | capture | unavailable | inline-error |
    # capture-error | skipped (D32: a referenced map we could not retrieve).
    sources_recovered: int = 0
    source_map: str = "none"
    # D31: the extract hit the AST node budget, so the tail was not examined.
    curtailed: bool = False
    # D37-L2: source-map recovery hit its write budget, so some recovered originals weren't scanned.
    recovered_partial: bool = False
    files: tuple[FileCoverage, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        """The ``analyze.coverage`` event payload."""
        return {
            "attributed": self.attributed,
            "unattributed": self.unattributed,
            "secrets": self.secrets,
            "secrets_suspected": self.secrets_suspected,
            "internal_ips": self.internal_ips,
            "secrets_engine": self.secrets_engine,
            "sources_recovered": self.sources_recovered,
            "source_map": self.source_map,
            "curtailed": self.curtailed,
            "recovered_partial": self.recovered_partial,
            "files": [_file_json(f) for f in self.files],
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> Coverage:
        """Read an event payload; a key missing from an event that predates it takes the
        field's default (so old events stay honest: not curtailed, engine "ok")."""
        return cls(
            attributed=int(payload.get("attributed", 0)),
            unattributed=int(payload.get("unattributed", 0)),
            secrets=int(payload.get("secrets", 0)),
            secrets_suspected=int(payload.get("secrets_suspected", 0)),
            internal_ips=int(payload.get("internal_ips", 0)),
            secrets_engine=str(payload.get("secrets_engine", "ok")),
            sources_recovered=int(payload.get("sources_recovered", 0)),
            source_map=str(payload.get("source_map", "none")),
            curtailed=bool(payload.get("curtailed", False)),
            recovered_partial=bool(payload.get("recovered_partial", False)),
            files=tuple(
                FileCoverage(
                    path=str(entry.get("path", "")),
                    attributed=int(entry.get("attributed", 0)),
                    unattributed=int(entry.get("unattributed", 0)),
                )
                for entry in payload.get("files", [])
            ),
        )

    def to_json(self) -> dict[str, Any]:
        """The findings API's ``coverage`` object. An explicit allow-list: the suspected and
        internal-IP counts are served as finding-type counts elsewhere, not here."""
        return {
            "attributed": self.attributed,
            "unattributed": self.unattributed,
            "secrets": self.secrets,
            "secrets_engine": self.secrets_engine,
            "sources_recovered": self.sources_recovered,
            "source_map": self.source_map,
            "curtailed": self.curtailed,
            "recovered_partial": self.recovered_partial,
            "files": [_file_json(f) for f in self.files],
        }


def merge(parts_oldest_first: Sequence[Coverage]) -> Coverage:
    """One run total from per-blob parts, ordered oldest to newest (see the module docstring)."""
    if not parts_oldest_first:
        return Coverage()
    parts = parts_oldest_first
    source_maps = [p.source_map for p in parts]
    return Coverage(
        attributed=sum(p.attributed for p in parts),
        unattributed=sum(p.unattributed for p in parts),
        secrets=sum(p.secrets for p in parts),
        secrets_suspected=sum(p.secrets_suspected for p in parts),
        internal_ips=sum(p.internal_ips for p in parts),
        secrets_engine=(
            "unavailable" if any(p.secrets_engine == "unavailable" for p in parts) else "ok"
        ),
        sources_recovered=sum(p.sources_recovered for p in parts),
        source_map="skipped" if "skipped" in source_maps else source_maps[-1],
        curtailed=any(p.curtailed for p in parts),
        recovered_partial=any(p.recovered_partial for p in parts),
        # Per-file-per-blob detail: concatenated, not merged by path — two blobs can share a
        # fallback path and still be two distinct entries.
        files=tuple(f for p in parts for f in p.files),
    )


def read_run_coverage(session: Session, run_id: str) -> Coverage | None:
    """The run's coverage, or ``None`` until analyze has emitted for it.

    A multi-asset run's assets each emit their OWN event exactly once (a redelivery skips an
    already analyze-terminal asset), so the run total is the merge of ALL of them. A legacy
    single-asset run has one analyze stage that a retry re-runs IN PLACE, re-emitting its one
    event — summing would double-count, so there the newest event alone is the coverage.
    """
    payloads = session.scalars(
        select(RunEvent.payload)
        .where(RunEvent.run_id == str(run_id), RunEvent.type == EVENT_TYPE)
        .order_by(RunEvent.id)
    ).all()
    if not payloads:
        return None
    has_assets = session.scalar(select(RunAsset.id).where(RunAsset.run_id == str(run_id)).limit(1))
    if has_assets is None:
        return Coverage.from_payload(payloads[-1])
    return merge([Coverage.from_payload(p) for p in payloads])


def _file_json(f: FileCoverage) -> dict[str, Any]:
    return {"path": f.path, "attributed": f.attributed, "unattributed": f.unattributed}
