"""Every FindingType is covered by every per-type table that must cover it (C8 guard).

The rules for a finding type live where they are used (priority here, labels in the two
clients); the architecture review chose to keep it that way and guard it instead of a central
catalogue. Adding a FindingType without a priority or a label used to fail silently: an
unknown type gets the default priority and renders as its raw wire token. These tests make
that a CI failure, and pin the extension's labels to the workspace's (the extension is built
separately and cannot import them — its file only says "KEEP IN SYNC").
"""

from __future__ import annotations

import re
from pathlib import Path

from recon.domain import FindingType
from recon.findings import priority

_REPO = Path(__file__).resolve().parents[5]
_WEB_LABELS = _REPO / "apps/platform/web/src/api/findingLabels.ts"
_EXTENSION_LABELS = _REPO / "apps/capture/chrome-extension/modules/finding-labels.js"
# Types whose wire token already reads as plain English, so both clients show it unchanged.
_READS_AS_IS = frozenset({FindingType.SECRET.value, FindingType.PARAM.value})
_ALL = frozenset(t.value for t in FindingType)


def _labels(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    block = text[text.index("TYPE_LABELS") : text.index("};", text.index("TYPE_LABELS"))]
    return dict(re.findall(r"^\s*([a-z_]+):\s*[\"']([^\"']+)[\"']", block, re.MULTILINE))


def test_every_finding_type_has_a_priority_base():
    missing = sorted(_ALL - set(priority._TYPE_BASE))
    assert missing == [], f"add a base score to priority._TYPE_BASE for: {missing}"


def test_every_finding_type_is_labelled_or_reads_as_is_in_the_workspace():
    labelled = set(_labels(_WEB_LABELS))
    assert sorted(_ALL - labelled - _READS_AS_IS) == [], "label it in web findingLabels.ts"
    assert sorted(labelled - _ALL) == [], "a web label for a type that no longer exists"


def test_the_extension_names_every_type_exactly_as_the_workspace_does():
    assert _labels(_EXTENSION_LABELS) == _labels(_WEB_LABELS)
