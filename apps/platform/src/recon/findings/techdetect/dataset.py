"""Load the vendored enthec/webappanalyzer fingerprint dataset (GPL-3.0, server-side
only — T10). Package-data JSON, lru-cached. Fail-closed: a missing, corrupt, OR
syntactically-valid-but-empty dataset raises (the analyze pass swallows it at
runtime; a load-time test guarantees presence, NOT the test-only
RECON_REQUIRE_ENGINES flag — T7)."""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from typing import TypedDict, cast

_DATA_PACKAGE = "recon.findings.techdetect_data"


class RawTechnology(TypedDict, total=False):
    cats: list[int]
    headers: dict[str, str]
    cookies: dict[str, str]
    scriptSrc: list[str]
    scripts: list[str]
    meta: dict[str, str]
    js: dict[str, str]
    html: list[str]
    implies: list[str]
    website: str


def _parse_raw(text: str) -> dict[str, RawTechnology]:
    """Parse + validate the vendored technologies.json text in isolation (no
    filesystem/package access), so the fail-closed contract (T7) is directly
    unit-testable. Raises ``json.JSONDecodeError`` on malformed JSON, and
    ``ValueError`` if the parsed mapping is syntactically valid but empty — an
    empty dataset is as unusable as a missing one and must not load silently."""
    techs = cast("dict[str, RawTechnology]", json.loads(text))
    if not techs:
        raise ValueError("techdetect dataset loaded but is empty")
    return techs


@lru_cache(maxsize=1)
def load_raw() -> tuple[dict[str, RawTechnology], dict[str, str], str]:
    """Return (technologies, category-id -> name, pinned commit). lru-cached."""
    files = resources.files(_DATA_PACKAGE)
    techs = _parse_raw(files.joinpath("technologies.json").read_text(encoding="utf-8"))
    raw_categories = cast(
        "dict[str, dict[str, object]]",
        json.loads(files.joinpath("categories.json").read_text(encoding="utf-8")),
    )
    categories = {cid: str(entry["name"]) for cid, entry in raw_categories.items()}
    commit = files.joinpath("commit.txt").read_text(encoding="utf-8").strip()
    return techs, categories, commit


def category_names(cats: list[int], categories: dict[str, str]) -> list[str]:
    """Resolve enthec numeric category ids to display names, dropping unknown ids."""
    return [categories[str(cid)] for cid in cats if str(cid) in categories]
