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
        "findings": [
            {
                "finding_hash": finding.finding_hash,
                "type": finding.type,
                "value": finding.value,
                "path": finding.path,
                "severity": finding.severity,
                "attributes": finding.attributes,
                "first_stage": finding.first_stage,
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
