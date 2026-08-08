"""Structure + index-integrity guard for the Architecture Decision Record trail.

The ADRs live at the repo root in ``docs/adr/`` (beside ``docs/ARCHITECTURE.md`` — the
cross-app "what" doc whose "why" they record). This test has no adjacent source on
purpose: it must sit under ``apps/platform/src`` and be named ``*_test.py`` or the gated
fast lane never collects it (``pyproject.toml`` pins ``testpaths = ["src"]`` /
``python_files = ["*_test.py"]`` and CI runs from ``apps/platform``). It resolves the ADR
directory by walking UP to the repo root (the ``.git`` ancestor) rather than a fixed
relative depth, so an ``apps/`` restructure can't silently point it at nothing, and it
never ``**``-globs (which would sweep the stale ``.claude/worktrees/**/docs`` copies).

Scope is deliberately narrow (DEBT D10 design): filename shape, unique numbering, valid
frontmatter (``status`` + ISO ``date``), and bidirectional README<->file index integrity
— the metadata that predictably rots as the set grows. Section-heading / prose linting is
a code-review concern, not a CI gate (it would couple every doc edit to a red build).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_FILENAME_RE = re.compile(r"^\d{4}-[a-z0-9-]+\.md$")
_NUMBER_RE = re.compile(r"^(\d{4})-")
# MADR `status` is an OPEN set, not a closed enum: a future supersession
# ("superseded by ADR-0123") must not red-build this gate.
_STATUS_RE = re.compile(r"^(proposed|accepted|rejected|deprecated|superseded by .+)$")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# Only same-directory markdown-link targets (an ADR): the leading "(" excludes
# path-qualified links such as (../superpowers/specs/2026-...md) or (../ARCHITECTURE.md).
_LINK_RE = re.compile(r"\((\d{4}-[a-z0-9-]+\.md)\)")

_TEMPLATE = "0000-adr-template.md"
_INDEX = "README.md"


def _repo_root() -> Path | None:
    """Nearest ancestor containing ``.git``, else ``None``.

    ``None`` means this isn't a git checkout — e.g. the module was packaged into
    the built image, where the integration lane runs ``pytest`` with no repo tree
    (``api``/``worker`` mount no source; build context is ``apps/platform`` only).
    That case SKIPs (below) rather than failing: the ADR trail is a repo artifact
    the image never ships. A real checkout still fails loudly when ``docs/adr/`` is
    missing, so an accidental deletion is caught.
    """
    for parent in Path(__file__).resolve().parents:
        if (parent / ".git").exists():
            return parent
    return None


def _parse_frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    fields: dict[str, str] = {}
    for line in text[3:end].splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip().strip('"').strip("'")
    return fields


_ROOT = _repo_root()
pytestmark = pytest.mark.skipif(
    _ROOT is None,
    reason="not a git checkout (e.g. the built image) — the ADR trail is not present here",
)

ADR_DIR = (_ROOT / "docs" / "adr") if _ROOT is not None else Path("docs/adr")
# Decision records only: NNNN-*.md minus the 0000 template. Empty when skipping.
ADR_FILES = (
    sorted(
        path
        for path in ADR_DIR.glob("*.md")
        if _FILENAME_RE.match(path.name) and path.name != _TEMPLATE
    )
    if _ROOT is not None
    else []
)


def test_adr_dir_has_records_template_and_index() -> None:
    assert ADR_FILES, f"no ADR records found in {ADR_DIR}"
    assert (ADR_DIR / _TEMPLATE).is_file(), "missing 0000-adr-template.md"
    assert (ADR_DIR / _INDEX).is_file(), "missing README.md index"


def test_adr_filenames_are_well_formed() -> None:
    bad = [path.name for path in ADR_DIR.glob("*.md") if not _is_known_doc(path.name)]
    assert not bad, f"ADR filenames must match NNNN-kebab.md: {sorted(bad)}"


def test_adr_numbers_are_unique() -> None:
    numbers = [m.group(1) for path in ADR_FILES if (m := _NUMBER_RE.match(path.name))]
    duplicates = sorted({n for n in numbers if numbers.count(n) > 1})
    assert not duplicates, f"duplicate ADR numbers: {duplicates}"


@pytest.mark.parametrize("path", ADR_FILES, ids=lambda p: p.name)
def test_adr_has_valid_frontmatter(path: Path) -> None:
    fields = _parse_frontmatter(path.read_text(encoding="utf-8"))
    status = fields.get("status", "")
    date = fields.get("date", "")
    assert _STATUS_RE.match(status), f"{path.name}: invalid status {status!r}"
    assert _ISO_DATE_RE.match(date), f"{path.name}: invalid/missing ISO date {date!r}"


def test_readme_index_matches_files() -> None:
    linked = set(_LINK_RE.findall((ADR_DIR / _INDEX).read_text(encoding="utf-8")))
    linked.discard(_TEMPLATE)  # the template may be linked but is not a record
    on_disk = {path.name for path in ADR_FILES}
    missing_from_index = sorted(on_disk - linked)
    dangling = sorted(name for name in linked if not (ADR_DIR / name).is_file())
    assert not missing_from_index, f"ADRs not linked in README: {missing_from_index}"
    assert not dangling, f"README links with no file: {dangling}"


def _is_known_doc(name: str) -> bool:
    return name in {_TEMPLATE, _INDEX} or bool(_FILENAME_RE.match(name))
