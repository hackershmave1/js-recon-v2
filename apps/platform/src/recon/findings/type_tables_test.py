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

import pytest

from recon.domain import FindingType
from recon.findings import priority

# The repo root is the ancestor holding `apps/` — looked up, not indexed: inside the API image
# the file sits at /app/src/recon/findings, which has fewer ancestors than the checkout.
_REPO = next((p for p in Path(__file__).resolve().parents if (p / "apps").is_dir()), Path("/"))
_WEB_LABELS = _REPO / "apps/platform/web/src/api/findingLabels.ts"
_EXTENSION_LABELS = _REPO / "apps/capture/chrome-extension/modules/finding-labels.js"
# Types whose wire token already reads as plain English, so both clients show it unchanged.
_READS_AS_IS = frozenset({FindingType.SECRET.value, FindingType.PARAM.value})
_ALL = frozenset(t.value for t in FindingType)
# The integration lane runs pytest inside the API image, which ships only `src/` — the client
# sources are not there. The host-tests lane (full checkout) is where the label guards run.
_needs_client_sources = pytest.mark.skipif(
    not (_WEB_LABELS.exists() and _EXTENSION_LABELS.exists()),
    reason="client label sources not in this checkout/image (enforced by the host-tests lane)",
)


def _labels(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    block = text[text.index("TYPE_LABELS") : text.index("};", text.index("TYPE_LABELS"))]
    labels = dict(re.findall(r"^\s*([a-z_]+):\s*[\"']([^\"']+)[\"']", block, re.MULTILINE))
    assert labels, f"parsed no labels from {path} — the guard would pass vacuously"
    return labels


def test_every_finding_type_has_a_priority_base():
    missing = sorted(_ALL - set(priority._TYPE_BASE))
    assert missing == [], f"add a base score to priority._TYPE_BASE for: {missing}"


@_needs_client_sources
def test_every_finding_type_is_labelled_or_reads_as_is_in_the_workspace():
    labelled = set(_labels(_WEB_LABELS))
    assert sorted(_ALL - labelled - _READS_AS_IS) == [], "label it in web findingLabels.ts"
    assert sorted(labelled - _ALL) == [], "a web label for a type that no longer exists"


@_needs_client_sources
def test_the_extension_names_every_type_exactly_as_the_workspace_does():
    assert _labels(_EXTENSION_LABELS) == _labels(_WEB_LABELS)
