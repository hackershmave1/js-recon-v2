"""Integration tests for the source viewer's recovered-original support (#2B).

Exercise ``recon.probe.sources`` against a live Postgres/Redis/MinIO stack:
source-map-recovered originals are listed from the findings' persisted
``occurrence.source_path`` (no recovery at list time), served on demand, and a
bad map at view time yields "not found" (None), never an exception. ``recover_sources``
is faked so no Go binary is needed — analyze persists the recovered occurrence,
then the viewer's on-demand recovery is stubbed per test.
"""

from __future__ import annotations

import pytest
from sqlalchemy import update

from recon import storage
from recon.db import models
from recon.db.base import tenant_session
from recon.findings import analyze, deobfuscate, engines, sourcemapper
from recon.probe import sources
from recon.runs import service

pytestmark = pytest.mark.integration


def _seed_run_with_recovered_source(redis, tenant, session_id, monkeypatch):
    """A legacy run whose (faked) source map recovers one original,
    ``app/src/api.js`` — so a real recovered-source finding is persisted."""

    def fake_recover(map_bytes, **_kwargs):
        return sourcemapper.RecoveredSources(
            files=[sourcemapper.RecoveredFile("app/src/api.js", b'fetch("/api/widgets/7");')],
            status="ok",
            origin="uploaded",
        )

    monkeypatch.setattr(sourcemapper, "recover_sources", fake_recover)
    view = service.create_run(redis, tenant_id=tenant, session_id=session_id)
    input_key = storage.put_blob(tenant, view.id, "input", b'fetch("/bundle/only");')
    map_key = storage.put_blob(tenant, view.id, "source_map", b'{"version":3}')
    with tenant_session(tenant) as session:
        session.execute(
            update(models.Run)
            .where(models.Run.id == view.id)
            .values(input_ref=input_key, source_map_ref=map_key)
        )
    analyze.analyze_run(redis, tenant_id=tenant, run_id=view.id)
    return view.id


def test_recovered_source_listed_from_occurrences_and_served(
    redis, authorized_session, monkeypatch
):
    tenant, session_id = authorized_session
    run_id = _seed_run_with_recovered_source(redis, tenant, session_id, monkeypatch)

    files = sources.list_sources(tenant, run_id)
    by_path = {f.path: f for f in files}
    # The recovered original is browsable (derived from the finding's source_path,
    # not by re-running recovery); the raw bundle is still listed as the upload.
    assert by_path["app/src/api.js"].kind == "source"
    assert by_path["app/src/api.js"].asset_url is None  # legacy run-level map
    assert by_path["input.js"].kind == "upload"

    # Its bytes are recovered on demand.
    content = sources.get_source_content(tenant, run_id, "app/src/api.js")
    assert content is not None
    assert 'fetch("/api/widgets/7")' in content.content


def test_recovered_content_with_bad_map_is_not_found_not_500(
    redis, authorized_session, monkeypatch
):
    tenant, session_id = authorized_session
    run_id = _seed_run_with_recovered_source(redis, tenant, session_id, monkeypatch)

    # Now the map goes bad at VIEW time (e.g. store corruption / tool drift). The
    # viewer must return None (-> 404), never let EngineError escape and 500 the tab.
    def boom(map_bytes, **_kwargs):
        raise engines.EngineError("unparseable source map")

    monkeypatch.setattr(sourcemapper, "recover_sources", boom)
    assert sources.get_source_content(tenant, run_id, "app/src/api.js") is None


def test_unknown_recovered_path_is_none(redis, authorized_session, monkeypatch):
    tenant, session_id = authorized_session
    run_id = _seed_run_with_recovered_source(redis, tenant, session_id, monkeypatch)
    # A path the map doesn't recover is simply not found.
    assert sources.get_source_content(tenant, run_id, "app/src/nope.js") is None


def _seed_no_map_bundle(redis, tenant, session_id, minified: bytes) -> str:
    """A legacy run with a minified bundle and NO source map."""
    view = service.create_run(redis, tenant_id=tenant, session_id=session_id)
    input_key = storage.put_blob(tenant, view.id, "input", minified)
    with tenant_session(tenant) as session:
        session.execute(
            update(models.Run).where(models.Run.id == view.id).values(input_ref=input_key)
        )
    return view.id


def test_no_map_bundle_served_beautified_multiline(redis, authorized_session):
    # A raw no-map bundle is beautified ON DEMAND so it renders multi-line with the
    # finding marks aligned to analyze's beautified endpoint lines.
    tenant, session_id = authorized_session
    minified = b'const a=fetch("/api/a");const b=fetch("/api/b");const c=fetch("/api/c");'
    assert len(minified.splitlines()) == 1
    run_id = _seed_no_map_bundle(redis, tenant, session_id, minified)

    content = sources.get_source_content(tenant, run_id, "input.js")
    assert content is not None
    assert len(content.content.splitlines()) > 1  # beautified, not the raw one-liner
    assert 'fetch("/api/a")' in content.content


def test_recovered_source_served_verbatim_not_beautified(redis, authorized_session, monkeypatch):
    # Recovered originals (kind="source") come straight from the source map via
    # _recovered_content -> _as_content and must NEVER be beautified — only the raw
    # no-map bundle is. A MINIFIED recovered source makes "verbatim" (one line)
    # visibly distinct from "beautified" (multi-line).
    tenant, session_id = authorized_session
    minified_original = b'const a=fetch("/api/a");const b=fetch("/api/b");'

    def fake_recover(map_bytes, **_kwargs):
        return sourcemapper.RecoveredSources(
            files=[sourcemapper.RecoveredFile("app/src/api.js", minified_original)],
            status="ok",
            origin="uploaded",
        )

    monkeypatch.setattr(sourcemapper, "recover_sources", fake_recover)
    view = service.create_run(redis, tenant_id=tenant, session_id=session_id)
    input_key = storage.put_blob(tenant, view.id, "input", b'fetch("/bundle/only");')
    map_key = storage.put_blob(tenant, view.id, "source_map", b'{"version":3}')
    with tenant_session(tenant) as session:
        session.execute(
            update(models.Run)
            .where(models.Run.id == view.id)
            .values(input_ref=input_key, source_map_ref=map_key)
        )
    analyze.analyze_run(redis, tenant_id=tenant, run_id=view.id)

    content = sources.get_source_content(tenant, view.id, "app/src/api.js")
    assert content is not None
    assert content.content == minified_original.decode("utf-8")  # verbatim
    assert len(content.content.splitlines()) == 1  # NOT beautified


def test_no_map_bundle_soft_off_serves_raw(redis, authorized_session, monkeypatch):
    # When beautify soft-fails (over cap / pathological), Sources serves the raw
    # bundle unchanged — the same fail-soft contract analyze uses.
    tenant, session_id = authorized_session
    minified = b'const a=fetch("/api/a");const b=fetch("/api/b");'
    run_id = _seed_no_map_bundle(redis, tenant, session_id, minified)

    monkeypatch.setattr(deobfuscate, "beautify", lambda _source: None)

    content = sources.get_source_content(tenant, run_id, "input.js")
    assert content is not None
    assert content.content == minified.decode("utf-8")  # raw, unchanged
    assert len(content.content.splitlines()) == 1
