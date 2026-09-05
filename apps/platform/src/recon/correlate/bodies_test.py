"""D45b2 — captured-body analysis (secret-scan + light param hints), host-lane hermetic.

Pure parsing (``_top_level_keys``/``_collect_bodies``/``_body_source_path``) needs no infra; the
``analyze_bodies`` orchestration is exercised with Kingfisher + the store/event seams faked, so the
provenance + param-tie wiring is pinned without the real binary or a DB. The real-engine +
live-DB path is ``bodies_integration_test.py``.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from recon.correlate import bodies
from recon.findings.kingfisher import RawSecret

# --------------------------------------------------------------------------- #
# _top_level_keys — conservative JSON-object / urlencoded-form key extraction.
# --------------------------------------------------------------------------- #


def test_top_level_keys_from_json_object():
    assert bodies._top_level_keys('{"username":"a","password":"b","remember":true}') == [
        "username",
        "password",
        "remember",
    ]


def test_top_level_keys_does_not_descend_into_nested_json():
    # Only the TOP level — nested keys are a schema, not a hint.
    assert bodies._top_level_keys('{"filter":{"nested":1},"page":2}') == ["filter", "page"]


def test_top_level_keys_from_urlencoded_form():
    assert bodies._top_level_keys("grant_type=password&username=a&scope=") == [
        "grant_type",
        "username",
        "scope",
    ]


def test_top_level_keys_skips_non_object_json_and_junk():
    # Valid JSON that is not an object -> nothing (don't guess); genuine junk -> nothing.
    assert bodies._top_level_keys("[1,2,3]") == []
    assert bodies._top_level_keys("42") == []
    assert bodies._top_level_keys('"a string"') == []
    assert bodies._top_level_keys("null") == []
    assert bodies._top_level_keys("<html>not a body</html>") == []
    assert bodies._top_level_keys("   ") == []


def test_top_level_keys_rejects_form_shaped_text_with_whitespace():
    # A real urlencoded form has no raw spaces (they encode as + / %20); free text with an
    # '=' must not be mistaken for a form.
    assert bodies._top_level_keys("note = hello world & x = 1") == []


def test_top_level_keys_dedupes_and_caps_fanout():
    dup = '{"a":1,"a":2,"b":3}'  # json.loads keeps the last "a"; dedup collapses to one
    assert bodies._top_level_keys(dup) == ["a", "b"]
    big = "{" + ",".join(f'"k{i}":{i}' for i in range(200)) + "}"
    assert len(bodies._top_level_keys(big)) == bodies._MAX_PARAM_KEYS_PER_BODY


# --------------------------------------------------------------------------- #
# _collect_bodies / _body_source_path
# --------------------------------------------------------------------------- #


def test_collect_bodies_orders_request_before_response_and_skips_empty():
    observed = [
        {"method": "POST", "url": "https://api.acme.io/login", "reqBody": "a=1", "respBody": "{}"},
        {"method": "GET", "url": "https://api.acme.io/ping"},  # no bodies
        {"method": "POST", "url": "https://api.acme.io/x", "reqBody": ""},  # empty -> skipped
        {"method": "POST", "url": "https://api.acme.io/y", "respBody": 123},  # non-str -> skipped
    ]
    collected = bodies._collect_bodies(observed, cap=100)
    assert [(b.kind, b.url, b.text) for b in collected] == [
        ("request", "https://api.acme.io/login", "a=1"),
        ("response", "https://api.acme.io/login", "{}"),
    ]
    assert collected[0].host == "api.acme.io"


def test_collect_bodies_respects_cap():
    observed = [
        {"method": "POST", "url": f"https://a.io/{i}", "reqBody": "x", "respBody": "y"}
        for i in range(10)
    ]
    assert len(bodies._collect_bodies(observed, cap=3)) == 3


def test_body_source_path_is_synthetic_and_kind_tagged():
    req = bodies._Body("request", "api.acme.io", "https://api.acme.io/v1/login", "a=1")
    res = bodies._Body("response", "api.acme.io", "https://api.acme.io/v1/login", "{}")
    assert bodies._body_source_path(req) == "capture-request://api.acme.io/v1/login"
    assert bodies._body_source_path(res) == "capture-response://api.acme.io/v1/login"


# --------------------------------------------------------------------------- #
# analyze_bodies — provenance + param-tie wiring (Kingfisher/store/event faked).
# --------------------------------------------------------------------------- #


class _FakeSettings:
    capture_max_bodies = 500
    engine_max_output_bytes = 32 * 1024 * 1024


def _finding(finding_hash, value, path="app/login.js"):
    return SimpleNamespace(finding_hash=finding_hash, type="endpoint", value=value, path=path)


def _run_bodies(*, observed, resolved, by_hash, secrets_by_index, status="ok"):
    recorded: list[dict] = []
    events: list[dict] = []

    def _record(_session, **kw):
        recorded.append(kw)
        return SimpleNamespace(finding_created=True, occurrence_created=True)

    with (
        patch.object(bodies, "get_settings", lambda: _FakeSettings()),
        patch.object(
            bodies.kingfisher, "scan_many", return_value=(secrets_by_index, status)
        ) as scan_many,
        patch.object(bodies, "tenant_session"),
        patch.object(bodies.store, "record_finding", side_effect=_record),
        patch.object(
            bodies, "record_event", side_effect=lambda *a, **k: events.append(k) or MagicMock()
        ),
        patch.object(bodies, "publish"),
    ):
        bodies.analyze_bodies(
            MagicMock(),
            tenant_id="t",
            run_id="r",
            observed=observed,
            resolved=resolved,
            by_hash=by_hash,
        )
    return recorded, events, scan_many


def test_analyze_bodies_records_secret_with_capture_provenance():
    observed = [
        {"method": "POST", "url": "https://api.acme.io/login", "respBody": '{"token":"sk_live_x"}'}
    ]
    secret = RawSecret(
        rule_id="kingfisher.stripe.1", rule_name="Stripe", snippet="sk_live_x", line=1
    )
    recorded, events, _ = _run_bodies(
        observed=observed, resolved={}, by_hash={}, secrets_by_index={0: [secret]}
    )
    secrets = [kw for kw in recorded if str(kw["finding_type"]) == "secret"]
    assert len(secrets) == 1
    kw = secrets[0]
    assert kw["value"].startswith("stripe:")  # provider:sha256, raw token never stored
    assert kw["path"] == "capture-response://api.acme.io/login"
    occ = kw["occurrence"]
    assert occ.engine == "capture"
    assert occ.host == "api.acme.io"
    assert occ.raw_url == "https://api.acme.io/login"
    assert occ.source_path == "capture-response://api.acme.io/login"
    # Offset-less on purpose (a captured body is not a revealable blob).
    assert occ.offset_start is None and occ.offset_end is None
    assert kw["first_stage"] == "correlating"
    assert events[0]["event_type"] == "correlate.bodies"
    assert events[0]["payload"] == {
        "bodies": 1,
        "secrets": 1,
        "params": 0,
        "secrets_engine": "ok",
    }


def test_analyze_bodies_extracts_params_only_for_matched_request_body():
    # A request body whose URL matched an endpoint in correlate contributes top-level keys as
    # PARAM findings tied to that endpoint's operation.
    observed = [{"method": "POST", "url": "https://api.acme.io/login", "reqBody": "user=a&pw=b"}]
    resolved = {"h1": "https://api.acme.io/login"}
    by_hash = {"h1": _finding("h1", "POST /login")}
    recorded, _events, _ = _run_bodies(
        observed=observed, resolved=resolved, by_hash=by_hash, secrets_by_index={}
    )
    params = [kw for kw in recorded if str(kw["finding_type"]) == "param"]
    assert {kw["value"] for kw in params} == {"POST /login body:user", "POST /login body:pw"}
    kw = params[0]
    assert kw["path"] == "app/login.js"  # reuses the matched endpoint's finding path
    assert kw["occurrence"].engine == "capture"
    assert kw["occurrence"].source_path == "capture-request://api.acme.io/login"
    assert kw["attributes"] == {"location": "body", "name": "user"}


def test_analyze_bodies_no_params_for_unmatched_url_or_response_body():
    # reqBody whose URL matched NOTHING -> no params; respBody is never param-mined.
    observed = [
        {"method": "POST", "url": "https://api.acme.io/unmatched", "reqBody": "a=1"},
        {"method": "POST", "url": "https://api.acme.io/login", "respBody": "b=2"},
    ]
    resolved = {"h1": "https://api.acme.io/login"}  # only /login matched, and it's a RESPONSE body
    by_hash = {"h1": _finding("h1", "POST /login")}
    recorded, _events, _ = _run_bodies(
        observed=observed, resolved=resolved, by_hash=by_hash, secrets_by_index={}
    )
    assert [kw for kw in recorded if str(kw["finding_type"]) == "param"] == []


def test_analyze_bodies_no_bodies_is_a_noop_without_a_scan():
    observed = [{"method": "GET", "url": "https://api.acme.io/ping"}]  # no bodies
    recorded, events, scan_many = _run_bodies(
        observed=observed, resolved={}, by_hash={}, secrets_by_index={}
    )
    assert recorded == [] and events == []
    scan_many.assert_not_called()
