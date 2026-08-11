import hashlib
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.parse import urlsplit

import pytest

from recon.capture import stage
from recon.capture.driver import CapturedScript, CaptureResult
from recon.fetch import egress
from recon.queue import retry


def _settings(enabled=True):
    return SimpleNamespace(
        enable_capture_mode=enabled,
        system_chrome_path="/usr/bin/chromium",
        capture_nav_timeout_seconds=1.0,
        capture_idle_settle_seconds=0.1,
        capture_max_scripts=100,
        crawl_duration_seconds=5.0,
        crawl_kill_grace_seconds=1.0,
        crawl_heartbeat_interval_seconds=0.1,
        max_fetch_bytes=10 * 1024 * 1024,
        allow_local_egress=False,
    )


def _script(url: str, src: str, target_type: str = "page") -> CapturedScript:
    raw = src.encode()
    return CapturedScript(
        url=url,
        source=raw,
        source_map_url=None,
        sha256=hashlib.sha256(raw).hexdigest(),
        target_type=target_type,
    )


def _blob(_tenant, _run, _kind, content):
    return f"blob/{hashlib.sha256(content).hexdigest()[:12]}"


def _run_capture(scripts, engagement, *, enabled=True, validate=None, nav_error=None):
    recorded = {}
    seeded = {}
    with (
        patch("recon.capture.stage.get_settings", return_value=_settings(enabled)),
        patch("recon.capture.stage.sessions_service.get_session", return_value=engagement),
        patch(
            "recon.capture.stage.egress.validate_target",
            side_effect=validate or (lambda *a, **k: SimpleNamespace()),
        ),
        patch(
            "recon.capture.stage.egress.host_of", side_effect=lambda u: urlsplit(u).hostname or ""
        ),
        patch(
            "recon.capture.stage.egress.host_in_scope",
            side_effect=lambda h, hosts, **k: any(h == x or h.endswith("." + x) for x in hosts),
        ),
        patch(
            "recon.capture.stage.driver.capture_scripts",
            return_value=CaptureResult(scripts=scripts, nav_error=nav_error),
        ),
        patch("recon.capture.stage.storage.put_blob", side_effect=_blob),
        patch("recon.capture.stage.tenant_session"),
        patch(
            "recon.capture.stage.assets.seed_captured",
            side_effect=lambda s, **k: seeded.update(k),
        ),
        patch(
            "recon.capture.stage.record_event",
            side_effect=lambda *a, **k: recorded.update(k) or MagicMock(),
        ),
        patch("recon.capture.stage.publish") as publish,
    ):
        stage.capture_run(
            MagicMock(),
            tenant_id="t",
            run_id="r",
            job_id="j",
            target="acme.io",
            session_id="sess-1",
        )
    return recorded, seeded, publish


def test_capture_seeds_fetched_rows_and_drops_out_of_scope():
    engagement = SimpleNamespace(scope_hosts=["acme.io"], authorization_ack=True)
    scripts = [
        _script("https://acme.io/app.js", "EXTERNAL"),
        _script("", "eval-generated-c16"),  # anonymous VM-only — the completeness case
        _script("https://evil.cdn/track.js", "THIRD_PARTY"),  # out of scope -> dropped
    ]
    recorded, seeded, publish = _run_capture(scripts, engagement)

    urls = [r["url"] for r in seeded["rows"]]
    assert "https://acme.io/app.js" in urls  # in-scope external keeps its URL
    assert any(u.startswith("vm://") for u in urls)  # anonymous -> content-addressed URL
    assert all("evil.cdn" not in u for u in urls)  # third-party dropped
    assert all(r["input_ref"] for r in seeded["rows"])  # each row carries a stored blob
    assert recorded["payload"]["count"] == 2
    assert recorded["payload"]["status"] == "ok"
    assert recorded["event_type"] == "discover.assets"
    publish.assert_called_once()


def test_capture_rejects_unauthorized_session():
    engagement = SimpleNamespace(scope_hosts=["acme.io"], authorization_ack=False)
    with pytest.raises(retry.FatalError, match="not authorized"):
        _run_capture([], engagement)


def test_capture_disabled_killswitch_is_fatal():
    engagement = SimpleNamespace(scope_hosts=["acme.io"], authorization_ack=True)
    with pytest.raises(retry.FatalError, match="disabled"):
        _run_capture([], engagement, enabled=False)


def test_capture_seed_egress_block_is_fatal():
    engagement = SimpleNamespace(scope_hosts=["acme.io"], authorization_ack=True)

    def blocked(*_a, **_k):
        raise egress.EgressBlocked("resolves to 169.254.169.254")

    with pytest.raises(retry.FatalError, match="egress guard"):
        _run_capture([], engagement, validate=blocked)


def test_asset_rows_unique_and_content_stable():
    # Two inline blocks report the SAME document URL; an external file once; an
    # anonymous eval'd script. Rows must be unique and identical across re-runs.
    scripts = [
        _script("https://acme.io/", "INLINE_A"),
        _script("https://acme.io/", "INLINE_B"),
        _script("https://acme.io/vendor.js", "VENDOR"),
        _script("", "ANON"),
    ]
    with patch("recon.capture.stage.storage.put_blob", side_effect=_blob):
        first = stage._asset_rows(scripts, tenant_id="t", run_id="r")
        second = stage._asset_rows(scripts, tenant_id="t", run_id="r")
    urls = [r["url"] for r in first]
    assert len(set(urls)) == 4  # all unique despite the shared inline URL
    assert "https://acme.io/vendor.js" in urls  # unique external keeps its URL
    assert sum(u.startswith("vm://") for u in urls) == 1  # the anonymous script
    assert [r["url"] for r in second] == urls  # content-stable across re-capture


def test_capture_nav_error_marks_blocked():
    # A hard navigation failure surfaces as status "blocked" (-> PARTIAL in finalize),
    # never a false "ok"/DONE with zero captured scripts.
    engagement = SimpleNamespace(scope_hosts=["acme.io"], authorization_ack=True)
    recorded, _seeded, _publish = _run_capture(
        [], engagement, nav_error="net::ERR_NAME_NOT_RESOLVED"
    )
    assert recorded["payload"]["status"] == "blocked"
    assert recorded["payload"]["count"] == 0


def test_capture_propagates_control_interrupt_via_on_progress():
    # REQ-A4 wiring: the driver's on_progress runs the stage's pause/cancel check,
    # whose ControlInterrupt must propagate out of capture_run (the worker maps it to
    # paused/cancelled).
    engagement = SimpleNamespace(scope_hosts=["acme.io"], authorization_ack=True)

    def fake_capture(*_a, on_progress, **_k):
        on_progress(0)  # the driver calls this at least once while driving
        return CaptureResult(scripts=[], nav_error=None)

    with (
        patch("recon.capture.stage.get_settings", return_value=_settings(True)),
        patch("recon.capture.stage.sessions_service.get_session", return_value=engagement),
        patch("recon.capture.stage.egress.validate_target", return_value=SimpleNamespace()),
        patch("recon.capture.stage.egress.host_of", return_value="acme.io"),
        patch("recon.capture.stage.driver.capture_scripts", side_effect=fake_capture),
        patch(
            "recon.capture.stage.run_queries.raise_if_control_requested",
            side_effect=retry.ControlInterrupt("cancel"),
        ),
        pytest.raises(retry.ControlInterrupt),
    ):
        stage.capture_run(
            MagicMock(),
            tenant_id="t",
            run_id="r",
            job_id="j",
            target="acme.io",
            session_id="sess-1",
        )
