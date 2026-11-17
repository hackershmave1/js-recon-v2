"""D64 secret fence for code windows: mask secret ranges AND every other copy of a secret's value.

Masking only the recorded sightings is not enough: Kingfisher reports a match where its rule's
context matches, so the same token pasted into a ``fetch`` header elsewhere has no sighting and
would reach the third-party LLM in clear. So each located sighting's plaintext is read from the
served text (and checked against the finding's hash by the caller) and every copy is masked.
"""

from __future__ import annotations

from collections.abc import Iterable

REDACTED = "[REDACTED SECRET]"


def py_index(text: str, utf16_col: int) -> int:
    """A UTF-16 column (what the viewer and ``view_col`` count) as a Python string index."""
    if text.isascii():
        return min(max(utf16_col, 0), len(text))
    units = 0
    for index, char in enumerate(text):
        if units >= utf16_col:
            return index
        units += 2 if ord(char) > 0xFFFF else 1
    return len(text)


def mask_window(
    window: dict[int, str],
    ranges: Iterable[tuple[int, int, int, int]],
    values: Iterable[str],
    target_line: int,
    target_col: int,
) -> tuple[dict[int, str], int, int]:
    """Mask ``ranges`` ((line, py_col, end_line, py_end_col), clamped to the window) and every copy
    of ``values`` in ``window``. Line count is preserved (a masked newline is kept), so line numbers
    stay true. Returns the masked window, the target column after masking and the masks applied."""
    order = sorted(window)
    lines = [window[n] for n in order]
    joined = "\n".join(lines)
    starts = _line_starts(lines)
    first, last = order[0], order[-1]
    found: list[tuple[int, int]] = []
    for line, col, end_line, end_col in ranges:
        if end_line < first or line > last:
            continue
        start = starts[line - first] + col if line >= first else 0
        end = starts[end_line - first] + end_col if end_line <= last else len(joined)
        if end > start:
            found.append((start, end))
    for value in {v for v in values if v}:
        at = joined.find(value)
        while at != -1:
            found.append((at, at + len(value)))
            at = joined.find(value, at + len(value))
    if not found:
        return window, target_col, 0

    target = starts[target_line - first] + target_col
    merged = _merge(found)
    for start, end in reversed(merged):
        replacement = REDACTED + "\n" * joined.count("\n", start, end)
        joined = joined[:start] + replacement + joined[end:]
        if end <= target:
            target += len(replacement) - (end - start)
        elif start < target:
            target = start
    masked = joined.split("\n")
    new_target_col = target - _line_starts(masked)[target_line - first]
    return dict(zip(order, masked, strict=True)), max(new_target_col, 0), len(merged)


def _line_starts(lines: list[str]) -> list[int]:
    starts, offset = [], 0
    for text in lines:
        starts.append(offset)
        offset += len(text) + 1
    return starts


def _merge(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged
