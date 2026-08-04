"""Findings read endpoint: ``GET /runs/{run_id}/findings`` (REQ-D3, REQ-C2).

A thin read over the findings a run's analyze stage produced. Isolation is the
database's (RLS in the read-model query): a run absent for this tenant is a 404,
deliberately distinct from a run with zero findings (200 + empty list).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from recon.api.deps import get_tenant_id
from recon.findings import queries

router = APIRouter(tags=["findings"])


@router.get("/runs/{run_id}/findings")
def get_run_findings(
    run_id: str,
    tenant_id: str = Depends(get_tenant_id),
) -> dict:
    result = queries.list_findings(tenant_id, run_id)
    if result is None:
        raise HTTPException(status_code=404, detail="run not found")
    return {
        "run_id": result.run_id,
        "count": len(result.findings),
        # REQ-C2: coverage is reported honestly alongside the findings it qualifies;
        # null until the analyze stage has run. Completeness is NOT guaranteed.
        "coverage": _coverage_dict(result.coverage),
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
                    }
                    for occurrence in finding.occurrences
                ],
            }
            for finding in result.findings
        ],
    }


def _coverage_dict(coverage: queries.CoverageView | None) -> dict | None:
    if coverage is None:
        return None
    return {
        "attributed": coverage.attributed,
        "unattributed": coverage.unattributed,
        "secrets": coverage.secrets,
        "secrets_engine": coverage.secrets_engine,
        "sources_recovered": coverage.sources_recovered,
        "source_map": coverage.source_map,
        "files": [
            {"path": f.path, "attributed": f.attributed, "unattributed": f.unattributed}
            for f in coverage.files
        ],
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
