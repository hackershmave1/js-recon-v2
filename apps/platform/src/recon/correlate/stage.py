"""Correlate stage — REQ-C3 runtime host resolution.

Runs in the (previously no-op) CORRELATING stage, after ANALYZE has written findings and
before finalize's spec reclassify picks up what it commits. It reads the request URLs the
capture stage observed (``discover.assets`` ``requests_ref`` blob), matches them to the
host-less endpoint findings (``recon.correlate.match``), and attaches each confident match
as a **capture-provenanced occurrence** carrying the real URL — so reconstruct/spec surface
the ground-truth URL and the existing host-gate treats the op as observed-absolute.

No-op posture: a non-capture run has no ``requests_ref`` on its ``discover.assets`` event, so
this returns immediately and writes nothing (the static path is untouched). Writing an
occurrence never churns ``finding_hash`` (host/raw_url are off the hashed identity) and never
creates a new finding; a redelivery is a free no-op (occurrence dedup + the host-less filter).
"""

from __future__ import annotations

import json
from urllib.parse import urlsplit

from redis import Redis

from recon import storage
from recon.correlate import match
from recon.db.base import tenant_session
from recon.discover import queries as discover_queries
from recon.events.log import publish, record_event
from recon.findings import normalize, store
from recon.findings import queries as findings_queries
from recon.observability import get_logger
from recon.runs import queries as run_queries

log = get_logger("recon.correlate")


def _method_path(value: str) -> tuple[str, str] | None:
    """Split an endpoint finding's operation into method and path; ``None`` for a malformed
    value or a non-rooted path. Uses ``operation_of_endpoint_value`` to drop the optional
    ``?query`` suffix a stored endpoint value carries — the matcher aligns on path segments
    and the observed URL's query is already stripped, so a query-bearing endpoint
    (``GET /search?q``) must still correlate to an observed ``/search``."""
    method, sep, path = normalize.operation_of_endpoint_value(value).partition(" ")
    if not sep or not path.startswith("/"):
        return None
    return method, path


def correlate_run(redis: Redis, *, tenant_id: str, run_id: str, job_id: str) -> None:
    """Resolve endpoint hosts from the capture stage's observed request URLs. Called from
    the worker for ``RunStage.CORRELATING``; a clean no-op for a non-capture run."""
    payload = discover_queries.latest_assets_event(tenant_id, run_id)
    requests_ref = payload.get("requests_ref") if payload else None
    if not requests_ref:
        return  # non-capture run (no observed requests) — nothing to correlate

    observed = json.loads(storage.get_blob(requests_ref))
    # Fast, bounded stage, but still observe cooperative cancel at entry (REQ-A4); an
    # interrupt rolls back the un-committed session below, so a re-run redoes cleanly.
    run_queries.raise_if_control_requested(tenant_id, run_id)

    view = findings_queries.list_findings(tenant_id, run_id)
    resolved: dict[str, str] = {}
    by_hash: dict[str, findings_queries.FindingView] = {}
    if observed and view is not None:
        by_hash = {f.finding_hash: f for f in view.findings}
        endpoints = _resolvable_endpoints(view.findings)
        resolved = match.correlate(endpoints, observed)

    written = 0
    with tenant_session(tenant_id) as session:
        for finding_hash, resolved_url in resolved.items():
            finding = by_hash[finding_hash]
            result = store.record_finding(
                session,
                tenant_id=tenant_id,
                run_id=run_id,
                finding_type="endpoint",
                value=finding.value,  # the MATCHED finding's own value/path — no new finding
                path=finding.path,
                occurrence=store.Occurrence(
                    host=urlsplit(resolved_url).hostname,
                    raw_url=resolved_url,
                    engine="capture",  # distinct provenance — ground-truth runtime evidence
                ),
                first_stage="correlating",
            )
            if result.occurrence_created:
                written += 1
        # Observability (CLAUDE.md §5): counts only, never a URL or value.
        event = record_event(
            session,
            tenant_id=tenant_id,
            run_id=run_id,
            event_type="correlate.resolved",
            payload={"observed": len(observed), "resolved": len(resolved), "written": written},
        )
    publish(redis, event)
    log.info(
        "correlate.done",
        run_id=run_id,
        observed=len(observed),
        resolved=len(resolved),
        written=written,
    )


def _resolvable_endpoints(findings: list[findings_queries.FindingView]) -> list[match.Endpoint]:
    """The endpoint findings worth correlating: host-less (an op already observed with a
    host is absolute — resolved — so it is left alone, which also makes a re-run a no-op)."""
    endpoints: list[match.Endpoint] = []
    for finding in findings:
        if finding.type != "endpoint" or any(o.host for o in finding.occurrences):
            continue
        parsed = _method_path(finding.value)
        if parsed is None:
            continue
        endpoints.append(
            match.Endpoint(finding_hash=finding.finding_hash, method=parsed[0], path=parsed[1])
        )
    return endpoints
