"""Serialize a run's reconstructed requests into a valid OpenAPI 3.0.3 document
(the inverse of spec-ingest). Pure over ``reconstruct.ReconstructedRequest`` — no
DB, no engines, no active traffic.

Honesty (REQ-C2): parameter NAMES are observed; parameter/body TYPES and schemas
are inferred and marked so; no security is asserted (headers are not captured).
Every emitted document is validated with ``openapi-spec-validator`` before it is
returned, so a caller never receives an invalid spec.
"""

from __future__ import annotations

import re

# Path tokens ``normalize.py`` emits for value-templated segments, mapped to an
# inferred OpenAPI schema. Every OTHER interpolation is handled generically.
_RECOGNIZED: dict[str, dict] = {
    "{id}": {"type": "integer"},
    "{uuid}": {"type": "string", "format": "uuid"},
    "{hash}": {"type": "string"},
}

# A segment that is exactly one clean ``{name}`` or ``${name}`` (name is a legal
# identifier) — its name is reusable verbatim; anything else gets a positional name.
_SINGLE_INTERP = re.compile(r"^\$?\{([A-Za-z_][A-Za-z0-9_]*)\}$")

_PARAM_DESCRIPTION = (
    "Name synthesized and type inferred from a templated path segment; "
    "the original parameter name is not recoverable from static analysis."
)


def _unique(base: str, used: set[str]) -> str:
    name = base
    counter = 2
    while name in used:
        name = f"{base}{counter}"
        counter += 1
    used.add(name)
    return name


def _path_param(name: str, schema: dict) -> dict:
    return {
        "name": name,
        "in": "path",
        "required": True,
        "schema": dict(schema),
        "description": _PARAM_DESCRIPTION,
    }


def _canonicalize_path(path: str) -> tuple[str, list[dict]]:
    """Rewrite ``path`` so every interpolation becomes one balanced, uniquely-named
    path parameter with a matching declaration. Guarantees the emitted path contains
    only balanced ``{legalName}`` tokens — the OpenAPI-validity invariant (gate B1/B2)."""
    out_segments: list[str] = []
    params: list[dict] = []
    used: set[str] = set()
    positional = 0
    for segment in path.split("/"):
        if segment in _RECOGNIZED:
            name = _unique(segment[1:-1], used)  # strip the braces
            out_segments.append("{" + name + "}")
            params.append(_path_param(name, _RECOGNIZED[segment]))
        elif "{" in segment or "}" in segment or "$" in segment:
            match = _SINGLE_INTERP.match(segment)
            if match:
                name = _unique(match.group(1), used)
            else:
                positional += 1
                name = _unique(f"p{positional}", used)
            out_segments.append("{" + name + "}")
            params.append(_path_param(name, {"type": "string"}))
        else:
            out_segments.append(segment)
    return "/".join(out_segments), params
