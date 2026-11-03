"""Exact view locations: detector offsets -> (line, UTF-16 col) in the text the Sources tab shows."""

from recon.findings import deobfuscate
from recon.findings.located import LocatedText, ViewLoc


def _slice(text: str, loc: ViewLoc) -> str:
    """What the browser would highlight: rows split on "\\n", cols in UTF-16 code units."""
    rows = text.split("\n")
    out = []
    for line in range(loc.line, loc.end_line + 1):
        row = rows[line - 1].encode("utf-16-le")
        start = loc.col if line == loc.line else 0
        end = loc.end_col if line == loc.end_line else len(row) // 2
        out.append(row[start * 2 : end * 2].decode("utf-16-le"))
    return "\n".join(out)


def test_a_byte_span_maps_to_line_and_col_in_the_served_text():
    text = "const a = 1;\nfetch('/api/users');\n"
    start = text.index("'/api/users'")
    loc = LocatedText(text).from_bytes(start, start + len("'/api/users'"))
    assert loc == ViewLoc(line=2, col=6, end_line=2, end_col=18)


def test_columns_count_utf16_code_units_after_non_ascii_and_emoji():
    # "é" is 2 UTF-8 bytes / 1 UTF-16 unit; "😀" is 4 bytes / 2 units (a surrogate pair).
    text = "x('é😀', '/api/x')"
    data = text.encode("utf-8")
    start = data.index(b"'/api/x'")
    loc = LocatedText(text).from_bytes(start, start + len(b"'/api/x'"))
    assert loc is not None
    assert _slice(text, loc) == "'/api/x'"
    assert loc.col == len("x('é😀', ".encode("utf-16-le")) // 2


def test_a_byte_offset_inside_a_multibyte_character_has_no_location():
    text = "a = 'é';"
    inside = len(b"a = '") + 1  # the second byte of "é"
    assert LocatedText(text).from_bytes(inside, inside + 2) is None


def test_an_empty_or_out_of_range_span_has_no_location():
    located = LocatedText("abc")
    assert located.from_bytes(0, 0) is None  # a declared const's 0..0 "position"
    assert located.from_bytes(2, 9) is None
    assert located.from_chars(-1, 1) is None


def test_a_span_across_lines_ends_on_its_own_line():
    text = "fetch(\n  '/a',\n  {})"
    loc = LocatedText(text).from_chars(0, len(text))
    assert loc == ViewLoc(line=1, col=0, end_line=3, end_col=5)
    assert _slice(text, loc) == text


def test_a_raw_minified_match_lands_on_the_same_token_in_the_beautified_view():
    raw = 'var t=e.i(1);window.addEventListener("message",function(e){t(e.data)});document.cookie="a=1"'
    served = deobfuscate.beautify(raw)
    assert served is not None and served != raw
    located = LocatedText(served, raw=raw)
    for token in ('window.addEventListener("message"', 'document.cookie="a=1"'):
        start = raw.index(token)
        loc = located.from_raw_chars(start, start + len(token))
        assert loc is not None, token
        assert loc.line > 1  # not the minified line 1 any more
        # Beautify may add spaces inside the token (`document.cookie = "a=1"`); the
        # non-whitespace text is identical.
        assert "".join(_slice(served, loc).split()) == "".join(token.split())


def test_raw_byte_offsets_are_converted_before_aligning():
    raw = 'var s="é";var k="AKIAABCDEFGHIJKLMNOP";'
    served = deobfuscate.beautify(raw)
    assert served is not None
    data = raw.encode("utf-8")
    start = data.index(b'"AKIA')
    loc = LocatedText(served, raw=raw).from_raw_bytes(start, start + 22)
    assert loc is not None
    assert _slice(served, loc) == '"AKIAABCDEFGHIJKLMNOP"'


def test_without_a_separate_raw_text_raw_offsets_are_served_offsets():
    text = "a\nb = 'x';"
    located = LocatedText(text)
    assert located.from_raw_chars(2, 3) == located.from_chars(2, 3) == ViewLoc(2, 0, 2, 1)


def test_alignment_fails_closed_when_the_texts_differ_beyond_whitespace():
    located = LocatedText("foo(bar);", raw="foo(baz);")
    assert located.from_raw_chars(4, 7) is None


def test_non_ascii_whitespace_is_content_not_layout():
    # NBSP / U+2028 are never inserted or dropped by beautify, so they must not be skipped.
    raw = "a=1; b=2;"
    served = "a = 1;\n b = 2;"
    loc = LocatedText(served, raw=raw).from_raw_chars(5, 8)
    assert loc is not None
    assert _slice(served, loc) == "b = 2"
