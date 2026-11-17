"""Unit tests for the fingerprint pass's per-host JS budget.

Pure: monkeypatches the two module deps (``run_assets.list_for_run`` and
``storage.get_blob``) so the budget logic is asserted without PG/S3. The pass's
end-to-end behavior is covered by the integration-marked ``analyze_technologies_test``.
"""

from __future__ import annotations

import pytest

from recon.findings import techdetect_pass as tp


class _Asset:
    def __init__(self, url: str, input_ref: str | None) -> None:
        self.url = url
        self.input_ref = input_ref


def _wire(monkeypatch: pytest.MonkeyPatch, assets: list[_Asset], blobs: dict[str, bytes]) -> None:
    monkeypatch.setattr(tp.run_assets, "list_for_run", lambda tenant_id, run_id: assets)
    monkeypatch.setattr(tp.storage, "get_blob", lambda ref: blobs[ref])


def test_js_texts_by_host_hard_caps_total_bytes_per_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A straddling asset must not push the per-host total past the cap. With a cap of
    # 100, asset A (80B) fills 80 and asset B is truncated to the remaining 20; C is
    # then skipped without being read. Total is exactly 100 — never ~2x, which the
    # earlier "count full blob length, append a per-asset slice" form allowed.
    monkeypatch.setattr(tp, "_MAX_JS_BYTES_PER_HOST", 100)
    assets = [
        _Asset("https://acme.io/a.js", "ref-a"),
        _Asset("https://acme.io/b.js", "ref-b"),
        _Asset("https://acme.io/c.js", "ref-c"),
    ]
    blobs = {"ref-a": b"a" * 80, "ref-b": b"b" * 80}  # ref-c absent: a read would KeyError
    _wire(monkeypatch, assets, blobs)

    out = tp._js_texts_by_host("t", "r", hosts={"acme.io"})

    assert out["acme.io"] == ["a" * 80, "b" * 20]
    assert sum(len(chunk) for chunk in out["acme.io"]) == 100


def test_js_texts_by_host_skips_off_host_and_unfetched_assets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assets = [
        _Asset("https://acme.io/app.js", "ref-a"),
        _Asset("https://other.io/x.js", "ref-b"),  # host not in the requested set
        _Asset("https://acme.io/nofetch.js", None),  # never fetched (no stored blob)
    ]
    blobs = {"ref-a": b"console.log(1)"}  # only the in-scope asset is ever read
    _wire(monkeypatch, assets, blobs)

    out = tp._js_texts_by_host("t", "r", hosts={"acme.io"})

    assert out == {"acme.io": ["console.log(1)"]}
