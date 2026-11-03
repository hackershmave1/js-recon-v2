"""Findings read endpoint: ``GET /runs/{run_id}/findings`` (REQ-D3, REQ-C2).

A thin read over the findings a run's analyze stage produced. Isolation is the
database's (RLS in the read-model query): a run absent for this tenant is a 404,
deliberately distinct from a run with zero findings (200 + empty list).
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Query

from recon.api.deps import get_tenant_id
from recon.findings import queries

router = APIRouter(tags=["findings"])


@router.get("/runs/{run_id}/findings")
def get_run_findings(
    run_id: str,
    tenant_id: str = Depends(get_tenant_id),
    # Captured-but-not-shown findings (analytics noise, junk / off-scope page routes — see
    # findings/visibility) are hidden by DEFAULT; the Findings view toggle passes
    # ?include_hidden=true to bring them back. `include_noise` is the older name, kept as an
    # alias. A reversible read overlay — the findings are stored either way, never deleted.
    include_hidden: bool = False,
    include_noise: bool = False,
    # D50: server-side filter + pagination params. All are optional; omitting them
    # preserves the existing behaviour (full unfiltered result, limit=2000).
    types: list[str] = Query(default=[]),
    triage_statuses: list[str] = Query(default=[]),
    q: str | None = None,
    risk_tags: list[str] = Query(default=[]),
    limit: int = Query(default=2000, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
) -> dict:
    result = queries.list_findings(
        tenant_id,
        run_id,
        include_hidden=include_hidden or include_noise,
        types=types,
        triage_statuses=triage_statuses,
        q=q,
        risk_tags=risk_tags,
        limit=limit,
        offset=offset,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="run not found")
    return {
        "run_id": result.run_id,
        "count": len(result.findings),
        # D50: total before pagination so the FE can show "N of M" and decide whether
        # a "load more" fetch would return more results.
        "total": result.total,
        "hidden": result.hidden,
        "offset": offset,
        "limit": limit,
        # REQ-C2: coverage is reported honestly alongside the findings it qualifies;
        # null until the analyze stage has run. Completeness is NOT guaranteed.
        "coverage": result.coverage.to_json() if result.coverage else None,
        # The run's "Files" metric: JS files collected (queries.files_collected) — the same
        # number the Sessions list shows. NOT coverage.files (recovered analysis units).
        "files": result.files,
        # Design §6.4: null until a spec is attached to the run's session at all —
        # distinct from an attached spec whose buckets are all zero.
        "spec": _spec_summary_dict(result.spec_summary),
        "findings": [
            {
                "finding_hash": finding.finding_hash,
                "type": finding.type,
                "value": finding.value,
                "path": finding.path,
                "severity": finding.severity,
                # D49: 0-100 read-time priority (type + risk tags); `severity` is its label.
                "priority": finding.priority,
                "attributes": finding.attributes,
                "first_stage": finding.first_stage,
                "revealable": finding.revealable,
                "triage": (
                    None
                    if finding.triage is None
                    else {
                        "status": finding.triage.status,
                        "note": finding.triage.note,
                        "actor": finding.triage.actor,
                        "updated_at": finding.triage.updated_at,
                    }
                ),
                # None -> the FE renders "unclassified" (never classified: no spec
                # attached to the session, or this finding predates attach/reclassify).
                "spec_status": (
                    None
                    if finding.spec_status is None
                    else {
                        "status": finding.spec_status.status,
                        "reason": finding.spec_status.reason,
                        "matched_operation": finding.spec_status.matched_operation,
                    }
                ),
                # Slice 4: cross-run sightings. null == "ungrouped" (the run's session
                # has no engagement); an object (counts may be 0) == grouped + computed.
                "sightings": (
                    None
                    if finding.sightings is None
                    else {
                        "capture": finding.sightings.capture,
                        "platform": finding.sightings.platform,
                    }
                ),
                "occurrences": [
                    {
                        "host": occurrence.host,
                        "raw_url": occurrence.raw_url,
                        "source_path": occurrence.source_path,
                        "line": occurrence.line,
                        "col": occurrence.col,
                        "offset_start": occurrence.offset_start,
                        "offset_end": occurrence.offset_end,
                        "evidence": occurrence.evidence,
                        "engine": occurrence.engine,
                        "confidence": occurrence.confidence,
                        "verified": occurrence.verified,
                        "asset_url": occurrence.asset_url,
                        # Exact range in the served source; null = no exact location.
                        "loc": asdict(occurrence.loc) if occurrence.loc else None,
                        "approximate_line": occurrence.approximate_line,
                    }
                    for occurrence in finding.occurrences
                ],
            }
            for finding in result.findings
        ],
    }


@router.get("/runs/{run_id}/diff")
def get_run_diff(
    run_id: str,
    base: str,
    tenant_id: str = Depends(get_tenant_id),
) -> dict:
    """REQ-D5: finding-set diff between two runs of the same session.

    ``base`` is the base run id to compare against. Returns 404 when either
    run is absent for this tenant. ``base_incomplete`` in the response signals
    that the base run was PARTIAL/FAILED/CANCELLED — the ``gone`` bucket may
    contain findings the base simply missed, not ones that disappeared from
    the attack surface."""
    result = queries.diff_runs(tenant_id, run_id, base)
    if result is None:
        raise HTTPException(status_code=404, detail="run or base run not found")
    return {
        "run_id": result.run_id,
        "base_run_id": result.base_run_id,
        "base_incomplete": result.base_incomplete,
        "new": [_diff_entry_dict(e) for e in result.new],
        "persisted": [_diff_entry_dict(e) for e in result.persisted],
        "gone": [_diff_entry_dict(e) for e in result.gone],
    }


def _diff_entry_dict(e: queries.DiffEntry) -> dict:
    return {
        "finding_hash": e.finding_hash,
        "type": e.type,
        "value": e.value,
        "path": e.path,
        "severity": e.severity,
        "priority": e.priority,
    }


def _spec_summary_dict(summary: queries.SpecSummary | None) -> dict | None:
    if summary is None:
        return None
    return {
        "documented": summary.documented,
        "shadow": summary.shadow,
        "unresolved": summary.unresolved,
        "suffix_verify": summary.suffix_verify,
        "base_url_incompleteness_ratio": summary.base_url_incompleteness_ratio,
    }
