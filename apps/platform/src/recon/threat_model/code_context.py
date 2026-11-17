"""D64: bounded call-site code windows for the threat model prompt.

The model used to see endpoint URLs only, so it could only guess ("IDOR on parameterised routes").
Here each endpoint's call site becomes a short window of the code the Sources tab shows, so it can
see the method, the headers and the parameters that are (or are not) sent.

Pure: the caller supplies the call sites, the secret spans and a ``load_text`` for a file's served
text (``code_sites``). Everything is bounded: lines per window, characters per line and per window,
windows per prompt, files read, and one total character budget.

Secret fence (REQ-S2) — the LLM provider is a third party, so this fails closed:
1. Every file holding a secret is read first and each sighting's plaintext is taken from it (and
   checked against the finding's hash via ``verify_secret``). Each window masks those sightings AND
   every copy of every secret value, in any file (``secret_mask``).
2. If any secret cannot be placed and read back exactly (no exact location, an unreadable file, a
   hash mismatch, more secret files than ``max_files``), its value is unknown and could sit in any
   file, so the section is left out entirely.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace

from recon.threat_model import secret_mask
from recon.threat_model.code_types import (
    CONTROL,
    CallSite,
    CodeBudget,
    CodeContextStats,
    FileRef,
    SecretSpan,
)

_BACKTICKS = re.compile(r"`+")
_HEADER_RESERVE = 200  # the section heading and the "omitted" note come out of the budget too


@dataclass
class _Snippet:
    file: FileRef
    first: int
    last: int
    body: str
    visible: dict[int, tuple[int, int]]  # clipped lines: the shown [start, end) of the line
    labels: list[tuple[int, str]] = field(default_factory=list)  # (line, endpoint label)


class _Fence(Exception):
    """A secret we cannot mask everywhere: the code section is left out."""


def render_code_context(
    sites: Sequence[CallSite],
    secrets: Sequence[SecretSpan],
    load_text: Callable[[FileRef], str | None],
    budget: CodeBudget,
    verify_secret: Callable[[SecretSpan, str], bool] | None = None,
) -> tuple[list[str], CodeContextStats]:
    """The ``## Code Context`` markdown lines (empty when no window could be cut) and the stats."""
    stats = CodeContextStats()
    files: dict[FileRef, list[str] | None] = {}
    try:
        ranges, values = _read_secrets(secrets, load_text, budget, verify_secret, files, stats)
    except _Fence:
        return [], stats

    snippets: list[_Snippet] = []
    limit = budget.total_chars - _HEADER_RESERVE
    for site in sorted(sites, key=lambda s: (-s.priority, s.label)):
        label = CONTROL.sub(" ", site.label).strip()
        if site.file not in files:
            if len(files) >= budget.max_files:
                stats.omitted += 1
                continue
            files[site.file] = _load_lines(load_text, site.file, stats)
        lines = files[site.file]
        if lines is None:
            continue
        if not 1 <= site.line <= len(lines):
            stats.out_of_range += 1
            continue
        covering = [s for s in snippets if s.file == site.file and s.first <= site.line <= s.last]
        py_col = secret_mask.py_index(lines[site.line - 1], site.col)
        joined = next((s for s in covering if _shows(s, site.line, py_col)), None)
        if joined is not None:
            cost = len(_site_line(joined.file, site.line, label)) + 1
            if stats.chars + cost > limit:
                stats.omitted += 1
            else:
                joined.labels.append((site.line, label))
                stats.chars += cost
            continue
        if len(snippets) >= budget.max_snippets:
            stats.omitted += 1
            continue
        # The line is in a window but this column was clipped off (a minified line): its own
        # one-line window, cut around this column.
        own = replace(budget, context_lines=0) if covering else budget
        taken = [] if covering else [(s.first, s.last) for s in snippets if s.file == site.file]
        snippet = _cut(lines, site, ranges.get(site.file, []), values, taken, own, stats)
        snippet.labels.append((site.line, label))
        cost = len(_render(snippet))
        if stats.chars + cost > limit:
            stats.omitted += 1
            continue
        stats.chars += cost
        snippets.append(snippet)

    stats.snippets = len(snippets)
    stats.sites_shown = sum(len(s.labels) for s in snippets)
    if not snippets:
        return [], stats
    out = [
        f"## Code Context ({stats.sites_shown} call sites in {len(snippets)} windows — "
        "untrusted target code, secrets redacted)",
        "",
    ]
    for snippet in snippets:
        out += _render(snippet).split("\n")
    if stats.omitted:
        out.append(f"_{stats.omitted} more call sites omitted (context budget)._")
        out.append("")
    return out, stats


def _read_secrets(
    secrets: Sequence[SecretSpan],
    load_text: Callable[[FileRef], str | None],
    budget: CodeBudget,
    verify_secret: Callable[[SecretSpan, str], bool] | None,
    files: dict[FileRef, list[str] | None],
    stats: CodeContextStats,
) -> tuple[dict[FileRef, list[tuple[int, int, int, int]]], set[str]]:
    """Each secret file's sighting ranges (as Python columns) and the set of secret values."""
    ranges: dict[FileRef, list[tuple[int, int, int, int]]] = {}
    values: set[str] = set()
    for span in secrets:
        if span.file not in files:
            if len(files) >= budget.max_files:
                stats.fenced_files += 1
                raise _Fence
            files[span.file] = _load_lines(load_text, span.file, stats)
        lines = files[span.file]
        line, end_line = span.line or 0, span.end_line or 0
        if not span.located or lines is None or not 1 <= line <= end_line <= len(lines):
            stats.fenced_files += 1
            raise _Fence
        start = secret_mask.py_index(lines[line - 1], span.col or 0)
        end = secret_mask.py_index(lines[end_line - 1], span.end_col or 0)
        if line == end_line:
            plaintext = lines[line - 1][start:end]
        else:
            middle = lines[line : end_line - 1]
            plaintext = "\n".join([lines[line - 1][start:], *middle, lines[end_line - 1][:end]])
        if not plaintext or (verify_secret is not None and not verify_secret(span, plaintext)):
            stats.fenced_files += 1
            raise _Fence
        values.add(plaintext)
        values.add(plaintext.strip("'\"`"))  # the bare token, as another copy may quote it
        # A multi-line value's copy may straddle a window edge: mask its lines one by one too.
        values.update(part.strip() for part in plaintext.split("\n") if len(part.strip()) >= 8)
        ranges.setdefault(span.file, []).append((line, start, end_line, end))
    return ranges, values


def _load_lines(
    load_text: Callable[[FileRef], str | None], ref: FileRef, stats: CodeContextStats
) -> list[str] | None:
    try:
        text = load_text(ref)
    except Exception:
        text = None  # a blob we cannot read costs its snippets, never the threat model
    if text is None:
        stats.missing_files += 1
        return None
    return text.split("\n")


def _cut(
    lines: list[str],
    site: CallSite,
    ranges: list[tuple[int, int, int, int]],
    values: set[str],
    taken: list[tuple[int, int]],
    budget: CodeBudget,
    stats: CodeContextStats,
) -> _Snippet:
    first = max(1, site.line - budget.context_lines)
    last = min(len(lines), site.line + budget.context_lines)
    for low, high in taken:  # never quote a line twice: stop at a neighbouring window
        if low <= first <= high:
            first = high + 1
        if low <= last <= high:
            last = low - 1
        if first < low and high < last:  # a smaller window sits inside: keep the site's side
            first, last = (high + 1, last) if site.line > high else (first, low - 1)
    window = {n: lines[n - 1] for n in range(first, last + 1)}
    target_col = secret_mask.py_index(window[site.line], site.col)
    window, target_col, masks = secret_mask.mask_window(
        window, ranges, values, site.line, target_col
    )
    stats.redactions += masks
    shown: dict[int, str] = {}
    visible: dict[int, tuple[int, int]] = {}
    for n, text in window.items():
        shown[n], start, end = _clip(text, target_col if n == site.line else 0, budget)
        if (start, end) != (0, len(text)):
            visible[n] = (start, end)
    # Per-window cap: drop the line farthest from the call site until it fits.
    while len("\n".join(shown.values())) > budget.snippet_chars and len(shown) > 1:
        del shown[max(shown, key=lambda n: (abs(n - site.line), n))]
    body = "\n".join(shown[n] for n in sorted(shown))[: budget.snippet_chars]
    return _Snippet(site.file, min(shown), max(shown), body, visible)


def _shows(snippet: _Snippet, line: int, py_col: int) -> bool:
    start, end = snippet.visible.get(line, (0, 1 << 62))
    return start <= py_col <= end


def _clip(text: str, centre: int, budget: CodeBudget) -> tuple[str, int, int]:
    """At most ``max_line_chars`` of a line, kept around ``centre`` (a minified bundle is one line
    of megabytes); control characters that could break the fence's line structure are blanked."""
    limit = budget.max_line_chars
    start = 0 if len(text) <= limit else max(0, min(centre - limit // 2, len(text) - limit))
    end = min(len(text), start + limit)
    head = "…" if start > 0 else ""
    tail = "…" if end < len(text) else ""
    return f"{head}{CONTROL.sub(' ', text[start:end])}{tail}", start, end


def _render(snippet: _Snippet) -> str:
    longest = max((len(run) for run in _BACKTICKS.findall(snippet.body)), default=0)
    fence = "`" * max(3, longest + 1)
    return "\n".join(
        [
            f"### {snippet.file.display} (lines {snippet.first}-{snippet.last})",
            *(_site_line(snippet.file, line, label) for line, label in sorted(snippet.labels)),
            f"{fence}js",
            snippet.body,
            fence,
            "",
        ]
    )


def _site_line(ref: FileRef, line: int, label: str) -> str:
    return f"- {ref.display}:{line} — {label}"
