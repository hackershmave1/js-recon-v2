"""Unit tests for the Sourcemapper adapter's pure parts (no binary needed).

The real ``sourcemapper`` binary is exercised only in a Docker smoke (it has no
binary release and needs Go to build, so it is absent on the host).
"""

from __future__ import annotations

import base64
import json
import os
import urllib.parse

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
