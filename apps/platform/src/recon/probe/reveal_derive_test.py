"""Hermetic tests for the secret-reveal kernel (``_derive`` + ``_reveal_candidates``).

The fail-closed integrity re-check is the security invariant: a reveal must refuse
(``denial="integrity"``) whenever the sliced bytes no longer hash to the finding
identity, and it must never reveal without a byte location or a resolvable blob. Only
the blob read touches infra — it's faked here (mirrors ``fetch/egress_test.py`` stubbing
``socket.getaddrinfo``), so the whole decision runs in the fast lane.
"""

from __future__ import annotations

import types

from botocore.exceptions import ClientError

from recon.findings import normalize
from recon.probe import reveal


def _target(**over) -> reveal._Target:
    base = {
        "input_ref": "t/r/raw_js/deadbeef",
        "rule": "stripe",
        "value": "unset",
        "offset_start": 0,
        "offset_end": 4,
        "source_path": "app.js",
        "line": 1,
    }
    base.update(over)
    return reveal._Target(**base)


def _fake_blob(monkeypatch, *, data: bytes | None = None, exc: Exception | None = None) -> None:
    def _get_blob(_key):
        if exc is not None:
            raise exc
        return data

    monkeypatch.setattr(reveal.storage, "get_blob", _get_blob)


def test_no_offsets_denies_before_reading_the_blob(monkeypatch):
    reads = {"n": 0}
    monkeypatch.setattr(
        reveal.storage, "get_blob", lambda _k: reads.__setitem__("n", reads["n"] + 1) or b""
    )
    outcome = reveal._derive(_target(offset_start=None))
    assert outcome.revealed is False
    assert outcome.denial == "no_offsets"
    assert reads["n"] == 0  # un-revealable: never touches storage


def test_missing_input_ref_denies_source_gone(monkeypatch):
    _fake_blob(monkeypatch, data=b"whatever")
    assert reveal._derive(_target(input_ref=None)).denial == "source_gone"


def test_blob_client_error_denies_source_gone(monkeypatch):
    _fake_blob(monkeypatch, exc=ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject"))
    outcome = reveal._derive(_target())
    assert outcome.revealed is False
    assert outcome.denial == "source_gone"


def test_correct_slice_reveals_the_plaintext(monkeypatch):
    token = "sk_live_ABCDEF0123456789"
    blob = b"const key = '" + token.encode() + b"';"
    start = blob.index(token.encode())
    end = start + len(token)
    value = normalize.normalize_secret_value(token, "stripe")
    _fake_blob(monkeypatch, data=blob)
    outcome = reveal._derive(_target(value=value, offset_start=start, offset_end=end))
    assert outcome.revealed is True
    assert outcome.value == token
    assert outcome.denial is None


def test_hash_drift_fails_closed_with_integrity(monkeypatch):
    # The bytes at the recorded offsets no longer hash to the finding value (the
    # source changed under the same offsets) -> refuse; never reveal on drift.
    _fake_blob(monkeypatch, data=b"const x = 'changed';")
    outcome = reveal._derive(_target(value="stripe:0000000000000000", offset_start=0, offset_end=5))
    assert outcome.revealed is False
    assert outcome.denial == "integrity"
    assert outcome.value is None


def test_offsets_are_sliced_in_the_utf8_replace_space(monkeypatch):
    # analyze decodes utf-8/replace before recording byte offsets; a stray non-utf8
    # byte must not shift where the token is sliced. _derive reproduces that space,
    # so offsets computed in it slice the token cleanly.
    token = "TOKEN123"
    raw = b"\xffx=" + token.encode() + b";"
    space = raw.decode("utf-8", "replace").encode("utf-8")
    start = space.index(token.encode())
    end = start + len(token)
    value = normalize.normalize_secret_value(token, "generic")
    _fake_blob(monkeypatch, data=raw)
    outcome = reveal._derive(
        _target(value=value, rule="generic", offset_start=start, offset_end=end)
    )
    assert outcome.revealed is True
    assert outcome.value == token


def _occ(offset_start, offset_end, source_path, occurrence_hash):
    return types.SimpleNamespace(
        offset_start=offset_start,
        offset_end=offset_end,
        source_path=source_path,
        occurrence_hash=occurrence_hash,
    )


def test_reveal_candidates_drops_occurrences_without_full_offsets():
    occs = [
        _occ(None, None, "a.js", "h1"),  # no offsets -> dropped
        _occ(0, 4, "a.js", "h2"),
        _occ(5, None, "a.js", "h3"),  # partial offsets -> dropped
    ]
    assert [o.occurrence_hash for o in reveal._reveal_candidates(occs)] == ["h2"]


def test_reveal_candidates_sort_is_deterministic():
    # Ordered by (source_path, offset_start, occurrence_hash) — the same order the
    # findings query displays, so the reveal source pick is stable.
    occs = [
        _occ(10, 12, "b.js", "h4"),
        _occ(0, 2, "b.js", "h3"),
        _occ(0, 2, "a.js", "h2"),
        _occ(0, 2, "a.js", "h1"),
    ]
    assert [o.occurrence_hash for o in reveal._reveal_candidates(occs)] == ["h1", "h2", "h3", "h4"]
