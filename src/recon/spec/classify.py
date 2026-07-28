"""Spec §5.1/§5.2 canonicalization core -- reduce a client-finding operation
or a documented spec operation to one comparable shape.

`compare_key` is the single wildcarding function BOTH sides of the shadow
diff go through (design §5.1): a spec `{petId}` placeholder, a `normalize`
value-template (`{id}`/`{uuid}`/`{hash}`), a bare numeric/UUID segment, and a
clean single-segment client interpolation (`${id}`) all collapse to the same
`*`, so two differently-spelled but equivalent operations compare equal
(write-up prevention #1: compare canonical forms, never raw strings).

`is_partial`/`is_non_http` gate what may ever be classified `shadow` at all
(design §5.2/§5.3, gates N1/B3): a base-unresolved or mixed-interpolation
path, or a non-HTTP verb (`WS`/`WSS`), is structurally unable to prove
undocumented -- honesty over guessing (REQ-C2 ethos), the same bias
`normalize.py` already applies to ambiguous path segments.

Numeric/UUID detection is reimplemented locally rather than imported from
`normalize`'s private `_INT_RE`/`_UUID_RE` -- this module's only declared
dependencies are `operation_of_endpoint_value` and `HTTP_METHODS` (design
§5.1); a small local regex is cheaper than reaching into another feature's
internals for something that isn't actually its concern (the same call
`recon.spec.ingest` already made for its own `_PATH_ITEM_METHODS`).

Pure, stdlib-only, no DB/network -- both `recon.spec.classify`'s own §5.3
decision-order dispatcher (a later task in this slice) and this module's
tests call these three functions directly.
"""

from __future__ import annotations

import re

from recon.findings.extract import HTTP_METHODS
from recon.findings.normalize import operation_of_endpoint_value

# A spec placeholder (`{petId}`) or a `normalize` value-template
# (`{id}`/`{uuid}`/`{hash}`) -- both share this brace shape and both are
# statically-certain parameter positions, never partial (design §5.2).
_BRACE_PLACEHOLDER_RE = re.compile(r"^\{[^{}]*\}$")

# A client template-literal substitution filling the ENTIRE segment
# (`${id}`) rather than only part of it (`v${n}`) -- only the former is a
# clean, wildcardable parameter position.
_DOLLAR_INTERPOLATION_RE = re.compile(r"^\$\{[^{}]*\}$")

_INT_RE = re.compile(r"^\d+$")
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _method_and_path(operation: str) -> tuple[str, str]:
    """Split `"METHOD /path[?query]"` into `(METHOD, /path)`, the query
    dropped through the same helper the finding-value side already uses."""
    method, _, path = operation_of_endpoint_value(operation).partition(" ")
    return method, path


def _segments(path: str) -> list[str]:
    """Path segments in order. A leading `/` (or a doubled/trailing slash)
    contributes no empty component -- the same convention
    `normalize._templatize_path` already reconstructs paths under."""
    return [segment for segment in path.split("/") if segment != ""]


def _is_clean_interpolation(segment: str) -> bool:
    """True if `segment` is EXACTLY one `${...}` substitution -- the whole
    segment, with no surrounding literal text and no second substitution."""
    return bool(_DOLLAR_INTERPOLATION_RE.match(segment))


def _is_param_segment(segment: str) -> bool:
    """True if `segment` is a parameter position that wildcards to `*`
    (design §5.1): a brace placeholder, a clean single-segment `${...}`
    interpolation, a purely numeric segment, or a UUID segment."""
    return bool(
        _BRACE_PLACEHOLDER_RE.match(segment)
        or _is_clean_interpolation(segment)
        or _INT_RE.match(segment)
        or _UUID_RE.match(segment)
    )


def compare_key(operation: str) -> str:
    """Reduce `operation` to `"METHOD /wildcarded/path"` -- every parameter
    position collapsed to `*`, `?query` stripped (design §5.1, gate N6)."""
    method, path = _method_and_path(operation)
    segments = ["*" if _is_param_segment(seg) else seg for seg in _segments(path)]
    wildcarded = "/" + "/".join(segments) if segments else "/"
    return f"{method} {wildcarded}"


def is_partial(operation: str) -> bool:
    """True when `operation`'s path cannot be cleanly wildcarded at all
    (design §5.2): a LEADING `${...}` segment (an unresolved base), or any
    segment that MIXES literal text with an interpolation (`v${n}`,
    `${a}${b}`) and so can't be reduced to one clean substitution. A clean
    single-segment `${id}` in a non-leading position is a matchable
    parameter, not partial; `{id}`/`{uuid}`/`{hash}` value-templates are
    always certain, never partial."""
    _, path = _method_and_path(operation)
    for index, segment in enumerate(_segments(path)):
        if "${" not in segment:
            continue
        if not _is_clean_interpolation(segment):
            return True  # mixed literal + interpolation -- can't wildcard
        if index == 0:
            return True  # leading interpolation -- unresolved base
    return False


def is_non_http(operation: str) -> bool:
    """True when `operation`'s method is not a documentable HTTP verb
    (design §5.1, gate B3) -- `WS`/`WSS` and any other non-HTTP verb are
    structurally undocumentable in OpenAPI and must never reach `shadow`."""
    method, _ = _method_and_path(operation)
    return method not in HTTP_METHODS
