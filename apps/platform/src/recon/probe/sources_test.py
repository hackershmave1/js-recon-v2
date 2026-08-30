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
from sqlalchemy import select, update

from recon import storage
from recon.db import models
from recon.db.base import tenant_session
from recon.findings import analyze, deobfuscate, engines, sourcemapper
from recon.probe import sources
from recon.runs import service

pytestmark = pytest.mark.integration


def _fake_both_recoveries(monkeypatch, path: str, content: bytes) -> None:
    """Fake BOTH recovery seams (no Go binary needed): ``recover_sources`` for analyze's
    scan + ``recover_one_file`` for the viewer's on-demand single-file serve — D37-L2
    slice 2 split the whole-tree recovery (analyze) from the one-file read (viewer/reveal).
    Both reproduce ``content`` for ``path``."""
    monkeypatch.setattr(
        sourcemapper,
        "recover_sources",
        lambda _map_bytes, **_k: sourcemapper.RecoveredSources(
            files=[sourcemapper.RecoveredFile(path, content)], status="ok", origin="uploaded"
        ),
    )
    monkeypatch.setattr(
        sourcemapper,
        "recover_one_file",
        lambda _map_path, target, **_k: content if target == path else None,
    )


def _seed_run_with_recovered_source(redis, tenant, session_id, monkeypatch):
    """A legacy run whose (faked) source map recovers one original,
    ``app/src/api.js`` — so a real recovered-source finding is persisted."""
    _fake_both_recoveries(monkeypatch, "app/src/api.js", b'fetch("/api/widgets/7");')
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
    # The viewer re-derives via recover_one_file (slice 2), so the failure is injected there.
    def boom(_map_path, _target, **_kwargs):
        raise engines.EngineError("unparseable source map")

    monkeypatch.setattr(sourcemapper, "recover_one_file", boom)
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


def _seed_recovered_run(redis, tenant, session_id, monkeypatch, recovered: bytes, path: str) -> str:
    """A legacy run whose (faked) source map recovers one original at ``path`` carrying
    ``recovered`` bytes, analyzed — so a real recovered-source finding is persisted."""
    _fake_both_recoveries(monkeypatch, path, recovered)
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


def test_recovered_non_minified_source_served_verbatim(redis, authorized_session, monkeypatch):
    # A genuinely multi-line recovered original (real code, short lines) is served
    # VERBATIM so its own, meaningful line numbers survive — only a MINIFIED recovered
    # source is beautified (next test), mirroring analyze._analysis_units.
    tenant, session_id = authorized_session
    readable = b'import x from "x";\nconst a = fetch("/api/a");\nexport default a;\n'
    run_id = _seed_recovered_run(redis, tenant, session_id, monkeypatch, readable, "app/src/api.js")

    content = sources.get_source_content(tenant, run_id, "app/src/api.js")
    assert content is not None
    assert content.content == readable.decode("utf-8")  # verbatim — real line numbers kept


def test_recovered_minified_source_beautified_and_finding_line_aligns(
    redis, authorized_session, monkeypatch
):
    # A MINIFIED recovered original (a vendor lib shipped minified in the map's
    # sourcesContent) is beautified on serve — the SAME beautify_if_minified analyze ran
    # before recording finding lines — so the finding lands on a distinct line that, in
    # the served text, actually contains the call. This is the jump-to-finding fix (#2).
    tenant, session_id = authorized_session
    minified = b'const a=fetch("/api/aaa");const b=fetch("/api/bbb");' * 12  # one >500-char line
    assert len(minified.splitlines()) == 1 and len(minified) > 500
    run_id = _seed_recovered_run(
        redis, tenant, session_id, monkeypatch, minified, "app/src/vendor.min.js"
    )

    content = sources.get_source_content(tenant, run_id, "app/src/vendor.min.js")
    assert content is not None
    served_lines = content.content.splitlines()
    assert len(served_lines) > 1  # beautified, not the raw one-liner

    with tenant_session(tenant) as session:
        lines = [
            ln
            for ln in session.execute(
                select(models.FindingOccurrence.line)
                .join(models.Finding, models.FindingOccurrence.finding)
                .where(
                    models.Finding.run_id == run_id,
                    models.FindingOccurrence.source_path == "app/src/vendor.min.js",
                )
            ).scalars()
            if ln is not None
        ]
    # Findings now span DISTINCT lines (pre-fix every one collapsed onto line 1 of the
    # one-line source), and every finding line points AT its call in the served
    # (beautified) text — the whole point of beautifying analyze + serve identically.
    assert lines and max(lines) > 1
    assert all("fetch(" in served_lines[ln - 1] for ln in lines)


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
