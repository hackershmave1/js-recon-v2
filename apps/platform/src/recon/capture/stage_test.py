import hashlib
import json
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
        capture_max_requests=1000,
        capture_interact=True,
        capture_max_scroll_steps=12,
        capture_max_clicks=40,
        capture_max_routes=15,
        crawl_duration_seconds=5.0,
        crawl_kill_grace_seconds=1.0,
        crawl_heartbeat_interval_seconds=0.1,
        crawl_fetch_source_maps=True,
        fetch_timeout_seconds=20.0,
        max_fetch_bytes=10 * 1024 * 1024,
        max_fetch_bytes_ceiling=32 * 1024 * 1024,
        allow_local_egress=False,
    )


def _script(
    url: str, src: str, target_type: str = "page", source_map_url: str | None = None
) -> CapturedScript:
    raw = src.encode()
    return CapturedScript(
        url=url,
        source=raw,
        source_map_url=source_map_url,
        sha256=hashlib.sha256(raw).hexdigest(),
        target_type=target_type,
    )


def _blob(_tenant, _run, _kind, content):
    return f"blob/{hashlib.sha256(content).hexdigest()[:12]}"


def _run_capture(
    scripts,
    engagement,
    *,
    enabled=True,
    validate=None,
    nav_error=None,
    map_fetch=None,
    fetch_maps=True,
    requests=None,
    blobs=None,
    events=None,
    headers_by_host=None,
    cookies_by_host=None,
    meta=None,
):
    recorded = {}
    seeded = {}
    settings = _settings(enabled)
    settings.crawl_fetch_source_maps = fetch_maps

    def _cap_blob(_tenant, _run, kind, content):
        if blobs is not None:
            blobs.setdefault(kind, []).append(content)
        return _blob(_tenant, _run, kind, content)

    def _record(_session, **k):
        # capture_run may record more than one event per run (discover.assets,
        # then an optional fingerprint.signal — Task 7): accumulate every call
        # into `events` (opt-in, like `blobs`) while `recorded` keeps meaning
        # "the FIRST event", so every existing assertion (written when there was
        # only ever one call) still inspects the discover.assets payload.
        if events is not None:
            events.append(dict(k))
        if not recorded:
            recorded.update(k)
        return MagicMock()

    with (
        patch("recon.capture.stage.get_settings", return_value=settings),
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
            return_value=CaptureResult(
                scripts=scripts,
                nav_error=nav_error,
                requests=requests or [],
                headers_by_host=headers_by_host or {},
                cookies_by_host=cookies_by_host or {},
                meta=meta or [],
            ),
        ),
        patch("recon.capture.stage.storage.put_blob", side_effect=_cap_blob),
        # The guarded .map GET + the on_progress folds (cancel-check + lease beat) are
        # stubbed so the seeding pass runs without real egress or a real DB/redis.
        patch(
            "recon.capture.stage.fetch.fetch_url",
            side_effect=map_fetch or (lambda *a, **k: b'{"version":3,"sources":[]}'),
        ),
        patch("recon.capture.stage.run_queries.raise_if_control_requested"),
        patch("recon.capture.stage.progress.beat"),
        patch("recon.capture.stage.tenant_session"),
        patch(
            "recon.capture.stage.assets.seed_captured",
            side_effect=lambda s, **k: seeded.update(k),
        ),
        patch("recon.capture.stage.record_event", side_effect=_record),
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


def test_capture_persists_in_scope_observed_requests():
    # REQ-C3: observed XHR/fetch URLs are filtered to in-scope hosts (a subdomain of a
    # scoped host is in scope) and stored as a blob referenced from the discover.assets
    # event, for the correlate stage. A third-party request is dropped like a script.
    engagement = SimpleNamespace(scope_hosts=["acme.io"], authorization_ack=True)
    scripts = [_script("https://acme.io/app.js", "APP")]
    requests = [
        {"method": "GET", "url": "https://api.acme.io/get-job-types"},  # subdomain -> in scope
        {"method": "POST", "url": "https://acme.io/getJobId"},  # target host
        {"method": "GET", "url": "https://evil.cdn/track"},  # out of scope -> dropped
    ]
    blobs: dict = {}
    recorded, _seeded, _publish = _run_capture(scripts, engagement, requests=requests, blobs=blobs)

    assert recorded["payload"]["requests_ref"]  # referenced from the discover.assets event
    stored = json.loads(blobs["capture-requests"][0])
    assert stored == [
        {"method": "GET", "url": "https://api.acme.io/get-job-types"},
        {"method": "POST", "url": "https://acme.io/getJobId"},
    ]


def test_capture_writes_one_fingerprint_signal_blob_and_event():
    # Task 7 / T6: a capture run that harvested headers/cookies/meta writes exactly
    # ONE fingerprint-signal blob + fingerprint.signal event (never per-asset), same
    # schema/event type the fetch stage emits, so analyze reads either mode alike.
    engagement = SimpleNamespace(scope_hosts=["acme.io"], authorization_ack=True)
    scripts = [_script("https://acme.io/app.js", "APP")]
    blobs: dict = {}
    events: list = []
    recorded, _seeded, _publish = _run_capture(
        scripts,
        engagement,
        blobs=blobs,
        events=events,
        headers_by_host={"acme.io": {"server": "nginx/1.25.3"}},
        cookies_by_host={"acme.io": ["sid"]},
        meta=["WordPress 6.4"],
    )

    assert recorded["event_type"] == "discover.assets"  # the FIRST event is unchanged
    signal_events = [e for e in events if e["event_type"] == "fingerprint.signal"]
    assert len(signal_events) == 1  # ONE event for the whole run, not one per asset
    assert signal_events[0]["payload"]["hosts"] == 1

    signal = json.loads(blobs["fingerprint-signal"][0])
    assert signal == {
        "acme.io": {
            "headers": {"server": "nginx/1.25.3"},
            "scripts": ["https://acme.io/app.js"],
            "meta": ["WordPress 6.4"],
            "cookies": ["sid"],
        }
    }


def test_capture_writes_no_fingerprint_signal_when_nothing_harvested():
    # The blob/event pair is skipped entirely (not written empty) when a capture
    # harvested no headers/cookies/meta at all — mirrors the fetch-side "if signal"
    # guard (T6): no noise blob for a run that saw nothing.
    engagement = SimpleNamespace(scope_hosts=["acme.io"], authorization_ack=True)
    blobs: dict = {}
    events: list = []
    _run_capture([], engagement, blobs=blobs, events=events)

    assert "fingerprint-signal" not in blobs
    assert all(e["event_type"] != "fingerprint.signal" for e in events)


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


def test_capture_fetches_external_source_map_and_links_ref():
    # An external sourceMapURL is resolved against the script's real URL, guarded-fetched,
    # and linked on the matching seeded row (so ANALYZE recovers real source paths).
    engagement = SimpleNamespace(scope_hosts=["acme.io"], authorization_ack=True)
    fetch_url = MagicMock(return_value=b'{"version":3,"sources":["app/src/api.js"]}')
    scripts = [_script("https://acme.io/app.js", "APP", source_map_url="app.js.map")]
    _recorded, seeded, _publish = _run_capture(scripts, engagement, map_fetch=fetch_url)

    assert fetch_url.call_args.args[0] == "https://acme.io/app.js.map"  # relative -> resolved
    assert fetch_url.call_args.args[1] == ["acme.io"]  # scope_hosts handed to the guard
    assert seeded["rows"][0]["source_map_ref"]  # a stored blob key, not None


def test_capture_inline_data_map_is_not_fetched():
    # An inline data: map is recovered downstream by analyze from the source's own
    # //# sourceMappingURL= comment — the capture stage must NOT re-fetch it.
    engagement = SimpleNamespace(scope_hosts=["acme.io"], authorization_ack=True)
    fetch_url = MagicMock()
    scripts = [
        _script("https://acme.io/app.js", "APP", source_map_url="data:application/json;base64,e30=")
    ]
    _recorded, seeded, _publish = _run_capture(scripts, engagement, map_fetch=fetch_url)

    fetch_url.assert_not_called()
    assert seeded["rows"][0]["source_map_ref"] is None


def test_capture_source_map_soft_miss_keeps_asset():
    # A blocked/oversized/malformed .map is a soft miss: source_map_ref stays null, the
    # script's own row is still seeded ok, and the whole capture run does NOT fail.
    engagement = SimpleNamespace(scope_hosts=["acme.io"], authorization_ack=True)

    def blocked(*_a, **_k):
        raise RuntimeError("egress blocked / oversized")

    scripts = [_script("https://acme.io/app.js", "APP", source_map_url="app.js.map")]
    recorded, seeded, publish = _run_capture(scripts, engagement, map_fetch=blocked)

    assert seeded["rows"][0]["source_map_ref"] is None
    assert seeded["rows"][0]["input_ref"]  # the captured script blob is untouched
    assert recorded["payload"]["count"] == 1
    assert recorded["payload"]["status"] == "ok"
    publish.assert_called_once()


def test_capture_malformed_source_map_url_does_not_abort_run():
    # THE critical soft-miss boundary (adversarial gate must-fix #1): a crafted
    # sourceMapURL that makes urljoin RAISE (invalid IPv6 literal) must not abort the
    # seeding pass — it soft-misses that one script and the others still seed.
    engagement = SimpleNamespace(scope_hosts=["acme.io"], authorization_ack=True)
    fetch_url = MagicMock(return_value=b"{}")
    scripts = [
        _script("https://acme.io/bad.js", "BAD", source_map_url="//[::"),
        _script("https://acme.io/good.js", "GOOD", source_map_url="good.js.map"),
    ]
    _recorded, seeded, _publish = _run_capture(scripts, engagement, map_fetch=fetch_url)

    by_url = {r["url"]: r for r in seeded["rows"]}
    assert by_url["https://acme.io/bad.js"]["source_map_ref"] is None  # raise -> soft miss
    assert by_url["https://acme.io/good.js"]["source_map_ref"]  # the pass continued


def test_capture_source_maps_disabled_skips_fetch():
    # The crawl_fetch_source_maps kill-switch governs capture too: no .map GET at all.
    engagement = SimpleNamespace(scope_hosts=["acme.io"], authorization_ack=True)
    fetch_url = MagicMock()
    scripts = [_script("https://acme.io/app.js", "APP", source_map_url="app.js.map")]
    _recorded, seeded, _publish = _run_capture(
        scripts, engagement, map_fetch=fetch_url, fetch_maps=False
    )

    fetch_url.assert_not_called()
    assert seeded["rows"][0]["source_map_ref"] is None


def test_capture_anonymous_script_map_resolves_against_document():
    # An anonymous/eval'd script (row url vm://<sha>) with a RELATIVE external map must
    # resolve against the document URL, never the vm:// placeholder.
    engagement = SimpleNamespace(scope_hosts=["acme.io"], authorization_ack=True)
    fetch_url = MagicMock(return_value=b"{}")
    scripts = [_script("", "ANON_EVAL", source_map_url="vm.js.map")]
    _recorded, seeded, _publish = _run_capture(scripts, engagement, map_fetch=fetch_url)

    assert fetch_url.call_args.args[0] == "https://acme.io/vm.js.map"  # vs the seed document
    assert seeded["rows"][0]["url"].startswith("vm://")  # row url stays content-addressed
    assert seeded["rows"][0]["source_map_ref"]


def test_capture_source_map_cancel_propagates_before_get():
    # A cancel observed during the source-map pass propagates out of capture_run (the
    # on_progress beat is OUTSIDE the soft-miss try, so a genuine cancel is never
    # swallowed) and no .map GET is issued.
    engagement = SimpleNamespace(scope_hosts=["acme.io"], authorization_ack=True)
    fetch_url = MagicMock()
    scripts = [_script("https://acme.io/app.js", "APP", source_map_url="app.js.map")]

    with (
        patch("recon.capture.stage.get_settings", return_value=_settings(True)),
        patch("recon.capture.stage.sessions_service.get_session", return_value=engagement),
        patch("recon.capture.stage.egress.validate_target", return_value=SimpleNamespace()),
        patch("recon.capture.stage.egress.host_of", return_value="acme.io"),
        patch(
            "recon.capture.stage.egress.host_in_scope",
            side_effect=lambda h, hosts, **k: any(h == x for x in hosts),
        ),
        patch(
            "recon.capture.stage.driver.capture_scripts",
            return_value=CaptureResult(scripts=scripts, nav_error=None),
        ),
        patch("recon.capture.stage.storage.put_blob", side_effect=_blob),
        patch("recon.capture.stage.fetch.fetch_url", side_effect=fetch_url),
        patch(
            "recon.capture.stage.run_queries.raise_if_control_requested",
            side_effect=retry.ControlInterrupt("cancel"),
        ),
        patch("recon.capture.stage.progress.beat"),
        patch("recon.capture.stage.tenant_session"),
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
    fetch_url.assert_not_called()
