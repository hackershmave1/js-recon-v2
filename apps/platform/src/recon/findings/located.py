"""Exact view locations: where a finding sits in the text the Sources tab SHOWS.

Detectors report positions in different spaces: tree-sitter gives UTF-8 byte offsets into the
unit it parsed, Kingfisher secrets are located by byte offset in the raw blob, and the
internal-IP / data-sink regexes give character offsets into the RAW minified bundle. The viewer
shows the beautified text, splits rows on ``"\\n"`` and indexes columns in UTF-16 code units (JS
strings). :class:`LocatedText` translates every one of those into a :class:`ViewLoc` in that
served text, or ``None`` when it cannot do so exactly — a finding with no location is honest, a
highlight on the wrong token is not.

Raw -> served alignment: beautify only adds or removes layout whitespace, so the sequence of the
remaining characters is identical in both texts (checked against every chunk of a real capture). A
raw position maps to the served position of the same non-layout character. Layout is ASCII
whitespace plus the two characters jsbeautifier 2.0.3 drops between tokens — a leading BOM
(U+FEFF) and NBSP (U+00A0); both are counted as layout on BOTH sides, so one kept inside a string
literal still aligns. (A U+2028 makes jsbeautifier rewrite string contents; that bundle then fails
closed below.)
Fail-closed twice: the whole texts must agree beyond layout, and each mapped token must too.

Positions here are DISPLAY data only. The stored ``line``/``col``/``offset_*`` of an occurrence
are part of its identity hash and stay exactly as the detectors report them (``store.Occurrence``).
"""

from __future__ import annotations

import re
from array import array
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from functools import cached_property

from recon.findings import deobfuscate

_LAYOUT_WS = re.compile(r"[ \t\n\r\f\v\u00a0\ufeff]+")
_NON_ASCII = re.compile(r"[^\x00-\x7f]")


@dataclass(frozen=True)
class ViewLoc:
    """A range in the served text: 1-based ``line`` counting ``"\\n"`` only, 0-based ``col`` in
    UTF-16 code units, end exclusive."""

    line: int
    col: int
    end_line: int
    end_col: int


class _TextIndex:
    """Sparse lookup tables over one text — sized by newlines / non-ASCII characters / whitespace
    runs, never one entry per character, so a 10 MiB single-line raw bundle stays cheap."""

    def __init__(self, text: str) -> None:
        self.text = text

    @cached_property
    def _newlines(self) -> array[int]:
        return array("q", (m.start() for m in re.finditer("\n", self.text)))

    @cached_property
    def _non_ascii(self) -> tuple[array[int], array[int], array[int], array[int]]:
        """(char positions, their byte positions, extra UTF-8 bytes up to and including each,
        astral char positions) of the non-ASCII characters."""
        chars: array[int] = array("q")
        byte_starts: array[int] = array("q")
        extra_upto: array[int] = array("q")
        astral: array[int] = array("q")
        extra = 0
        for match in _NON_ASCII.finditer(self.text):
            pos = match.start()
            code = ord(match.group())
            chars.append(pos)
            byte_starts.append(pos + extra)
            extra += 1 if code < 0x800 else 2 if code < 0x10000 else 3
            extra_upto.append(extra)
            if code > 0xFFFF:
                astral.append(pos)
        return chars, byte_starts, extra_upto, astral

    @cached_property
    def _layout(self) -> tuple[array[int], array[int], array[int], array[int]]:
        """(start, end, layout chars before, content chars before) of each whitespace run."""
        starts: array[int] = array("q")
        ends: array[int] = array("q")
        ws_before: array[int] = array("q")
        content_before: array[int] = array("q")
        ws = 0
        for match in _LAYOUT_WS.finditer(self.text):
            starts.append(match.start())
            ends.append(match.end())
            ws_before.append(ws)
            content_before.append(match.start() - ws)
            ws += match.end() - match.start()
        return starts, ends, ws_before, content_before

    def char_of_byte(self, offset: int) -> int | None:
        if self.text.isascii():
            return offset if 0 <= offset <= len(self.text) else None
        chars, byte_starts, extra_upto, _ = self._non_ascii
        before = bisect_left(byte_starts, offset)  # non-ASCII chars starting before `offset`
        char = offset - (extra_upto[before - 1] if before else 0)
        if not 0 <= char <= len(self.text):
            return None
        # Inside a multi-byte character -> the round trip back to bytes disagrees.
        return char if self._byte_of_char(char) == offset else None

    def _byte_of_char(self, char: int) -> int:
        chars, _, extra_upto, _ = self._non_ascii
        before = bisect_left(chars, char)
        return char + (extra_upto[before - 1] if before else 0)

    def view_point(self, char: int) -> tuple[int, int]:
        """(1-based line, 0-based UTF-16 column) of a char offset."""
        line_index = bisect_left(self._newlines, char)
        line_start = self._newlines[line_index - 1] + 1 if line_index else 0
        col = char - line_start
        if not self.text.isascii():
            astral = self._non_ascii[3]
            col += bisect_left(astral, char) - bisect_left(astral, line_start)
        return line_index + 1, col

    def content_count(self) -> int:
        starts, ends, ws_before, _ = self._layout
        ws_total = ws_before[-1] + ends[-1] - starts[-1] if starts else 0
        return len(self.text) - ws_total

    def content_before(self, char: int) -> int:
        """How many non-layout characters precede ``char``."""
        starts, ends, ws_before, _ = self._layout
        run = bisect_right(starts, char) - 1
        if run < 0:
            return char
        return char - ws_before[run] - (min(char, ends[run]) - starts[run])

    def char_of_content(self, index: int) -> int:
        """Char offset of the ``index``-th (0-based) non-layout character."""
        starts, ends, ws_before, content_before = self._layout
        run = bisect_right(content_before, index) - 1
        if run < 0:
            return index
        return index + ws_before[run] + ends[run] - starts[run]


class LocatedText:
    """The served text of one source unit, plus the raw text its raw-lane detectors scanned when
    the two differ (a beautified bundle). ``raw=None`` means the detectors scanned the served text
    itself (a recovered original, or a bundle served raw over the beautify cap)."""

    def __init__(self, served: str, raw: str | None = None) -> None:
        self.served = served
        self._served = _TextIndex(served)
        self._raw = _TextIndex(raw) if raw is not None and raw != served else None

    @classmethod
    def for_bundle(cls, raw: str) -> LocatedText:
        """A no-map bundle as the Sources tab serves it: beautified (the ONE beautify analyze
        runs for it), or raw when over the cap / on failure."""
        beautified = deobfuscate.beautify(raw)
        return cls(beautified, raw=raw) if beautified is not None else cls(raw)

    def from_chars(self, start: int, end: int) -> ViewLoc | None:
        """A ``[start, end)`` char span in the served text."""
        if not 0 <= start < end <= len(self.served):
            return None
        line, col = self._served.view_point(start)
        end_line, end_col = self._served.view_point(end)
        return ViewLoc(line, col, end_line, end_col)

    def from_bytes(self, start: int | None, end: int | None) -> ViewLoc | None:
        """A ``[start, end)`` UTF-8 byte span in the served text (tree-sitter, recovered secrets)."""
        if start is None or end is None or start >= end:
            return None
        char_start = self._served.char_of_byte(start)
        char_end = self._served.char_of_byte(end)
        if char_start is None or char_end is None:
            return None
        return self.from_chars(char_start, char_end)

    def from_raw_chars(self, start: int, end: int) -> ViewLoc | None:
        """A ``[start, end)`` char span in the raw text (internal-IP / data-sink regexes)."""
        if self._raw is None:
            return self.from_chars(start, end)
        raw = self._raw
        if not 0 <= start < end <= len(raw.text) or not self._aligned:
            return None
        first = raw.content_before(start)
        after = raw.content_before(end)
        if after <= first:
            return None  # the span is all layout whitespace
        served_start = self._served.char_of_content(first)
        served_end = self._served.char_of_content(after - 1) + 1
        token = _LAYOUT_WS.sub("", raw.text[start:end])
        if _LAYOUT_WS.sub("", self.served[served_start:served_end]) != token:
            return None
        return self.from_chars(served_start, served_end)

    def from_raw_bytes(self, start: int, end: int) -> ViewLoc | None:
        """A ``[start, end)`` UTF-8 byte span in the raw text (bundle secrets)."""
        raw = self._raw or self._served
        if start >= end:
            return None
        char_start = raw.char_of_byte(start)
        char_end = raw.char_of_byte(end)
        if char_start is None or char_end is None:
            return None
        return self.from_raw_chars(char_start, char_end)

    @cached_property
    def _aligned(self) -> bool:
        """Both texts have the same non-layout characters (counted, then spot-checked by each
        token) — false if beautify ever did more than move whitespace."""
        assert self._raw is not None
        return self._raw.content_count() == self._served.content_count()
