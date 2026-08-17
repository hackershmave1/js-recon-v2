from typing import Any

import pytest

from recon.findings import techdetect
from recon.findings.techdetect.dataset import RawTechnology


def _signal(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"headers": {}, "scripts": [], "meta": [], "cookies": []}
    base.update(over)
    return base


def test_detects_server_framework_library_and_meta_with_versions() -> None:
    signal = _signal(
        headers={"server": "nginx/1.25.3", "x-powered-by": "Express"},
        scripts=["https://acme.io/static/jquery-3.5.1.min.js"],
        meta=["WordPress 6.4"],
    )
    names = {d.name: d for d in techdetect.detect("acme.io", signal, [])}
    assert names["Nginx"].version == "1.25.3"
    # enthec assigns Nginx more than one category (Web servers + Reverse proxies);
    # assert membership, not an exact list, so a dataset re-pin can't break this.
    assert "Web servers" in names["Nginx"].categories
    assert "Express" in names
    assert names["jQuery"].version == "3.5.1"
    assert names["WordPress"].version == "6.4"


def test_confidence_sums_across_patterns_capped_at_100() -> None:
    # Cloudflare matches on BOTH cf-ray (100) and Server:cloudflare (100) -> capped 100.
    signal = _signal(headers={"cf-ray": "7d1b-EWR", "server": "cloudflare"})
    cloudflare = next(d for d in techdetect.detect("acme.io", signal, []) if d.name == "Cloudflare")
    assert cloudflare.confidence == 100
    assert len(cloudflare.evidence) == 2  # both surfaces recorded


def test_evidence_is_bounded_and_secret_free() -> None:
    signal = _signal(headers={"server": "nginx/1.25.3"})
    nginx = next(d for d in techdetect.detect("acme.io", signal, []) if d.name == "Nginx")
    assert nginx.evidence == ["server: nginx/1.25.3"]
    assert all(len(e) <= 200 for e in nginx.evidence)


def test_no_signal_yields_no_detections() -> None:
    assert techdetect.detect("acme.io", _signal(), []) == []


def test_dataset_commit_and_skip_count_are_exposed() -> None:
    assert isinstance(techdetect.dataset_commit(), str)
    assert techdetect.skipped_pattern_count() >= 0


# --- These tests swap in a synthetic raw dataset (still through the real detect() ->
# _compiled() -> match() path, not by calling match() directly) so the `cookies` and
# `scripts` surfaces, and Re2Match.group() (the version-extracting match below), get
# DETERMINISTIC coverage that doesn't drift when the vendored dataset is re-pinned.


def _detect_with_raw_dataset(
    monkeypatch: pytest.MonkeyPatch,
    raw_techs: dict[str, RawTechnology],
    categories: dict[str, str],
    host: str,
    signal: dict[str, Any],
    js_texts: list[str],
) -> list[techdetect.Detection]:
    """Run detect() against a synthetic raw dataset instead of the vendored one.

    detect() and skipped_pattern_count() both read compiled patterns through the
    module-level `_compiled()` lru_cache, which is keyed on nothing (compile_all's
    dict argument is unhashable) and therefore process-wide. Clear it both before
    (so this call re-compiles from the patched dataset.load_raw) and after (so a
    later test doesn't see this test's synthetic patterns as if they were real).
    """
    monkeypatch.setattr(
        techdetect._dataset, "load_raw", lambda: (raw_techs, categories, "test-commit")
    )
    techdetect._compiled.cache_clear()
    try:
        return techdetect.detect(host, signal, js_texts)
    finally:
        techdetect._compiled.cache_clear()


def test_cookies_surface_is_matched_end_to_end_through_detect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # enthec cookie patterns test presence of the cookie NAME; the field value is
    # conventionally empty (no regex, no version) - see compile._compile_mapping.
    raw: dict[str, RawTechnology] = {
        "SessionCookieTech": {"cats": [19], "cookies": {"connect.sid": ""}},
    }
    signal = _signal(cookies=["connect.sid"])
    detections = _detect_with_raw_dataset(
        monkeypatch, raw, {"19": "Miscellaneous"}, "acme.io", signal, []
    )
    assert len(detections) == 1
    cookie_tech = detections[0]
    assert cookie_tech.name == "SessionCookieTech"
    assert cookie_tech.categories == ["Miscellaneous"]
    assert cookie_tech.confidence == 100
    assert cookie_tech.evidence == ["connect.sid: connect.sid"]


def test_scripts_surface_version_match_bounds_evidence_to_the_matched_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Matched against js_texts (the host's stored JS bytes), NOT signal["scripts"]
    # (that list feeds the dataset's scriptSrc surface instead - see _surface_values).
    raw: dict[str, RawTechnology] = {
        "WidgetLib": {"cats": [59], "scripts": [r"WidgetLib\.init\('([\d.]+)'\)\;version:\1"]},
    }
    js_texts = ["(function(){ WidgetLib.init('2.3.1'); })();"]
    detections = _detect_with_raw_dataset(
        monkeypatch,
        raw,
        {"59": "JavaScript libraries"},
        "acme.io",
        _signal(),
        js_texts,
    )
    assert len(detections) == 1
    widget = detections[0]
    assert widget.name == "WidgetLib"
    assert widget.version == "2.3.1"
    # Bounded to Re2Match.group(0) - the matched substring, NOT the whole js_texts
    # entry (which also contains the surrounding IIFE wrapper).
    assert widget.evidence == ["scripts: WidgetLib.init('2.3.1')"]


def test_scripts_surface_zero_width_match_never_leaks_the_raw_js_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A presence-only "scripts" pattern (empty regex, mirrors the cookies-surface
    # presence test above) matches a zero-width string at position 0, so
    # `found.group(0)` is "". Unlike cookies/headers, `value` here is the entire
    # js_texts entry - falling back to it would leak raw JS (possibly a secret,
    # T1) into `evidence`. The fix: a fixed placeholder, never the raw source.
    raw: dict[str, RawTechnology] = {
        "AnyScriptTech": {"cats": [59], "scripts": [""]},
    }
    js_texts = ["const SECRET_API_KEY = 'sk_live_abc123';"]
    detections = _detect_with_raw_dataset(
        monkeypatch,
        raw,
        {"59": "JavaScript libraries"},
        "acme.io",
        _signal(),
        js_texts,
    )
    assert len(detections) == 1
    any_script = detections[0]
    assert any_script.name == "AnyScriptTech"
    assert any_script.evidence == ["scripts: <scripts match>"]
    assert "SECRET_API_KEY" not in any_script.evidence[0]
    assert "sk_live_abc123" not in any_script.evidence[0]


def test_version_conflict_keeps_highest_confidence_and_files_the_rest_as_alternates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # x-conflict-a (confidence 90, version 1.0) is matched BEFORE x-conflict-b
    # (confidence 40, version 2.0) - the lower-confidence pattern must not overwrite
    # the winning version, but its version must still surface as an alternate
    # rather than being silently dropped (T3).
    raw: dict[str, RawTechnology] = {
        "ConflictTech": {
            "cats": [19],
            "headers": {
                "x-conflict-a": r"tag-a/([\d.]+)\;version:\1\;confidence:90",
                "x-conflict-b": r"tag-b/([\d.]+)\;version:\1\;confidence:40",
            },
        },
    }
    signal = _signal(headers={"x-conflict-a": "tag-a/1.0", "x-conflict-b": "tag-b/2.0"})
    detections = _detect_with_raw_dataset(
        monkeypatch, raw, {"19": "Miscellaneous"}, "acme.io", signal, []
    )
    assert len(detections) == 1
    conflict = detections[0]
    assert conflict.version == "1.0"
    assert conflict.confidence == 100  # 90 + 40 capped at 100
    assert "version alt: 2.0" in conflict.evidence
