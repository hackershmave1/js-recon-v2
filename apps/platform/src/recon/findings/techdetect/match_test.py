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


# --- js (window-global) surface: presence-matched in bundle SOURCE via one RE2 Set.
# No runtime value, so version is always None and confidence is capped (a js-only tech
# reads as "suspected", never "certain"). Same synthetic-dataset path as above.


def test_js_surface_presence_fires_with_no_version_and_bounded_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw: dict[str, RawTechnology] = {
        "JsFramework": {"cats": [59], "js": {"__appruntime__": "^(.+)$\\;version:\\1"}},
    }
    # The enthec value regex targets the global's RUNTIME value; static source can't
    # supply it, so the key is presence-matched and no version is recovered.
    js_texts = ["(function(){ window.__appruntime__ = {v: '9'}; })();"]
    detections = _detect_with_raw_dataset(
        monkeypatch, raw, {"59": "JavaScript libraries"}, "acme.io", _signal(), js_texts
    )
    assert len(detections) == 1
    tech = detections[0]
    assert tech.name == "JsFramework"
    assert tech.categories == ["JavaScript libraries"]
    assert tech.version is None
    assert tech.confidence == 50  # _JS_SURFACE_CEILING - suspected, not certain
    assert tech.evidence == ["js: __appruntime__"]
    assert all(len(e) <= 200 for e in tech.evidence)


def test_js_surface_is_case_sensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    raw: dict[str, RawTechnology] = {"CaseTech": {"cats": [59], "js": {"__MyGlobal__": ""}}}
    hit = _detect_with_raw_dataset(
        monkeypatch, raw, {"59": "JS"}, "acme.io", _signal(), ["a __MyGlobal__ b"]
    )
    assert [d.name for d in hit] == ["CaseTech"]
    miss = _detect_with_raw_dataset(
        monkeypatch, raw, {"59": "JS"}, "acme.io", _signal(), ["a __myglobal__ b"]
    )
    assert miss == []


def test_js_surface_matches_at_token_boundaries_only(monkeypatch: pytest.MonkeyPatch) -> None:
    raw: dict[str, RawTechnology] = {"BoundTech": {"cats": [59], "js": {"myglobalx": ""}}}
    fires = _detect_with_raw_dataset(
        monkeypatch, raw, {"59": "JS"}, "acme.io", _signal(), ["call(myglobalx);"]
    )
    assert [d.name for d in fires] == ["BoundTech"]
    # A substring of a longer identifier must NOT fire (word-bounded).
    inside = _detect_with_raw_dataset(
        monkeypatch, raw, {"59": "JS"}, "acme.io", _signal(), ["var xmyglobalxy = 1;"]
    )
    assert inside == []


def test_js_surface_matches_dollar_prefixed_global(monkeypatch: pytest.MonkeyPatch) -> None:
    # A `$` sigil keeps a short key (no leading \b, so the `$` boundary is not mis-anchored).
    raw: dict[str, RawTechnology] = {"DollarTech": {"cats": [59], "js": {"$myapp": ""}}}
    fires = _detect_with_raw_dataset(
        monkeypatch, raw, {"59": "JS"}, "acme.io", _signal(), ["window.$myapp = init();"]
    )
    assert [d.name for d in fires] == ["DollarTech"]


def test_js_surface_drops_non_distinctive_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    # `Vue` (<4 chars) and `core` (bare word <8) are FP magnets present in the source but
    # deliberately never enter the Set -> no detection.
    raw: dict[str, RawTechnology] = {"ShortTech": {"cats": [59], "js": {"Vue": "", "core": ""}}}
    js_texts = ["var Vue = 1; require('core');"]
    assert (
        _detect_with_raw_dataset(monkeypatch, raw, {"59": "JS"}, "acme.io", _signal(), js_texts)
        == []
    )


def test_js_only_tech_is_capped_and_can_never_be_certain(monkeypatch: pytest.MonkeyPatch) -> None:
    # Two js globals both present would naively sum to 200 -> a phantom "certain" 100.
    # The per-tech js cap holds a js-only tech at the suspected ceiling instead (OBJ-2).
    raw: dict[str, RawTechnology] = {
        "JsOnly": {"cats": [59], "js": {"__frameworkcore__": "", "__frameworkboot__": ""}},
    }
    js_texts = ["window.__frameworkcore__ = 1; window.__frameworkboot__ = 1;"]
    detections = _detect_with_raw_dataset(
        monkeypatch, raw, {"59": "JS"}, "acme.io", _signal(), js_texts
    )
    assert len(detections) == 1
    assert detections[0].confidence == 50  # NOT 100
    assert detections[0].evidence == ["js: __frameworkcore__", "js: __frameworkboot__"]


def test_phase1_corroboration_pushes_a_js_tech_past_the_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Same tech, now with a Phase-1 header hit (confidence 40): 40 + capped-js(50) = 90,
    # crossing the ceiling that js-source-alone cannot.
    raw: dict[str, RawTechnology] = {
        "JsFramework": {
            "cats": [59],
            "headers": {"x-fw": r"fw\;confidence:40"},
            "js": {"__frameworkcore__": ""},
        },
    }
    signal = _signal(headers={"x-fw": "fw"})
    js_texts = ["window.__frameworkcore__ = 1;"]
    detections = _detect_with_raw_dataset(
        monkeypatch, raw, {"59": "JS"}, "acme.io", signal, js_texts
    )
    assert len(detections) == 1
    assert detections[0].confidence == 90  # 40 (phase-1) + min(100, 50) (js)


def test_js_texts_with_no_match_or_empty_are_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    raw: dict[str, RawTechnology] = {"JsTech": {"cats": [59], "js": {"__presentglobal__": ""}}}
    # Present but unmatched (Set.Match -> None) must not crash or detect.
    assert (
        _detect_with_raw_dataset(
            monkeypatch, raw, {"59": "JS"}, "acme.io", _signal(), ["nothing relevant here"]
        )
        == []
    )
    # No JS text at all -> no js detections.
    assert _detect_with_raw_dataset(monkeypatch, raw, {"59": "JS"}, "acme.io", _signal(), []) == []


def test_js_surface_resolves_shared_and_distinct_keys_to_correct_techs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A global name shared by two techs is added to the Set once PER tech (distinct
    # indices), so both fire; a tech-only key fires just its own tech.
    raw: dict[str, RawTechnology] = {
        "AlphaLib": {"cats": [59], "js": {"__sharedglobal__": "", "__alphaonly__": ""}},
        "BetaLib": {"cats": [59], "js": {"__sharedglobal__": "", "__betaonly__": ""}},
    }
    js_texts = ["__sharedglobal__; __alphaonly__;"]
    detections = {
        d.name: d
        for d in _detect_with_raw_dataset(
            monkeypatch, raw, {"59": "JS"}, "acme.io", _signal(), js_texts
        )
    }
    assert set(detections) == {"AlphaLib", "BetaLib"}
    assert detections["AlphaLib"].evidence == ["js: __sharedglobal__", "js: __alphaonly__"]
    assert detections["BetaLib"].evidence == ["js: __sharedglobal__"]


def test_js_evidence_never_leaks_surrounding_source(monkeypatch: pytest.MonkeyPatch) -> None:
    # Set.Match yields indices only (no offsets), so evidence is the key literal - a
    # secret sitting next to the matched global can never reach `evidence` (T1).
    raw: dict[str, RawTechnology] = {"JsTech": {"cats": [59], "js": {"__appmarker__": ""}}}
    js_texts = ["const SECRET_API_KEY = 'sk_live_abc123'; window.__appmarker__ = 1;"]
    detection = _detect_with_raw_dataset(
        monkeypatch, raw, {"59": "JS"}, "acme.io", _signal(), js_texts
    )[0]
    assert detection.evidence == ["js: __appmarker__"]
    assert "SECRET_API_KEY" not in detection.evidence[0]
    assert "sk_live_abc123" not in detection.evidence[0]


def test_js_surface_dedups_a_global_seen_across_multiple_assets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The same global present in two separate JS assets is ONE signal (dedup by Set
    # index across js_texts): recorded once, confidence counted once.
    raw: dict[str, RawTechnology] = {
        "MultiAssetTech": {"cats": [59], "js": {"__sharedmarker__": ""}}
    }
    js_texts = ["window.__sharedmarker__ = 1;", "elsewhere __sharedmarker__ referenced;"]
    detections = _detect_with_raw_dataset(
        monkeypatch, raw, {"59": "JS"}, "acme.io", _signal(), js_texts
    )
    assert len(detections) == 1
    assert detections[0].evidence == ["js: __sharedmarker__"]  # once, not per-asset
    assert detections[0].confidence == 50
