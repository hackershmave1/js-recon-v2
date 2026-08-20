"""Fast-lane tests for the cross-chunk wiring in analyze.py (Increments 2a + 2b).

Pure / monkeypatched — no stack. The real source-map recovery (sourcemapper binary)
is exercised in the integration lane and by the recon-range harness; here
`recover_sources` is stubbed (or the no-map path used) so the export-index KEYING,
the webpack build-scoping (finding F4), and the per-unit resolution glue are covered
without infra.
"""

from __future__ import annotations

from types import SimpleNamespace

from recon import storage
from recon.domain import AssetStatus
from recon.findings import analyze, sourcemapper
from recon.findings.analyze import CrossModuleIndex
from recon.findings.sourcemapper import RecoveredFile, RecoveredSources

_EXPORTS = {
    "src/api/base.js": {"API_BASE": "https://api.acme.com", "ORDERS_PATH": "/api/v3/orders"}
}


def _row(**kw: object) -> SimpleNamespace:
    base: dict[str, object] = {
        "fetch_status": AssetStatus.OK.value,
        "input_ref": "ir",
        "source_map_ref": "smr",
        "url": "u",
    }
    base.update(kw)
    return SimpleNamespace(**base)


# --- _resolve_cross_module (per-unit glue) ------------------------------------ #


def test_resolve_cross_module_uses_source_name_as_key():
    # The importer key MUST be the recovered `source_name` (== export-index key),
    # or nothing resolves (adversary finding 4 of Increment 1).
    unit = 'import { API_BASE, ORDERS_PATH } from "./base.js"; fetch(API_BASE + ORDERS_PATH)'
    consts, members = analyze._resolve_cross_module(
        "src/api/orders.js", unit, CrossModuleIndex(_EXPORTS)
    )
    assert consts == {"API_BASE": "https://api.acme.com", "ORDERS_PATH": "/api/v3/orders"}
    assert members is None


def test_resolve_cross_module_none_without_index_or_imports():
    unit = 'import { API_BASE } from "./base.js"; fetch(API_BASE)'
    assert analyze._resolve_cross_module("src/api/orders.js", unit, None) == (None, None)
    # a unit with no imports -> (None, None) so extract() stays on its per-file path
    assert analyze._resolve_cross_module("x", 'fetch("/x")', CrossModuleIndex(_EXPORTS)) == (
        None,
        None,
    )


# --- build_export_index: ESM keying (map + no-map) ---------------------------- #

_RECOVERED = {
    b"entry": [
        RecoveredFile(
            path="src/api/base.js",
            content=b'export const API_BASE = "https://api.acme.com";'
            b'export const ORDERS_PATH = "/api/v3/orders";',
        ),
        RecoveredFile(path="src/main.js", content=b'import "./api/base.js";'),  # no exports
    ],
}


def _stub_recovery(monkeypatch, recovered: dict[bytes, list[RecoveredFile]]) -> None:
    monkeypatch.setattr(storage, "get_blob", lambda ref: ref.encode())
    monkeypatch.setattr(
        sourcemapper,
        "recover_sources",
        lambda map_bytes, **kw: RecoveredSources(files=recovered.get(map_bytes, []), status="ok"),
    )


def test_build_export_index_keys_esm_by_recovered_path(monkeypatch):
    _stub_recovery(monkeypatch, _RECOVERED)
    index = analyze.build_export_index([_row(source_map_ref="entry")])
    assert index.exports == _EXPORTS
    assert index.webpack == {}


def test_build_export_index_skips_non_ok_and_missing_ref(monkeypatch):
    _stub_recovery(monkeypatch, _RECOVERED)
    rows = [
        _row(source_map_ref="entry", fetch_status=AssetStatus.FAILED.value),
        _row(source_map_ref="entry", input_ref=None),
    ]
    assert not analyze.build_export_index(rows)  # empty -> falsy CrossModuleIndex


def test_build_export_index_nomap_esm_keys_by_url_path(monkeypatch):
    entry = b'const S="https://api.acme.com",T="/api/v3/orders";export{S as A,T as O};'
    monkeypatch.setattr(storage, "get_blob", lambda ref: entry)
    index = analyze.build_export_index(
        [_row(source_map_ref=None, url="http://h:4175/assets/index-abc.js")]
    )
    assert index.exports == {
        "/assets/index-abc.js": {"A": "https://api.acme.com", "O": "/api/v3/orders"}
    }


def test_build_export_index_bad_capture_map_falls_back_to_url_key(monkeypatch):
    # A malformed inline/capture map raises EngineError; mirror _analysis_units and
    # fall through to the URL-key (minified-ESM) branch (LOW-1).
    entry = b'const S="https://api.acme.com",T="/api/v3/orders";export{S as A,T as O};'
    monkeypatch.setattr(storage, "get_blob", lambda ref: entry)

    def _boom(map_bytes, **kw):
        raise sourcemapper.engines.EngineError("bad map")

    monkeypatch.setattr(sourcemapper, "recover_sources", _boom)
    index = analyze.build_export_index(
        [_row(source_map_ref="cap", url="http://h/assets/index-abc.js")],
        source_map_origin="capture",
    )
    assert index.exports == {
        "/assets/index-abc.js": {"A": "https://api.acme.com", "O": "/api/v3/orders"}
    }


def test_nomap_esm_resolution_via_url_key():
    index = CrossModuleIndex(
        {"/assets/index-abc.js": {"A": "https://api.acme.com", "O": "/api/v3/orders"}}
    )
    orders = 'import{A as a,O as o}from"./index-abc.js";fetch(a+o)'
    consts, _members = analyze._resolve_cross_module("/assets/orders-xyz.js", orders, index)
    assert consts == {"a": "https://api.acme.com", "o": "/api/v3/orders"}


# --- build_export_index: webpack (2b) ----------------------------------------- #

# a no-map webpack ENTRY chunk that registers module 389 + carries its build's jsonp global
_WP_ENTRY_A = (
    b'var e={389(a,t,n){const o="https://api.acme.com",r="/api/v3/orders";n.d(t,["M",0,r,"t",0,o])}};'
    b"self.webpackChunkA=self.webpackChunkA||[];"
)
# a DIFFERENT build (B) that also registers a module 389 with a DIFFERENT value
_WP_ENTRY_B = b'var e={389(a,t,n){const o="https://evil.example";n.d(t,["t",0,o])}};self.webpackChunkB=self.webpackChunkB||[];'
# the consuming lazy chunk of build A: var r = require(389); fetch(r.t + r.M)
_WP_ORDERS_A = b"(self.webpackChunkA=self.webpackChunkA||[]).push([[9],{9(e,a,n){var r=n(389);fetch(r.t+r.M)}}]);"


def test_build_export_index_webpack_keyed_by_build_id(monkeypatch):
    monkeypatch.setattr(storage, "get_blob", lambda ref: ref)  # ref IS the source bytes
    index = analyze.build_export_index(
        [_row(source_map_ref=None, input_ref=_WP_ENTRY_A, url="http://h/main.js")]
    )
    assert index.webpack == {"A": {"389": {"M": "/api/v3/orders", "t": "https://api.acme.com"}}}


def test_webpack_resolution_member_fold():
    index = CrossModuleIndex(
        webpack={"A": {"389": {"M": "/api/v3/orders", "t": "https://api.acme.com"}}}
    )
    _consts, members = analyze._resolve_cross_module("/assets/9.js", _WP_ORDERS_A.decode(), index)
    assert members == {"r": {"M": "/api/v3/orders", "t": "https://api.acme.com"}}


def test_webpack_build_scoping_prevents_cross_build_wire(monkeypatch):
    # Adversary F4: two builds both ship module 389 with different values. The
    # consuming chunk is build A, so it must resolve 389 to A's value, NEVER B's.
    monkeypatch.setattr(storage, "get_blob", lambda ref: ref)
    index = analyze.build_export_index(
        [
            _row(source_map_ref=None, input_ref=_WP_ENTRY_A, url="http://h/a/main.js"),
            _row(source_map_ref=None, input_ref=_WP_ENTRY_B, url="http://h/b/main.js"),
        ]
    )
    # both builds indexed separately, id 389 not merged
    assert set(index.webpack) == {"A", "B"}
    assert index.webpack["A"]["389"]["t"] == "https://api.acme.com"
    assert index.webpack["B"]["389"]["t"] == "https://evil.example"
    # a build-A consumer resolves 389 to A's value only (no cross-wire to B)
    _consts, members = analyze._resolve_cross_module("/assets/9.js", _WP_ORDERS_A.decode(), index)
    assert members == {"r": {"M": "/api/v3/orders", "t": "https://api.acme.com"}}
