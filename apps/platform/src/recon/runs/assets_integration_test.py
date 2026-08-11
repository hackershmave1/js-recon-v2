"""Integration: the runtime-capture seeding INSERT persists ``source_map_ref`` (Postgres).

Requires the full compose stack (Postgres, Redis, MinIO). The static crawl and the
extension ingest both link a source map with a later ``set_source_map_ref`` UPDATE; the
capture stage links it at INSERT time via ``seed_captured`` (a new write path), so this
proves that INSERT round-trips — and that a MIXED batch of mapped + unmapped rows persists
without the bulk insert choking on heterogeneous columns.
"""

from __future__ import annotations

import pytest

from recon import storage
from recon.db.base import tenant_session
from recon.domain import AssetStatus
from recon.runs import assets as run_assets
from recon.runs import service

pytestmark = pytest.mark.integration


def test_seed_captured_persists_source_map_ref(redis, authorized_session):
    tenant, session_id = authorized_session
    view = service.create_run(redis, tenant_id=tenant, session_id=session_id)

    input_a = storage.put_blob(tenant, view.id, "input", b'fetch("/a");')
    input_b = storage.put_blob(tenant, view.id, "input", b'fetch("/b");')
    map_a = storage.put_blob(tenant, view.id, "source_map", b'{"version":3}')

    # A MIXED batch: one row carries a recovered map, one does not. Both must persist —
    # seed_captured builds uniform value dicts, so the bulk insert doesn't choke.
    rows = [
        {"url": "https://acme.io/a.js", "input_ref": input_a, "source_map_ref": map_a},
        {"url": "https://acme.io/b.js", "input_ref": input_b, "source_map_ref": None},
    ]
    with tenant_session(tenant) as session:
        run_assets.seed_captured(session, tenant_id=tenant, run_id=view.id, rows=rows)

    by_url = {r.url: r for r in run_assets.list_for_run(tenant, view.id)}
    assert by_url["https://acme.io/a.js"].source_map_ref == map_a  # linked at INSERT time
    assert by_url["https://acme.io/b.js"].source_map_ref is None  # unmapped row unaffected
    assert by_url["https://acme.io/a.js"].fetch_status == AssetStatus.OK.value
