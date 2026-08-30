"""Fast-lane tests for D37-L2 slice 3: the streaming, on-disk recovered-source path.

``sourcemapper.iter_recovered_files`` is faked (no Go binary) so ``_analysis_units`` beautifies the
yielded originals to an on-disk tree; these pin the byte-exact write contract (M2), the cumulative-
write budget + honest partial (M2/R4), the 32 MiB -> 96 MiB cap lift (recovers the whole map), and
the per-file heartbeat (S4). The end-to-end reveal round-trip + 409-on-drift live in the integration
lane (``probe/reveal_recovered_test.py``); the byte-exact contract they rely on is pinned here.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

from recon.config import get_settings
from recon.findings import analyze, deobfuscate, sourcemapper

# An inline data: map ({"version":3}) so _analysis_units needs no blob store (fast lane): recovery is
# faked, so the map bytes are never actually parsed — only their presence routes to the recovered path.
_INLINE = "x\n//# sourceMappingURL=data:application/json;base64,eyJ2ZXJzaW9uIjozfQ=="


def _fake_iter(*files: tuple[str, bytes]):
    """A fake ``iter_recovered_files`` yielding fixed (rel_path, raw_bytes) originals."""

    def _iter(_map_path, **_kwargs):
        yield from files

    return _iter


def test_recovered_minified_beautified_to_disk_byte_exact(monkeypatch):
    # M2: a recovered MINIFIED original is beautified and written byte-exact — the SAME bytes
    # recon.probe.sources.recover_file_text reproduces at reveal, so an analyze-time secret offset
    # round-trips (409-on-drift never trips on a clean map).
    minified = b'const a=1,b=2;fetch("/api/x");' + b" /* pad */" * 80  # one long line -> minified
    monkeypatch.setattr(
        sourcemapper, "iter_recovered_files", _fake_iter(("app/vendor.js", minified))
    )
    with analyze._analysis_units(None, _INLINE) as units:
        assert not units.is_bundle and units.names == ["app/vendor.js"]
        expected = deobfuscate.beautify_if_minified(minified.decode("utf-8", "replace"))
        assert expected != minified.decode("utf-8")  # it really beautified (now multi-line)
        assert units.read_text("app/vendor.js") == expected  # read-back == the reveal text
        with open(os.path.join(units.tree_root, "app", "vendor.js"), "rb") as handle:
            assert handle.read() == expected.encode("utf-8")  # byte-exact (no added newline/BOM)


def test_multiline_original_passes_through_unbeautified(monkeypatch):
    # A genuinely multi-line original keeps its real line numbers (beautify_if_minified is a no-op),
    # so its findings land where the served source shows them.
    original = b'// real source\nexport const pay = () => fetch("/api/pay");\n'
    monkeypatch.setattr(sourcemapper, "iter_recovered_files", _fake_iter(("app/pay.js", original)))
    with analyze._analysis_units(None, _INLINE) as units:
        assert units.read_text("app/pay.js") == original.decode("utf-8")


def test_recovers_up_to_map_cap_not_the_32mib_output_cap(monkeypatch):
    # The 32 MiB in-RAM output cap is lifted: analyze recovers up to the MAP INPUT cap
    # (max_source_map_bytes, 96 MiB) so a big map's endpoints are recovered whole, not truncated.
    captured: dict[str, object] = {}

    def spy_iter(_map_path, **kwargs):
        captured.update(kwargs)
        yield "a.js", b'fetch("/x");'

    monkeypatch.setattr(sourcemapper, "iter_recovered_files", spy_iter)
    with analyze._analysis_units(None, _INLINE):
        pass
    settings = get_settings()
    assert captured["max_recovered_bytes"] == settings.max_source_map_bytes
    assert captured["max_recovered_bytes"] > settings.engine_max_output_bytes  # past the old cap


def test_write_budget_stops_whole_file_and_marks_partial(monkeypatch):
    # M2/R4: the cumulative-write budget (2x the map cap) stops at WHOLE-FILE granularity with an
    # honest partial — never a mid-file cut (which would desync reveal -> 409). Shrink the cap so a
    # few small files trip it.
    monkeypatch.setattr(analyze, "get_settings", lambda: SimpleNamespace(max_source_map_bytes=20))
    # budget = 2*20 = 40 bytes. Three 15-byte files: a (15) + b (30) fit; c (45) trips -> dropped.
    monkeypatch.setattr(
        sourcemapper,
        "iter_recovered_files",
        _fake_iter(("a.js", b"x" * 15), ("b.js", b"y" * 15), ("c.js", b"z" * 15)),
    )
    with analyze._analysis_units(None, _INLINE) as units:
        assert units.partial is True
        assert units.names == ["a.js", "b.js"]  # c.js dropped WHOLE, never truncated
        assert units.read_text("b.js") == "y" * 15
        assert not os.path.exists(os.path.join(units.tree_root, "c.js"))


def test_per_file_heartbeat_beats_before_each_recovered_file(monkeypatch):
    # S4: the beautify-to-disk loop beats before EACH file so a big map's per-file work can't
    # outlast the 30s stall window and let a peer reclaim the RUNNING job.
    beats = {"n": 0}

    def _heartbeat() -> None:
        beats["n"] += 1

    monkeypatch.setattr(
        sourcemapper,
        "iter_recovered_files",
        _fake_iter(("a.js", b"1"), ("b.js", b"2"), ("c.js", b"3")),
    )
    with analyze._analysis_units(None, _INLINE, heartbeat=_heartbeat):
        pass
    assert beats["n"] == 3  # one beat per recovered file


def test_no_recovered_files_falls_back_to_bundle(monkeypatch):
    # A map that recovers NOTHING (no sourcesContent) falls back to bundle analysis under the map's
    # origin — the on-disk tree is cleaned up (nothing to scan), and the bundle is analyzed instead.
    monkeypatch.setattr(sourcemapper, "iter_recovered_files", _fake_iter())  # yields nothing
    with analyze._analysis_units(None, _INLINE) as units:
        assert units.is_bundle
        assert units.source_map_status == "inline"
        assert units.tree_root is None
