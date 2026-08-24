"""Hermetic tests for the bounded source-content builder (`_content_from_text`).

`_content_from_text` enforces the bounded-response invariant: the (already-decoded)
text is capped in BYTE space — encoded to UTF-8, sliced to a byte cap, decoded back —
so both the returned string and its byte backing stay bounded, and a `truncated` flag
tells the caller the tail was dropped. Callers decode the raw blob with
`errors="replace"` before handing text in, so non-UTF-8 input can never crash here.
"""

from __future__ import annotations

from recon.probe import sources


def test_small_content_is_not_truncated_and_round_trips():
    out = sources._content_from_text("app.js", "console.log(1)")
    assert out.path == "app.js"
    assert out.content == "console.log(1)"
    assert out.truncated is False


def test_over_cap_is_truncated_and_bounded(monkeypatch):
    monkeypatch.setattr(sources, "_MAX_CONTENT_BYTES", 8)
    out = sources._content_from_text("big.js", "abcdefghIJKL")  # 12 bytes, cap 8
    assert out.truncated is True
    assert out.content == "abcdefgh"  # only the first 8 bytes survive
    assert len(out.content.encode("utf-8")) <= 8


def test_exactly_at_cap_is_not_truncated(monkeypatch):
    # `len(encoded) > cap` is strict: an exactly-cap-sized body is kept whole.
    monkeypatch.setattr(sources, "_MAX_CONTENT_BYTES", 8)
    out = sources._content_from_text("edge.js", "abcdefgh")
    assert out.truncated is False
    assert out.content == "abcdefgh"


def test_bytes_are_capped_before_decode(monkeypatch):
    # Cap falls inside a multi-byte utf-8 char: capping in BYTE space (then
    # decode-replace) is what keeps the string bounded — capping by character count
    # would let a single char's bytes straddle the cap.
    monkeypatch.setattr(sources, "_MAX_CONTENT_BYTES", 2)
    out = sources._content_from_text("uni.js", "aé")  # encodes to b"a\xc3\xa9" (3 bytes)
    assert out.truncated is True
    assert out.content == "a�"  # 'a' + replacement for the split byte


def test_non_utf8_input_decoded_with_replacement_round_trips():
    # Callers decode the raw blob with errors="replace" before building content, so
    # invalid bytes become the replacement char and never crash the bounded builder.
    text = b"ok\xff\xfe".decode("utf-8", "replace")
    out = sources._content_from_text("bin.js", text)
    assert out.truncated is False
    assert out.content.startswith("ok")


def test_formatted_flag_defaults_false_and_passes_through():
    # D35: `formatted` records whether the SERVER already beautified the text. It defaults
    # False (a raw-served bundle, which the client formats) and passes through unchanged, so
    # the client decides whether to re-format from an authoritative flag, not a content guess.
    assert sources._content_from_text("raw.js", "x").formatted is False
    assert sources._content_from_text("fmt.js", "x", formatted=True).formatted is True
