"""D64 code-context inputs and bounds: files, call sites, secret sightings, budget, stats."""

from __future__ import annotations

import re
from dataclasses import dataclass

# Control characters (and JS line separators) that could break a prompt line's structure.
CONTROL = re.compile(r"[\x00-\x1f\x7f\u2028\u2029]+")


@dataclass(frozen=True)
class FileRef:
    """One served file: a crawl asset's bundle (``source_path`` "input.js"), a recovered original
    (its source path) or a legacy run's single upload (``asset_url`` None)."""

    asset_url: str | None
    source_path: str

    @property
    def display(self) -> str:
        shown = self.asset_url if self.asset_url and self.source_path == "input.js" else None
        return CONTROL.sub(" ", shown or self.source_path)  # a map's paths are target-chosen


@dataclass(frozen=True)
class CallSite:
    """An endpoint's exact location in the served text: 1-based line, 0-based UTF-16 column."""

    label: str
    file: FileRef
    line: int
    col: int
    priority: int = 0


@dataclass(frozen=True)
class SecretSpan:
    """A secret sighting's served-text range; ``line`` None (or 0) = no exact location known.
    ``value``/``rule`` are the finding's identity hash and rule, for ``verify_secret``."""

    file: FileRef
    line: int | None
    col: int | None
    end_line: int | None
    end_col: int | None
    value: str | None = None
    rule: str | None = None

    @property
    def located(self) -> bool:
        return bool(self.line) and None not in (self.col, self.end_line, self.end_col)


@dataclass(frozen=True)
class CodeBudget:
    # ~25 lines a window, ~25 windows: about 8K tokens at most (DEBT D64's sizing).
    context_lines: int = 12
    max_line_chars: int = 240
    snippet_chars: int = 3000
    total_chars: int = 32000
    max_snippets: int = 25
    max_files: int = 25


@dataclass
class CodeContextStats:
    snippets: int = 0  # windows
    sites_shown: int = 0  # call sites named under those windows
    chars: int = 0
    omitted: int = 0  # sites dropped by the snippet cap, the file cap or the budget
    missing_files: int = 0
    fenced_files: int = 0  # secret files we could not read back exactly (=> no section at all)
    out_of_range: int = 0
    redactions: int = 0
