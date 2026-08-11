"""Colocated tests for the no-map deobfuscation baseline (Phase 1).

Pure unit tests — reformat JS strings, assert the fail-soft contract. No infra,
so these run in the host lane (``-m "not integration"``) and cover the module for
``--cov-fail-under=55`` (``RECON_REQUIRE_ENGINES`` does not gate a pure-Python dep,
so the soft-fail branch is exercised explicitly here).
"""

from __future__ import annotations

import jsbeautifier

from recon.findings import deobfuscate


def test_minified_one_liner_beautifies_to_multiple_lines():
    minified = 'const a=fetch("/api/a");const b=fetch("/api/b");const c=fetch("/api/c");'
    assert len(minified.splitlines()) == 1

    out = deobfuscate.beautify(minified)

    assert out is not None
    assert len(out.splitlines()) > 1  # distinct lines to localize findings against


def test_beautify_is_deterministic():
    # Both callers (analyze + Sources) must re-derive BYTE-IDENTICAL text.
    source = 'axios.get("/api/orders",{params:{page:2}});$.post("/api/login",{user:1});'

    assert deobfuscate.beautify(source) == deobfuscate.beautify(source)


def test_input_over_cap_returns_none():
    # Over the input cap -> soft-fail to None so the caller keeps the raw bundle.
    oversized = "a;" * (deobfuscate._MAX_BEAUTIFY_BYTES // 2 + 1)
    assert len(oversized) > deobfuscate._MAX_BEAUTIFY_BYTES

    assert deobfuscate.beautify(oversized) is None


def test_beautifier_error_soft_fails_to_none(monkeypatch):
    # A pathological input that makes jsbeautifier raise must NOT propagate — the
    # caller falls back to the raw bundle unchanged.
    def boom(_source, _opts):
        raise ValueError("pathological input")

    monkeypatch.setattr(jsbeautifier, "beautify", boom)

    assert deobfuscate.beautify('fetch("/api/x");') is None
