"""Unit tests for the D52 bounded source grep. list_sources/get_source_content hit DB+storage,
so they're monkeypatched — this covers the search LOGIC (matching, caps, 404/empty) on the host
lane; the end-to-end read is exercised by the integration suite."""

from __future__ import annotations

import pytest

from recon.probe import sources
from recon.probe.sources import SourceContent, SourceFile


def _wire(monkeypatch, files, contents):
    monkeypatch.setattr(sources, "list_sources", lambda _t, _r: files)

    def _content(_t, _r, path, asset_url=None):
        text = contents.get(path)
        return (
            None
            if text is None
            else SourceContent(path=path, content=text, truncated=False, formatted=False)
        )

    monkeypatch.setattr(sources, "get_source_content", _content)


def _f(path):
    return SourceFile(path=path, kind="asset", fetch_status="ok")


def test_matches_case_insensitive_with_line_and_snippet(monkeypatch):
    _wire(
        monkeypatch,
        [_f("a.js"), _f("b.js")],
        {"a.js": "const API_TOKEN = 'x';\nnothing here", "b.js": "unrelated"},
    )
    out = sources.search_sources("t", "r", "token")
    assert out is not None and len(out) == 1
    assert out[0].path == "a.js"
    assert out[0].line == 1
    assert "API_TOKEN" in out[0].snippet


def test_short_query_returns_empty_not_a_scan(monkeypatch):
    _wire(monkeypatch, [_f("a.js")], {"a.js": "aaaa"})
    assert sources.search_sources("t", "r", "a") == []


def test_absent_run_returns_none(monkeypatch):
    monkeypatch.setattr(sources, "list_sources", lambda _t, _r: None)
    assert sources.search_sources("t", "r", "token") is None


def test_per_file_match_cap(monkeypatch):
    _wire(monkeypatch, [_f("a.js")], {"a.js": "\n".join(["match"] * 100)})
    out = sources.search_sources("t", "r", "match")
    assert out is not None and len(out) == sources._SEARCH_MAX_PER_FILE


def test_total_match_cap_across_files(monkeypatch):
    files = [_f(f"f{i}.js") for i in range(50)]
    contents = {f"f{i}.js": "\n".join(["hit"] * 10) for i in range(50)}  # 50*10 = 500 potential
    _wire(monkeypatch, files, contents)
    out = sources.search_sources("t", "r", "hit", max_matches=25)
    assert out is not None and len(out) == 25


def test_missing_content_is_skipped(monkeypatch):
    _wire(monkeypatch, [_f("gone.js"), _f("here.js")], {"here.js": "found it"})  # gone.js -> None
    out = sources.search_sources("t", "r", "found")
    assert out is not None and [m.path for m in out] == ["here.js"]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
