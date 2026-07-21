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
from recon.findings import engines, kingfisher, normalize, sourcemapper, store
from recon.findings.extract import RawEndpoint, extract
from recon.findings.kingfisher import RawSecret
from recon.observability import get_logger

log = get_logger("recon.findings.analyze")

# Fallback source path when no source map recovers the real per-file paths — the
# whole bundle is one logical source. Sourcemapper replaces this with real paths.
_SOURCE_NAME = "input.js"


@dataclass(frozen=True)
class Coverage:
    attributed: int
    unattributed: int
    findings_written: int
    secrets: int = 0
    # Honest engine status (REQ-C2/§5): a scanner that was absent must not be
    # reported as "no secrets". Reachable values are "ok" and "unavailable" — a
    # genuine engine error/timeout raises before a Coverage is ever returned.
    secrets_engine: str = "ok"
    # Source-map honesty: how many original files were recovered, and how the map
    # was handled (none | uploaded | inline | unavailable | inline-error). REQ-D5
    # must NOT treat map-scoped endpoint coverage as full-bundle coverage.
    sources_recovered: int = 0
    source_map: str = "none"


def analyze_run(redis: Redis, *, tenant_id: str, run_id: str) -> Coverage:
    """Analyze the run's input JS and persist its findings. No input -> no-op."""
    with tenant_session(tenant_id) as session:
        run = session.get(Run, run_id)
        input_ref = run.input_ref if run is not None else None
        source_map_ref = run.source_map_ref if run is not None else None
    if not input_ref:
        return Coverage(0, 0, 0)

    raw = storage.get_blob(input_ref)
    source = raw.decode("utf-8", "replace")
    # Secret scanning runs out-of-process, BEFORE the staging transaction, so a
    # multi-second subprocess never holds a DB connection open. A missing binary
    # degrades coverage (status recorded on the event); a genuine engine failure
    # raises here and fails/retries the stage rather than under-reporting secrets.
    scan = kingfisher.scan(raw)

    # Prefer recovered original sources (real per-source paths) when a source map
    # is available; otherwise analyze the bundle under the input.js placeholder.
    # We can't union the two — the source path is part of finding identity, so the
    # same endpoint hashes differently per path (that is why coverage records the
    # map status: map-scoped coverage is NOT full-bundle coverage, REQ-D5).
    units, source_map_status, sources_recovered = _analysis_units(source_map_ref, source)

    attributed = 0
    unattributed = 0
    written = 0
    with tenant_session(tenant_id) as session:  # one REQ-A3 staging transaction
        for source_name, unit_text in units:
            extraction = extract(unit_text)
            attributed += len(extraction.endpoints)
            unattributed += extraction.unattributed
            path = normalize.normalize_source_path(source_name)
            for endpoint in extraction.endpoints:
                written += _record_endpoint(session, tenant_id, run_id, path, source_name, endpoint)
        # Secrets are scanned on the original bundle this slice (input.js path).
        # NOTE (follow-up): scanning recovered sources for secrets (real per-source
        # paths for secrets too) is deferred; endpoint/param paths are the D3 win here.
        secret_path = normalize.normalize_source_path(_SOURCE_NAME)
        for secret in scan.secrets:
            written += _record_secret(session, tenant_id, run_id, secret_path, source, secret)
        coverage_event = record_event(
            session,
            tenant_id=tenant_id,
            run_id=run_id,
            event_type="analyze.coverage",
            payload={
                "attributed": attributed,
                "unattributed": unattributed,
                "secrets": len(scan.secrets),
                "secrets_engine": scan.status,
                "sources_recovered": sources_recovered,
                "source_map": source_map_status,
            },
        )
    publish(redis, coverage_event)
    log.info(
        "analyze.done",
        run_id=run_id,
        attributed=attributed,
        unattributed=unattributed,
        secrets=len(scan.secrets),
        secrets_engine=scan.status,
        sources_recovered=sources_recovered,
        source_map=source_map_status,
        findings=written,
    )
    return Coverage(
        attributed, unattributed, written,
        secrets=len(scan.secrets), secrets_engine=scan.status,
        sources_recovered=sources_recovered, source_map=source_map_status,
    )


def _analysis_units(source_map_ref: str | None, source: str) -> tuple[list[tuple[str, str]], str, int]:
    """Decide what to analyze: recovered original sources (real paths) if a source
    map recovers any, else the bundle under ``input.js``. Returns the (name, text)
    units, the source-map status, and the count of recovered files."""
    map_bytes, origin = _resolve_source_map(source_map_ref, source)
    if not map_bytes:
        return [(_SOURCE_NAME, source)], "none", 0

    try:
        recovered = sourcemapper.recover_sources(map_bytes, origin=origin)
    except engines.EngineError:
        # An inline map is opportunistic and rides in the (untrusted) analyzed JS,
        # so a malformed one must NOT fail the run — fall back to bundle analysis.
        # An uploaded map is user-supplied and explicit, so a failure surfaces.
        if origin == "inline":
            return [(_SOURCE_NAME, source)], "inline-error", 0
        raise
    if recovered.status != "ok":  # binary unavailable -> fall back to the bundle
        return [(_SOURCE_NAME, source)], recovered.status, 0
    if not recovered.files:  # map present but nothing recovered (e.g. no sourcesContent)
        return [(_SOURCE_NAME, source)], origin, 0

    units = [(f.path, f.content.decode("utf-8", "replace")) for f in recovered.files]
    return units, origin, len(recovered.files)


def _resolve_source_map(source_map_ref: str | None, source: str) -> tuple[bytes | None, str]:
    if source_map_ref:
        return storage.get_blob(source_map_ref), "uploaded"
    inline = sourcemapper.extract_inline_map(source)
    if inline:
        return inline, "inline"
    return None, "none"


def _record_endpoint(
    session, tenant_id: str, run_id: str, path: str, source_path: str, ep: RawEndpoint
) -> int:
    normalized = normalize.normalize_endpoint(ep.method, ep.url)
    written = _write(
        session, tenant_id, run_id, FindingType.ENDPOINT, normalized.value, path,
        occurrence=store.Occurrence(
            host=normalized.host, raw_url=ep.url, source_path=source_path,
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
                host=normalized.host, raw_url=ep.url, source_path=source_path,
                line=ep.line, col=ep.col, offset_start=ep.start_byte, offset_end=ep.end_byte,
                engine="vespasian",
            ),
            attributes={"location": param.location, "name": param.name},
        )
    return written


def _record_secret(session, tenant_id: str, run_id: str, path: str, source: str, secret: RawSecret) -> int:
    # value = provider:sha256(token) — the raw token is never hashed in cleartext.
    value = normalize.normalize_secret_value(secret.snippet, secret.rule_id)
    offset = kingfisher.byte_offset(source, secret.line, secret.column_start)
    offset_end = offset + len(secret.snippet.encode("utf-8")) if offset is not None else None
    # NOTE (sensitivity): the raw matched secret is stored on the occurrence
    # (evidence) so an authorized tester can validate/revoke it (REQ-D3 §4.2). It
    # is tenant-scoped by RLS; redaction-at-rest + a retention TTL are a later
    # slice (REQ-S4/D6). The finding identity itself carries only the hash.
    return _write(
        session, tenant_id, run_id, FindingType.SECRET, value, path,
        occurrence=store.Occurrence(
            source_path=_SOURCE_NAME, line=secret.line, col=secret.column_start,
            offset_start=offset, offset_end=offset_end,
            evidence=secret.snippet, engine="kingfisher", confidence=secret.confidence,
            verified=True if secret.validation_status == "Active" else None,
        ),
        attributes={"rule": secret.rule_id, "name": secret.rule_name},
    )


def _write(session, tenant_id, run_id, finding_type, value, path, *, occurrence, attributes) -> int:
    result = store.record_finding(
        session, tenant_id=tenant_id, run_id=run_id, finding_type=finding_type,
        value=value, path=path, occurrence=occurrence, attributes=attributes,
        first_stage="analyzing",
    )
    return int(result.finding_created) + int(result.occurrence_created)
