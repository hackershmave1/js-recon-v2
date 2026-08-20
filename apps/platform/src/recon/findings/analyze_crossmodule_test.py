"""Fast-lane tests for the cross-chunk wiring in analyze.py (Slice C).

Pure / monkeypatched — no stack. The real source-map recovery (sourcemapper
binary) is exercised in the integration lane and by the recon-range harness; here
`recover_sources` is stubbed so the export-index KEYING (the finding-4 risk) and
the per-unit resolution glue are covered without infra.
"""

from __future__ import annotations

from types import SimpleNamespace

from recon import storage
from recon.domain import AssetStatus
from recon.findings import analyze, sourcemapper
from recon.findings.sourcemapper import RecoveredFile, RecoveredSources

_INDEX = {"src/api/base.js": {"API_BASE": "https://api.acme.com", "ORDERS_PATH": "/api/v3/orders"}}


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
    # or nothing resolves (adversary finding 4).
    unit = 'import { API_BASE, ORDERS_PATH } from "./base.js"; fetch(API_BASE + ORDERS_PATH)'
    got = analyze._resolve_cross_module("src/api/orders.js", unit, _INDEX)
    assert got == {"API_BASE": "https://api.acme.com", "ORDERS_PATH": "/api/v3/orders"}


def test_resolve_cross_module_none_without_index_or_imports():
    unit = 'import { API_BASE } from "./base.js"; fetch(API_BASE)'
    assert analyze._resolve_cross_module("src/api/orders.js", unit, None) is None
    # a unit with no imports -> None so extract() stays on its unchanged per-file path
    assert analyze._resolve_cross_module("src/api/orders.js", 'fetch("/x")', _INDEX) is None


# --- build_export_index (keying + best-effort) -------------------------------- #

_RECOVERED = {
    b"entry": [
        RecoveredFile(
            path="src/api/base.js",
            content=b'export const API_BASE = "https://api.acme.com";'
            b'export const ORDERS_PATH = "/api/v3/orders";',
        ),
        RecoveredFile(path="src/main.js", content=b'import "./api/base.js";'),  # no exports
    ],
    b"orders": [
        RecoveredFile(
            path="src/api/orders.js",
            content=b'import { API_BASE } from "./base.js"; fetch(API_BASE);',  # imports only
        )
    ],
}


def _stub_recovery(monkeypatch, recovered: dict[bytes, list[RecoveredFile]]) -> None:
    # storage.get_blob(ref) returns the ref name as bytes so the recover stub can
    # dispatch per asset; recover_sources maps those bytes to synthetic files.
    monkeypatch.setattr(storage, "get_blob", lambda ref: ref.encode())
    monkeypatch.setattr(
        sourcemapper,
        "recover_sources",
        lambda map_bytes, **kw: RecoveredSources(files=recovered.get(map_bytes, []), status="ok"),
    )


def test_build_export_index_keys_by_recovered_path(monkeypatch):
    _stub_recovery(monkeypatch, _RECOVERED)
    rows = [_row(source_map_ref="entry"), _row(source_map_ref="orders")]
    index = analyze.build_export_index(rows)
    # base.js's exports indexed under its recovered f.path; the import-only orders.js
    # and export-less main.js contribute nothing.
    assert index == {
        "src/api/base.js": {"API_BASE": "https://api.acme.com", "ORDERS_PATH": "/api/v3/orders"}
    }


def test_build_export_index_end_to_end_key_match():
    # The keying is only useful if it actually resolves: feed the index this build
    # produced back through the per-unit glue for the orders unit.
    consts = analyze._resolve_cross_module(
        "src/api/orders.js",
        'import { API_BASE, ORDERS_PATH } from "./base.js"; fetch(API_BASE + ORDERS_PATH)',
        {"src/api/base.js": {"API_BASE": "https://api.acme.com", "ORDERS_PATH": "/api/v3/orders"}},
    )
    assert consts == {"API_BASE": "https://api.acme.com", "ORDERS_PATH": "/api/v3/orders"}


def test_build_export_index_skips_non_ok_and_missing_ref(monkeypatch):
    _stub_recovery(monkeypatch, _RECOVERED)
    rows = [
        _row(source_map_ref="entry", fetch_status=AssetStatus.FAILED.value),  # not OK -> skipped
        _row(source_map_ref="entry", input_ref=None),  # no input_ref -> skipped
    ]
    assert analyze.build_export_index(rows) == {}


def test_build_export_index_is_best_effort_on_recovery_error(monkeypatch):
    _stub_recovery(monkeypatch, _RECOVERED)

    def _boom(map_bytes, **kw):
        if map_bytes == b"bad":
            raise sourcemapper.engines.EngineError("bad map")
        return RecoveredSources(files=_RECOVERED.get(map_bytes, []), status="ok")

    monkeypatch.setattr(sourcemapper, "recover_sources", _boom)
    # the bad asset is swallowed; the good one still contributes its exports
    rows = [_row(source_map_ref="bad"), _row(source_map_ref="entry")]
    assert analyze.build_export_index(rows) == {
        "src/api/base.js": {"API_BASE": "https://api.acme.com", "ORDERS_PATH": "/api/v3/orders"}
    }


# --- no-map / minified-ESM path (2a) ------------------------------------------ #


def test_build_export_index_nomap_keys_by_url_path(monkeypatch):
    # No source_map_ref and no inline map -> the asset is a bundle unit, so its
    # exports are harvested straight from the minified source and keyed by the URL
    # path (the same key `_extract_endpoints` uses for a bundle unit).
    entry = b'const S="https://api.acme.com",T="/api/v3/orders";export{S as A,T as O};'
    monkeypatch.setattr(storage, "get_blob", lambda ref: entry)
    rows = [_row(source_map_ref=None, url="http://h:4175/assets/index-abc.js")]
    assert analyze.build_export_index(rows) == {
        "/assets/index-abc.js": {"A": "https://api.acme.com", "O": "/api/v3/orders"}
    }


def test_nomap_resolution_via_url_key():
    # An orders bundle unit resolves its imports against the URL-keyed index: the
    # specifier "./index-abc.js" resolves relative to the importer URL path.
    index = {"/assets/index-abc.js": {"A": "https://api.acme.com", "O": "/api/v3/orders"}}
    orders = 'import{A as a,O as o}from"./index-abc.js";fetch(a+o)'
    assert analyze._resolve_cross_module("/assets/orders-xyz.js", orders, index) == {
        "a": "https://api.acme.com",
        "o": "/api/v3/orders",
    }


def test_build_export_index_bad_capture_map_falls_back_to_url_key(monkeypatch):
    # A present-but-malformed inline/capture map raises EngineError; mirroring
    # `_analysis_units` (which falls back to bundle analysis for inline/capture), the
    # harvest falls through to the URL-key branch so importers still resolve (LOW-1).
    entry = b'const S="https://api.acme.com",T="/api/v3/orders";export{S as A,T as O};'
    monkeypatch.setattr(storage, "get_blob", lambda ref: entry)

    def _boom(map_bytes, **kw):
        raise sourcemapper.engines.EngineError("bad map")

    monkeypatch.setattr(sourcemapper, "recover_sources", _boom)
    rows = [_row(source_map_ref="cap", url="http://h/assets/index-abc.js")]
    assert analyze.build_export_index(rows, source_map_origin="capture") == {
        "/assets/index-abc.js": {"A": "https://api.acme.com", "O": "/api/v3/orders"}
    }
