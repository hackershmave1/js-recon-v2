"""runs/captured_versions (extension ingest) — and seed_captured, which it builds on — through
their interface, against real Postgres + MinIO."""

import pytest

from recon import storage
from recon.db.base import tenant_session
from recon.runs import assets, captured_versions
from recon.runs import service as runs_service
from recon.runs.captured_versions import CapturedAsset, CapturedVersionsResult, version_url

pytestmark = pytest.mark.integration

URL = "https://acme.io/x.js"


@pytest.fixture()
def run(authorized_session, redis):
    tenant_id, session_id = authorized_session
    view = runs_service.create_run(redis, tenant_id=tenant_id, session_id=session_id)
    return tenant_id, view.id


def _file(run, content: bytes, url: str = URL, **kw) -> CapturedAsset:
    tenant_id, run_id = run
    return CapturedAsset(
        url=url, input_ref=storage.put_blob(tenant_id, run_id, "input", content), **kw
    )


def _add(run, files, max_versions=10) -> CapturedVersionsResult:
    tenant_id, run_id = run
    with tenant_session(tenant_id) as session:
        return captured_versions.add_captured_versions(
            session, tenant_id=tenant_id, run_id=run_id, files=files, max_versions=max_versions
        )


def _refs(run) -> dict[str, str | None]:
    return {r.url: r.input_ref for r in assets.list_for_run(*run)}


def test_first_copy_owns_the_url_and_later_bytes_become_versions(run):
    a, b = _file(run, b"a();"), _file(run, b"b();")
    result = _add(run, [a, b, a])
    assert _refs(run) == {URL: a.input_ref, version_url(URL, b.input_ref): b.input_ref}
    assert (result.total, result.versions, result.capped) == (
        2,
        [version_url(URL, b.input_ref)],
        [],
    )
    assert {r.fetch_status for r in assets.list_for_run(*run)} == {"ok"}


def test_a_retry_adds_nothing(run):
    a, b = _file(run, b"a();"), _file(run, b"b();")
    _add(run, [a])
    _add(run, [b])
    again = _add(run, [b, a])  # new-then-old in a later batch: both already stored
    assert again.versions == []
    assert len(_refs(run)) == 2


def test_each_version_keeps_its_own_source_map(run):
    tenant_id, run_id = run
    map_a = storage.put_blob(tenant_id, run_id, "source_map", b'{"version":3,"a":1}')
    a = _file(run, b"a();", source_map_ref=map_a)
    b = _file(run, b"b();", source_map_skipped=True)
    _add(run, [a])
    _add(run, [b])
    rows = {r.url: r for r in assets.list_for_run(*run)}
    assert (rows[URL].source_map_ref, rows[URL].source_map_skipped) == (map_a, False)
    version = rows[version_url(URL, b.input_ref)]
    assert (version.source_map_ref, version.source_map_skipped) == (None, True)


def test_versions_are_capped_per_url(run):
    files = [_file(run, f"v{i}();".encode()) for i in range(4)]
    result = _add(run, files, max_versions=2)
    assert result.capped == [files[3]]
    assert len(_refs(run)) == 3  # the first + two versions


def test_the_cap_count_treats_underscores_literally(run):
    # A LIKE-prefix count would read "_" as a wildcard and count a sibling url's versions.
    tenant_id, run_id = run
    sibling = "https://acme.io/xXjs"  # matches "x_js" if "_" were a wildcard
    with tenant_session(tenant_id) as session:
        assets.seed_captured(
            session,
            tenant_id=tenant_id,
            run_id=run_id,
            rows=[{"url": f"{sibling}#sha256={i}", "input_ref": f"k{i}"} for i in range(3)],
        )
    url = "https://acme.io/x_js"
    files = [_file(run, b"a();", url=url), _file(run, b"b();", url=url)]
    assert _add(run, files, max_versions=1).capped == []


def test_browser_capture_keeps_many_same_page_inline_scripts(run):
    # The capture stage suffixes every inline script on a page; seed_captured has no version cap.
    tenant_id, run_id = run
    rows = [{"url": f"https://acme.io/#sha256={i:064x}", "input_ref": f"k{i}"} for i in range(12)]
    with tenant_session(tenant_id) as session:
        inserted = assets.seed_captured(session, tenant_id=tenant_id, run_id=run_id, rows=rows)
    assert len(inserted) == 12
    assert len(_refs(run)) == 12
