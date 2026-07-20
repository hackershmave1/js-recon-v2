"""Analyze stage — the in-process half of "one JS file -> findings".

Reads the run's JS input blob, extracts its network calls (Vespasian), normalizes
each into its REQ-D3 identity, and writes them through the transactional outbox
(REQ-A3). Emits a coverage event with attributed-vs-unattributed counts so
coverage is reported honestly (REQ-C2). Idempotent: a stage retry re-emits the
same hashes and the outbox upserts are no-ops.
"""

from __future__ import annotations

from dataclasses import dataclass

from redis import Redis

from recon import storage
from recon.db.base import tenant_session
from recon.db.models import Run
from recon.domain import FindingType
from recon.events.log import publish, record_event
from recon.findings import normalize, store
from recon.findings.extract import RawEndpoint, extract
from recon.observability import get_logger

log = get_logger("recon.findings.analyze")

# Single-file MVP: the input is one JS blob with no source map, so every finding
# shares one logical source path. Real per-source paths arrive with Sourcemapper.
_SOURCE_NAME = "input.js"


@dataclass(frozen=True)
class Coverage:
    attributed: int
    unattributed: int
    findings_written: int


def analyze_run(redis: Redis, *, tenant_id: str, run_id: str) -> Coverage:
    """Analyze the run's input JS and persist its findings. No input -> no-op."""
    with tenant_session(tenant_id) as session:
        run = session.get(Run, run_id)
        input_ref = run.input_ref if run is not None else None
    if not input_ref:
        return Coverage(0, 0, 0)

    source = storage.get_blob(input_ref).decode("utf-8", "replace")
    extraction = extract(source)
    path = normalize.normalize_source_path(_SOURCE_NAME)

    written = 0
    with tenant_session(tenant_id) as session:  # one REQ-A3 staging transaction
        for endpoint in extraction.endpoints:
            written += _record_endpoint(session, tenant_id, run_id, path, endpoint)
        coverage_event = record_event(
            session,
            tenant_id=tenant_id,
            run_id=run_id,
            event_type="analyze.coverage",
            payload={
                "attributed": len(extraction.endpoints),
                "unattributed": extraction.unattributed,
            },
        )
    publish(redis, coverage_event)
    log.info(
        "analyze.done",
        run_id=run_id,
        attributed=len(extraction.endpoints),
        unattributed=extraction.unattributed,
        findings=written,
    )
    return Coverage(len(extraction.endpoints), extraction.unattributed, written)


def _record_endpoint(session, tenant_id: str, run_id: str, path: str, ep: RawEndpoint) -> int:
    normalized = normalize.normalize_endpoint(ep.method, ep.url)
    written = _write(
        session, tenant_id, run_id, FindingType.ENDPOINT, normalized.value, path,
        occurrence=store.Occurrence(
            host=normalized.host, raw_url=ep.url, source_path=_SOURCE_NAME,
            line=ep.line, col=ep.col, offset_start=ep.start_byte, offset_end=ep.end_byte,
            evidence=ep.snippet, engine="vespasian",
        ),
        attributes={"kind": ep.kind, "method": ep.method},
    )
    operation = normalize.endpoint_operation(ep.method, ep.url)
    for param in ep.params:
        value = normalize.normalize_param_value(operation, param.location, param.name)
        written += _write(
            session, tenant_id, run_id, FindingType.PARAM, value, path,
            occurrence=store.Occurrence(
                host=normalized.host, raw_url=ep.url, source_path=_SOURCE_NAME,
                line=ep.line, col=ep.col, offset_start=ep.start_byte, offset_end=ep.end_byte,
                engine="vespasian",
            ),
            attributes={"location": param.location, "name": param.name},
        )
    return written


def _write(session, tenant_id, run_id, finding_type, value, path, *, occurrence, attributes) -> int:
    result = store.record_finding(
        session, tenant_id=tenant_id, run_id=run_id, finding_type=finding_type,
        value=value, path=path, occurrence=occurrence, attributes=attributes,
        first_stage="analyzing",
    )
    return int(result.finding_created) + int(result.occurrence_created)
