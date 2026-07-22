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
        "findings": [
            {
                "finding_hash": finding.finding_hash,
                "type": finding.type,
                "value": finding.value,
                "path": finding.path,
                "severity": finding.severity,
                "attributes": finding.attributes,
                "first_stage": finding.first_stage,
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
