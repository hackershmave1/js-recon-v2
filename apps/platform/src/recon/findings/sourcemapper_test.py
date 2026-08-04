"""Unit tests for the Sourcemapper adapter's pure parts (no binary needed).

The real ``sourcemapper`` binary is exercised only in a Docker smoke (it has no
binary release and needs Go to build, so it is absent on the host).
"""

from __future__ import annotations

import base64
import json
import os
import urllib.parse

import pytest

from recon.findings import engines, sourcemapper

_MAP = {"version": 3, "sources": ["app/src/api.js"], "mappings": "AAAA"}


def _inline_comment(payload: str) -> str:
    return f"console.log(1);\n//# sourceMappingURL={payload}\n"


def test_extract_inline_base64_map():
    raw = json.dumps(_MAP).encode("utf-8")
    b64 = base64.b64encode(raw).decode("ascii")
    js = _inline_comment(f"data:application/json;charset=utf-8;base64,{b64}")
    extracted = sourcemapper.extract_inline_map(js)
    assert extracted is not None
    assert json.loads(extracted)["sources"] == ["app/src/api.js"]


def test_extract_inline_percent_encoded_map():
    raw = json.dumps(_MAP)
    js = _inline_comment("data:application/json," + urllib.parse.quote(raw))
    extracted = sourcemapper.extract_inline_map(js)
    assert extracted is not None
    assert json.loads(extracted)["version"] == 3


def test_external_reference_is_none_deferred():
    # An external .map URL needs the (deferred) fetch stage — not handled here.
    assert sourcemapper.extract_inline_map(_inline_comment("app.js.map")) is None
    assert sourcemapper.extract_inline_map(_inline_comment("https://cdn.x/app.js.map")) is None


def test_no_sourcemap_comment_is_none():
    assert sourcemapper.extract_inline_map("const x = 1;\n") is None


def test_last_sourcemap_comment_wins():
    raw = json.dumps(_MAP).encode("utf-8")
    b64 = base64.b64encode(raw).decode("ascii")
    js = (
        _inline_comment("old.js.map")
        + _inline_comment(f"data:application/json;base64,{b64}")
    )
    extracted = sourcemapper.extract_inline_map(js)
    assert extracted is not None and json.loads(extracted)["sources"] == ["app/src/api.js"]


def test_legacy_at_comment_and_malformed_base64():
    raw = json.dumps(_MAP).encode("utf-8")
    b64 = base64.b64encode(raw).decode("ascii")
    assert sourcemapper.extract_inline_map(f"//@ sourceMappingURL=data:application/json;base64,{b64}") is not None
    # Malformed base64 payload -> None, never a crash.
    assert sourcemapper.extract_inline_map("//# sourceMappingURL=data:application/json;base64,!!!!") is None


def test_recover_sources_walks_output_tree(monkeypatch):
    # Stub the subprocess: emulate sourcemapper writing a recovered tree into the
    # -output dir, so recover_sources' walk + relpath mapping is exercised without
    # the real Go binary (which is Docker-only).
    def fake_run_engine(argv, **_kwargs):
        out_dir = argv[argv.index("-output") + 1]
        os.makedirs(os.path.join(out_dir, "app", "src"), exist_ok=True)
        with open(os.path.join(out_dir, "app", "src", "api.js"), "wb") as handle:
            handle.write(b'fetch("/api/x");')
        with open(os.path.join(out_dir, "index.js"), "wb") as handle:
            handle.write(b"// root")
        return engines.EngineResult(0, b"", b"")

    monkeypatch.setattr(sourcemapper.engines, "run_engine", fake_run_engine)
    result = sourcemapper.recover_sources(b'{"version":3}', bin_path="stub", origin="uploaded")

    assert result.status == "ok" and result.origin == "uploaded"
    assert {f.path for f in result.files} == {"app/src/api.js", "index.js"}
    api = next(f for f in result.files if f.path == "app/src/api.js")
    assert api.content == b'fetch("/api/x");'


def test_recover_sources_missing_binary_is_unavailable():
    result = sourcemapper.recover_sources(b'{"version":3}', bin_path="definitely-not-sourcemapper-xyz")
    assert result.status == "unavailable"
    assert result.files == []


def test_recover_sources_total_bytes_capped(monkeypatch):
    def fake_run_engine(argv, **_kwargs):
        out_dir = argv[argv.index("-output") + 1]
        with open(os.path.join(out_dir, "big.js"), "wb") as handle:
            handle.write(b"x" * 500)
        return engines.EngineResult(0, b"", b"")

    monkeypatch.setattr(sourcemapper.engines, "run_engine", fake_run_engine)
    # cap smaller than the recovered file -> it is dropped rather than read whole.
    result = sourcemapper.recover_sources(b"{}", bin_path="stub", max_recovered_bytes=10)
    assert result.files == []


# A golden source map with sourcesContent — the real binary reconstructs the two
# declared sources verbatim. Kept as the contract fixture (REQ-T4): regenerate the
# expectations from real output if the pinned Sourcemapper commit is bumped.
_GOLDEN_MAP = json.dumps(
    {
        "version": 3,
        "file": "bundle.js",
        "sources": ["src/app.js", "src/util.js"],
        "sourcesContent": ['fetch("/api/widgets/7");\n', "export const util = 1;\n"],
        "names": [],
        "mappings": "",
    }
).encode("utf-8")


@pytest.mark.integration
def test_recover_sources_real_binary_matches_golden_map(engines_required):
    # Contract test against the REAL sourcemapper binary (Docker/CI only — it has
    # no host build). Marked integration so it runs in the container job (where the
    # Go-built binary lives), not the no-infra host job. The golden map must
    # recover its two declared sources with
    # exact content; an upstream output-schema drift (a changed tree layout or a
    # hard-fail on this input) trips this, so a silent regression can't ship
    # (REQ-T4). Skips on a host without the Go-built binary, fails in CI.
    try:
        recovered = sourcemapper.recover_sources(_GOLDEN_MAP, origin="uploaded")
    except engines.EngineError as exc:  # pragma: no cover - only on drift
        pytest.fail(f"sourcemapper rejected the golden map (output-schema drift?): {exc}")
    if recovered.status == "unavailable":
        if engines_required:
            pytest.fail("sourcemapper binary required (RECON_REQUIRE_ENGINES) but unavailable")
        pytest.skip("sourcemapper binary not available (no host build)")
    by_path = {f.path: f.content for f in recovered.files}
    assert set(by_path) == {"src/app.js", "src/util.js"}
    assert by_path["src/app.js"] == b'fetch("/api/widgets/7");\n'
