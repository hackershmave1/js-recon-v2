"""Hermetic tests for the bounded source-content builder (`_as_content`).

`_as_content` is pure and enforces the bounded-response invariant: the raw bytes are
sliced to a byte cap *before* decoding, so both the returned string and its byte
backing stay bounded, and a `truncated` flag tells the caller the tail was dropped.
"""

from __future__ import annotations

from recon.probe import sources


def test_small_content_is_not_truncated_and_round_trips():
    out = sources._as_content("app.js", b"console.log(1)")
    assert out.path == "app.js"
    assert out.content == "console.log(1)"
    assert out.truncated is False


def test_over_cap_is_truncated_and_bounded(monkeypatch):
    monkeypatch.setattr(sources, "_MAX_CONTENT_BYTES", 8)
    out = sources._as_content("big.js", b"abcdefghIJKL")  # 12 bytes, cap 8
    assert out.truncated is True
    assert out.content == "abcdefgh"  # only the first 8 bytes survive
    assert len(out.content.encode("utf-8")) <= 8


def test_exactly_at_cap_is_not_truncated(monkeypatch):
    # `len(raw) > cap` is strict: an exactly-cap-sized body is kept whole.
    monkeypatch.setattr(sources, "_MAX_CONTENT_BYTES", 8)
    out = sources._as_content("edge.js", b"abcdefgh")
    assert out.truncated is False
    assert out.content == "abcdefgh"


def test_bytes_are_sliced_before_decode(monkeypatch):
    # Cap falls inside a multi-byte utf-8 char: slicing in BYTE space (then
    # decode-replace) is what keeps the string bounded — decoding first would let a
    # single char straddle the cap.
    monkeypatch.setattr(sources, "_MAX_CONTENT_BYTES", 2)
    out = sources._as_content("uni.js", "aé".encode())  # b"a\xc3\xa9" (3 bytes)
    assert out.truncated is True
    assert out.content == "a�"  # 'a' + replacement for the split byte


def test_non_utf8_bytes_decode_with_replacement():
    out = sources._as_content("bin.js", b"ok\xff\xfe")
    assert out.truncated is False
    assert out.content.startswith("ok")  # invalid bytes replaced, no crash
