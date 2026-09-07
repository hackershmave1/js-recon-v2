"""Hermetic unit tests for the client-side data-sink detector (D52).

Pure ``str`` in / ``list[DataSinkSighting]`` out — no DB, no engine, no marker, so it
runs in the fast lane. Exercises both sink families (postmessage + storage), boundary
guards (no false positives on similar-but-non-matching patterns), the per-blob cap,
snippet trimming, and line/offset correctness.
"""

from __future__ import annotations

import pytest

from recon.findings.data_sinks import find_data_sinks

# ---------------------------------------------------------------------------
# POSTMESSAGE_SINK positives
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "source",
    [
        "window.addEventListener('message', handler);",
        'window.addEventListener("message", handler);',
        "window.addEventListener('message',handler);",
        # Whitespace between ( and literal
        "window.addEventListener( 'message' , handleMessage);",
        # Mixed-case method (minifier artefact)
        "window.AddEventListener('message', cb);",
    ],
)
def test_postmessage_patterns_are_detected(source: str) -> None:
    sightings = find_data_sinks(source)
    pm = [s for s in sightings if s.sink_type == "postmessage_sink"]
    assert len(pm) == 1, f"expected 1 postmessage_sink in {source!r}, got {pm}"


def test_postmessage_sighting_has_correct_type_and_value() -> None:
    source = "window.addEventListener('message', onMsg);"
    (sighting,) = find_data_sinks(source)
    assert sighting.sink_type == "postmessage_sink"
    # value is the matched snippet (trimmed) — should start with 'addEventListener'
    assert "addEventListener" in sighting.value
    assert "'message'" in sighting.value


def test_postmessage_line_and_offset() -> None:
    source = "var x = 1;\nwindow.addEventListener('message', handler);\nvar y = 2;"
    sightings = find_data_sinks(source)
    pm = [s for s in sightings if s.sink_type == "postmessage_sink"]
    assert len(pm) == 1
    s = pm[0]
    assert s.line == 2
    # offset round-trips to the matched text
    assert source[s.offset_start : s.offset_end] == s.value[: s.offset_end - s.offset_start]


def test_postmessage_evidence_is_the_containing_line() -> None:
    source = "foo();\nwindow.addEventListener('message', handler);\nbar();"
    (s,) = [s for s in find_data_sinks(source) if s.sink_type == "postmessage_sink"]
    assert s.evidence == "window.addEventListener('message', handler);"


# ---------------------------------------------------------------------------
# POSTMESSAGE_SINK negatives
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "source",
    [
        # 'click' is not 'message'
        "window.addEventListener('click', handler);",
        # 'messages' is not 'message' (substring guard from word boundary in the pattern)
        "window.addEventListener('messages', handler);",
        # Not addEventListener at all
        "onmessage = function(e) {};",
        "self.onmessage = function(e) {};",
    ],
)
def test_postmessage_false_positives_are_not_matched(source: str) -> None:
    pm = [s for s in find_data_sinks(source) if s.sink_type == "postmessage_sink"]
    assert pm == [], f"unexpected postmessage_sink sighting in {source!r}"


# ---------------------------------------------------------------------------
# STORAGE_SINK positives
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "source",
    [
        "localStorage.setItem('key', value);",
        "localStorage.removeItem('key');",
        "localStorage.clear();",
        "sessionStorage.setItem('token', tok);",
        "sessionStorage.removeItem('token');",
        "sessionStorage.clear();",
        "document.cookie = 'session=abc; path=/';",
        # Whitespace around dot and =
        "document.cookie  =  'x=y';",
        "localStorage  .  setItem('k', 'v');",
    ],
)
def test_storage_patterns_are_detected(source: str) -> None:
    sightings = find_data_sinks(source)
    st = [s for s in sightings if s.sink_type == "storage_sink"]
    assert len(st) == 1, f"expected 1 storage_sink in {source!r}, got {st}"


def test_storage_sighting_has_correct_type() -> None:
    source = "localStorage.setItem('userId', id);"
    (sighting,) = find_data_sinks(source)
    assert sighting.sink_type == "storage_sink"
    assert "localStorage" in sighting.value


def test_cookie_assignment_is_storage_sink() -> None:
    source = "document.cookie = 'session=tok; SameSite=Strict';"
    (sighting,) = find_data_sinks(source)
    assert sighting.sink_type == "storage_sink"
    assert "cookie" in sighting.value


# ---------------------------------------------------------------------------
# STORAGE_SINK negatives
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "source",
    [
        # getItem is a READ, not a write — not a sink
        "localStorage.getItem('key');",
        "sessionStorage.getItem('token');",
        # Partial word — no word boundary before localStorage
        "xlocalStorage.setItem('k', 'v');",
        # document.cookie READ (no assignment)
        "var c = document.cookie;",
        "console.log(document.cookie);",
    ],
)
def test_storage_false_positives_are_not_matched(source: str) -> None:
    st = [s for s in find_data_sinks(source) if s.sink_type == "storage_sink"]
    assert st == [], f"unexpected storage_sink sighting in {source!r}"


# ---------------------------------------------------------------------------
# Both types in same source
# ---------------------------------------------------------------------------


def test_both_sink_types_coexist_in_same_source() -> None:
    source = (
        "window.addEventListener('message', handler);\n"
        "localStorage.setItem('k', v);\n"
        "document.cookie = 'x=1';"
    )
    sightings = find_data_sinks(source)
    pm = [s for s in sightings if s.sink_type == "postmessage_sink"]
    st = [s for s in sightings if s.sink_type == "storage_sink"]
    assert len(pm) == 1
    assert len(st) == 2  # setItem + cookie


def test_sightings_returned_in_source_order() -> None:
    source = (
        "localStorage.setItem('k', v);\n"
        "window.addEventListener('message', handler);\n"
        "document.cookie = 'x=1';"
    )
    sightings = find_data_sinks(source)
    offsets = [s.offset_start for s in sightings]
    assert offsets == sorted(offsets)


# ---------------------------------------------------------------------------
# Value trimming
# ---------------------------------------------------------------------------


def test_value_is_trimmed_to_120_chars() -> None:
    # Construct a line where the matched snippet is longer than 120 chars.
    # The actual match is "addEventListener('message'" — short, so pad by wrapping it.
    long_comment = "// " + "a" * 200 + "\n"
    source = long_comment + "window.addEventListener('message', handler);"
    sightings = find_data_sinks(source)
    pm = [s for s in sightings if s.sink_type == "postmessage_sink"]
    assert len(pm) == 1
    assert len(pm[0].value) <= 120


# ---------------------------------------------------------------------------
# Per-blob cap
# ---------------------------------------------------------------------------


def test_per_blob_cap_is_applied_per_sink_type() -> None:
    # 600 postMessage calls — capped at 500 (default)
    line = "window.addEventListener('message', h);\n"
    source = line * 600
    sightings = find_data_sinks(source)
    pm = [s for s in sightings if s.sink_type == "postmessage_sink"]
    assert len(pm) == 500


def test_cap_override_is_honoured() -> None:
    line = "localStorage.setItem('k', v);\n"
    source = line * 100
    sightings = find_data_sinks(source, cap=10)
    st = [s for s in sightings if s.sink_type == "storage_sink"]
    assert len(st) == 10


# ---------------------------------------------------------------------------
# Evidence snippet (minified-file guard)
# ---------------------------------------------------------------------------


def test_evidence_is_windowed_on_minified_single_line() -> None:
    """On a minified file (one very long line) evidence must be a ≤302-char snippet, not the full line."""
    # Simulate a minified file: 5000-char line with the sink buried in the middle.
    padding = "x" * 2000
    match_text = "window.addEventListener('message', h);"
    minified = padding + match_text + padding
    (s,) = [s for s in find_data_sinks(minified) if s.sink_type == "postmessage_sink"]
    # evidence must be capped; +2 for possible ellipsis chars
    assert len(s.evidence) <= 302, f"evidence too long: {len(s.evidence)} chars"
    # The matched snippet must appear in the evidence window
    assert "addEventListener" in s.evidence
    # Ellipsis markers present since we clipped both sides
    assert s.evidence.startswith("\u2026")
    assert s.evidence.endswith("\u2026")
