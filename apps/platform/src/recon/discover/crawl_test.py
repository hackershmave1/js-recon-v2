# src/recon/discover/crawl_test.py
import json
import socket
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from recon.discover import crawl
from recon.discover.harness import CrawlResult
from recon.fetch import egress
from recon.queue import retry

# _load_target is patched to return "acme.io", so the crawl seed URL is this.
_SEED_URL = "https://acme.io"


def _fake_getaddrinfo(ip):
    def resolver(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, port))]

    return resolver


def _patches(katana_urls, validated, engagement, existing=None):
    """Common patch set: session lookup, harness, katana parse, egress, storage, event.

    The single egress.validate_target mock serves BOTH call sites: the crawl-seed
    gate (the seed URL must pass so katana can launch) and the per-output
    re-validation (only ``validated`` URLs pass). The seed is allowed here so the
    seed gate stays green by default; seed-block behavior has its own test.
    """
    allowed = set(validated) | {_SEED_URL}

    def validate(url, scope, **_kwargs):  # **_kwargs absorbs allow_local (REQ-CE3)
        if url not in allowed:
            raise egress.EgressBlocked(f"blocked: {url}")
        return SimpleNamespace(url=url)
    return [
        # Mock the DB seam so these stay pure units (record_event is patched too).
        patch("recon.discover.crawl.tenant_session"),
        patch("recon.discover.crawl.queries.latest_assets_event", return_value=existing),
        patch("recon.discover.crawl._load_target", return_value=("acme.io", "sess-1", None)),
        patch("recon.discover.crawl.sessions_service.get_session", return_value=engagement),
        patch("recon.discover.crawl.harness.run_crawl",
              return_value=CrawlResult(stdout=b"", timed_out=False)),
        patch("recon.discover.crawl.katana.parse_assets", return_value=katana_urls),
        patch("recon.discover.crawl.egress.validate_target", side_effect=validate),
        patch("recon.discover.crawl.storage.put_blob", return_value="t/r/assets/deadbeef"),
    ]


def test_discover_run_writes_only_in_scope_assets():
    engagement = SimpleNamespace(scope_hosts=["acme.io"], authorization_ack=True)
    recorded = {}
    with patch("recon.discover.crawl.record_event",
               side_effect=lambda *a, **k: recorded.update(k) or MagicMock()), \
         patch("recon.discover.crawl.publish"):
        for p in _patches(
            katana_urls=["https://acme.io/app.js", "http://169.254.169.254/x.js"],
            validated={"https://acme.io/app.js"}, engagement=engagement,
        ):
            p.start()
        try:
            crawl.discover_run(MagicMock(), tenant_id="t", run_id="r", job_id="j")
        finally:
            patch.stopall()
    # The internal/out-of-scope URL was dropped by egress re-validation.
    assert recorded["payload"]["count"] == 1
    assert recorded["payload"]["status"] == "ok"


def test_discover_run_is_idempotent_when_event_exists():
    engagement = SimpleNamespace(scope_hosts=["acme.io"], authorization_ack=True)
    with patch("recon.discover.crawl.harness.run_crawl") as run_crawl, \
         patch("recon.discover.crawl.queries.latest_assets_event",
               return_value={"count": 3, "assets_ref": "x", "status": "ok"}):
        crawl.discover_run(MagicMock(), tenant_id="t", run_id="r", job_id="j")
    run_crawl.assert_not_called()  # no re-crawl on redelivery


def test_discover_run_rejects_unauthorized_session():
    engagement = SimpleNamespace(scope_hosts=["acme.io"], authorization_ack=False)
    with patch("recon.discover.crawl.queries.latest_assets_event", return_value=None), \
         patch("recon.discover.crawl._load_target", return_value=("acme.io", "sess-1", None)), \
         patch("recon.discover.crawl.sessions_service.get_session", return_value=engagement):
        with pytest.raises(retry.FatalError):
            crawl.discover_run(MagicMock(), tenant_id="t", run_id="r", job_id="j")


def test_discover_run_skips_upload_run_with_a_base_url_target():
    # An upload run carries a `target` only as a base-URL hint (REQ-C2); it must
    # NOT be crawled. input_ref present => upload => no crawl, no seed egress/DNS.
    with patch("recon.discover.crawl.queries.latest_assets_event", return_value=None), \
         patch("recon.discover.crawl._load_target",
               return_value=("acme.io", "sess-1", "t/r/input/deadbeef")), \
         patch("recon.discover.crawl.sessions_service.get_session") as get_session, \
         patch("recon.discover.crawl.egress.validate_target") as validate_target, \
         patch("recon.discover.crawl.harness.run_crawl") as run_crawl:
        crawl.discover_run(MagicMock(), tenant_id="t", run_id="r", job_id="j")
    run_crawl.assert_not_called()  # no crawl for an upload
    validate_target.assert_not_called()  # no seed egress/DNS for an upload
    get_session.assert_not_called()  # returned before the authorization check


def test_discover_run_skips_target_with_path():
    # A single-asset URL target is NOT a crawl — no event, no rows (backward compat).
    with patch("recon.discover.crawl.queries.latest_assets_event", return_value=None), \
         patch("recon.discover.crawl._load_target",
               return_value=("https://acme.io/app.js", "sess-1", None)), \
         patch("recon.discover.crawl.sessions_service.get_session"), \
         patch("recon.discover.crawl.harness.run_crawl") as run_crawl:
        crawl.discover_run(MagicMock(), tenant_id="t", run_id="r", job_id="j")
    run_crawl.assert_not_called()


def test_discover_run_seeds_run_asset_rows():
    engagement = SimpleNamespace(scope_hosts=["acme.io"], authorization_ack=True)
    seeded = {}
    with patch("recon.discover.crawl.record_event", return_value=MagicMock()), \
         patch("recon.discover.crawl.publish"), \
         patch("recon.discover.crawl.assets.seed_pending",
               side_effect=lambda s, **k: seeded.update(k)):
        for p in _patches(
            katana_urls=["https://acme.io/app.js", "https://acme.io/vendor.js"],
            validated={"https://acme.io/app.js", "https://acme.io/vendor.js"},
            engagement=engagement,
        ):
            p.start()
        try:
            crawl.discover_run(MagicMock(), tenant_id="t", run_id="r", job_id="j")
        finally:
            patch.stopall()
    assert seeded["urls"] == ["https://acme.io/app.js", "https://acme.io/vendor.js"]


def test_discover_run_rejects_seed_that_fails_egress_guard():
    # SSRF (S2): the crawl SEED goes through the full egress guard before katana
    # launches. If it fails (out of scope, or resolves to an internal IP), the run
    # is fatal and katana NEVER launches — the seed can't become an SSRF pivot.
    engagement = SimpleNamespace(scope_hosts=["acme.io"], authorization_ack=True)
    with patch("recon.discover.crawl.queries.latest_assets_event", return_value=None), \
         patch("recon.discover.crawl._load_target", return_value=("acme.io", "sess-1", None)), \
         patch("recon.discover.crawl.sessions_service.get_session", return_value=engagement), \
         patch("recon.discover.crawl.harness.run_crawl") as run_crawl, \
         patch("recon.discover.crawl.egress.validate_target",
               side_effect=egress.EgressBlocked(
                   "host acme.io resolves to a non-public address: 169.254.169.254")):
        with pytest.raises(retry.FatalError):
            crawl.discover_run(MagicMock(), tenant_id="t", run_id="r", job_id="j")
    run_crawl.assert_not_called()


def test_discover_run_builds_a_schemed_seed_url_for_the_guard(monkeypatch):
    # The bare-domain target must become a real URL, else the guard's scheme check
    # would fatally block EVERY crawl. Run the REAL guard (DNS stubbed to a public
    # IP) and assert katana is reached — proving the seed URL is well-formed, not
    # merely that validate_target is called.
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("93.184.216.34"))
    engagement = SimpleNamespace(scope_hosts=["acme.io"], authorization_ack=True)
    with patch("recon.discover.crawl.tenant_session"), \
         patch("recon.discover.crawl.queries.latest_assets_event", return_value=None), \
         patch("recon.discover.crawl._load_target", return_value=("acme.io", "sess-1", None)), \
         patch("recon.discover.crawl.sessions_service.get_session", return_value=engagement), \
         patch("recon.discover.crawl.harness.run_crawl",
               return_value=CrawlResult(stdout=b"", timed_out=False)) as run_crawl, \
         patch("recon.discover.crawl.katana.parse_assets", return_value=[]), \
         patch("recon.discover.crawl.storage.put_blob", return_value="t/r/assets/x"), \
         patch("recon.discover.crawl.assets.seed_pending"), \
         patch("recon.discover.crawl.record_event", return_value=MagicMock()), \
         patch("recon.discover.crawl.publish"):
        crawl.discover_run(MagicMock(), tenant_id="t", run_id="r", job_id="j")
    run_crawl.assert_called_once()
