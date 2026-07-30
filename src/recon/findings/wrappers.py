"""Custom HTTP-client wrapper recognition (REQ-C2 first clause) — pure, stdlib-only.

An analyst teaches the extractor a wrapper by naming its callee (`api`,
`apiClient`); `recon.findings.extract.extract` then treats
`<callee>.<http-method>(path[, body])` and `<callee>.request({url, method})` as
endpoints via the existing axios-member path. This module holds ONLY the value
object, the input validator, and the callee-set helper, so it stays unit-testable
with no tree-sitter or DB dependency (spec §3).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

# A bare JS identifier: a member receiver like `api` / `apiClient` / `_http` / `$api`.
# Dotted receivers (`this.http`) and callable wrappers (`api('/x')`) are deferred
# fast-follows (spec §4), so a callee that is not a bare identifier is rejected at
# the door rather than silently never matching.
_CALLEE_RE = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")


class InvalidWrapperCallee(ValueError):
    """A wrapper callee that is not a bare JavaScript identifier."""


@dataclass(frozen=True)
class WrapperRule:
    """One taught wrapper: the bare identifier its HTTP calls are made on."""

    callee: str


def validate_callee(callee: str) -> None:
    """Raise :class:`InvalidWrapperCallee` unless `callee` is a bare JS identifier."""
    if not _CALLEE_RE.match(callee or ""):
        raise InvalidWrapperCallee(f"not a bare identifier: {callee!r}")


def wrapper_callees(rules: Sequence[WrapperRule]) -> frozenset[str]:
    """The set of callee identifiers to match in `_dispatch_member` (dispatch-last)."""
    return frozenset(rule.callee for rule in rules)
