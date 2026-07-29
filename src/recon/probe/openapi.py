"""Serialize a run's reconstructed requests into a valid OpenAPI 3.0.3 document
(the inverse of spec-ingest). Pure over ``reconstruct.ReconstructedRequest`` — no
DB, no engines, no active traffic.

Honesty (REQ-C2): parameter NAMES are observed; parameter/body TYPES and schemas
are inferred and marked so; no security is asserted (headers are not captured).
Every emitted document is validated with ``openapi-spec-validator`` before it is
returned, so a caller never receives an invalid spec.
"""

from __future__ import annotations

import json
import re
from urllib.parse import urlsplit

import yaml
from openapi_spec_validator import validate

from recon.probe.reconstruct import ReconstructedRequest

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

# Methods OpenAPI 3.x allows as path-item operation keys. A client call using any
# other verb (PURGE, PROPFIND, SUBSCRIBE, ...) is real attack surface but is not a
# valid OpenAPI operation, so — like WS/WSS — it is surfaced in a root extension
# instead of written into `paths` (which would fail validation and 500 the export).
_OPENAPI_METHODS = frozenset({"get", "put", "post", "delete", "options", "head", "patch", "trace"})


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


_RESPONSE_DESCRIPTION = "Not observed — static analysis does not capture responses."


def _query_param(param) -> dict:
    obj = {
        "name": param.name,
        "in": "query",
        "required": False,
        "schema": {"type": "string"},
        "description": "Name observed; type inferred.",
    }
    if param.example is not None:
        obj["example"] = param.example
    return obj


def _request_body(request: ReconstructedRequest) -> dict | None:
    # Only assert a media type we actually observed (fetch/axios -> json). jQuery/xhr
    # bodies leave content_type None; those are surfaced via x-recon-body-params instead.
    if not request.body_params or request.content_type is None:
        return None
    properties = {
        name: {"type": "string", "description": "Name observed; type inferred."}
        for name in request.body_params
    }
    return {
        "required": False,
        "content": {
            request.content_type: {
                "schema": {
                    "type": "object",
                    "description": "Property names observed statically; types inferred; not exhaustive.",
                    "properties": properties,
                }
            }
        },
    }


def _body_confidence(request: ReconstructedRequest) -> str:
    if not request.body_params:
        return "absent"
    return "inferred" if request.content_type else "names-only"


def _operation_object(request: ReconstructedRequest, path_params: list[dict]) -> dict:
    parameters = list(path_params) + [_query_param(p) for p in request.query_params]
    operation: dict = {
        "x-recon-confidence": {
            "path": "certain",
            "methods": "observed-only",
            "param-names": "synthesized" if path_params else "observed",
            "param-types": "inferred",
            "body": _body_confidence(request),
        },
        "responses": {"default": {"description": _RESPONSE_DESCRIPTION}},
    }
    if parameters:
        operation["parameters"] = parameters
    body = _request_body(request)
    if body is not None:
        operation["requestBody"] = body
    elif request.body_params:  # names known, content-type not observed
        operation["x-recon-body-params"] = list(request.body_params)
        operation["description"] = (
            "Request body observed with property names: "
            + ", ".join(request.body_params)
            + "; content-type not observed, so no request-body schema is asserted."
        )
    return operation


_INFO_DESCRIPTION = (
    "Statically reconstructed from JavaScript by the recon platform. Paths, HTTP "
    "methods, and parameter names are OBSERVED. Parameter and body TYPES and schemas "
    "are INFERRED. Response bodies were not observed. No authentication is asserted — "
    "request headers are not captured by static analysis."
)
_SERVER_DESCRIPTION = "Host observed; scheme/port inferred where not seen in a concrete URL."


def _merge_operations(existing: dict, other: dict) -> dict:
    # Union parameters by (name, in); existing wins on a name+location conflict.
    seen = {(p["name"], p["in"]) for p in existing.get("parameters", [])}
    merged = list(existing.get("parameters", []))
    for param in other.get("parameters", []):
        key = (param["name"], param["in"])
        if key not in seen:
            merged.append(param)
            seen.add(key)
    if merged:
        existing["parameters"] = merged
    _merge_bodies(existing, other)
    return existing


def _body_property_names(operation: dict) -> set[str]:
    names: set[str] = set(operation.get("x-recon-body-params", []))
    for media in operation.get("requestBody", {}).get("content", {}).values():
        names |= set(media.get("schema", {}).get("properties", {}))
    return names


def _content_types(operation: dict) -> set[str]:
    return set(operation.get("requestBody", {}).get("content", {}))


def _merge_bodies(existing: dict, other: dict) -> None:
    """Union both operations' request-body evidence so no body property name is ever
    dropped on a canonicalization collision, and keep the honesty tag + description
    consistent with the merged result. `description` is only ever set from a body
    (names-only branch of _operation_object), so clearing it here is safe."""
    names = sorted(_body_property_names(existing) | _body_property_names(other))
    content_types = sorted(_content_types(existing) | _content_types(other))
    existing.pop("requestBody", None)
    existing.pop("x-recon-body-params", None)
    existing.pop("description", None)
    if not names:
        existing["x-recon-confidence"]["body"] = "absent"
        return
    if content_types:
        # Build a FRESH schema per media type (never share a dict reference — a shared
        # ref makes yaml.safe_dump emit anchors/aliases).
        existing["requestBody"] = {
            "required": False,
            "content": {
                ct: {
                    "schema": {
                        "type": "object",
                        "description": "Property names observed statically; types inferred; not exhaustive.",
                        "properties": {
                            name: {"type": "string", "description": "Name observed; type inferred."}
                            for name in names
                        },
                    }
                }
                for ct in content_types
            },
        }
        existing["x-recon-confidence"]["body"] = "inferred"
    else:
        existing["x-recon-body-params"] = names
        existing["description"] = (
            "Request body observed with property names: "
            + ", ".join(names)
            + "; content-type not observed, so no request-body schema is asserted."
        )
        existing["x-recon-confidence"]["body"] = "names-only"


def _servers(requests: list[ReconstructedRequest]) -> list[dict]:
    by_host: dict[str, str] = {}
    for request in requests:
        if not request.probeable:
            continue
        if request.example_url:
            split = urlsplit(request.example_url)
            if split.scheme and split.hostname:
                origin = f"{split.scheme}://{split.hostname}"
                if split.port:
                    origin += f":{split.port}"
                by_host[split.hostname] = origin
        for host in request.hosts:
            by_host.setdefault(host, f"https://{host}")
    return [{"url": url, "description": _SERVER_DESCRIPTION} for url in sorted(by_host.values())]


def build_openapi(requests: list[ReconstructedRequest], *, run_id: str) -> dict:
    paths: dict[str, dict] = {}
    websockets: list[str] = []
    nonstandard: list[str] = []
    for request in requests:
        if not request.probeable:
            websockets.append(f"{request.method} {request.example_url or request.path}")
            continue
        method = request.method.lower()
        if method not in _OPENAPI_METHODS:
            nonstandard.append(f"{request.method} {request.example_url or request.path}")
            continue
        canon_path, path_params = _canonicalize_path(request.path)
        operation = _operation_object(request, path_params)
        path_item = paths.setdefault(canon_path, {})
        if method in path_item:
            path_item[method] = _merge_operations(path_item[method], operation)
        else:
            path_item[method] = operation

    document: dict = {
        "openapi": "3.0.3",
        "info": {
            "title": f"Reconstructed API — run {run_id[:8]}",
            "version": "0.0.0",
            "description": _INFO_DESCRIPTION,
        },
        "paths": paths,
    }
    servers = _servers(requests)
    if servers:
        document["servers"] = servers
    if websockets:
        document["x-recon-websocket-endpoints"] = sorted(set(websockets))
    if nonstandard:
        document["x-recon-nonstandard-operations"] = sorted(set(nonstandard))

    validate(document)  # honesty guarantee — never return an invalid document
    return document


class _NoAliasDumper(yaml.SafeDumper):
    def ignore_aliases(self, data):  # never emit &anchor/*alias
        return True


def dump_openapi(document: dict, fmt: str) -> tuple[bytes, str]:
    if fmt == "json":
        return json.dumps(document, indent=2).encode("utf-8"), "application/json"
    if fmt == "yaml":
        text = yaml.dump(document, Dumper=_NoAliasDumper, sort_keys=False, allow_unicode=True)
        return text.encode("utf-8"), "application/yaml"
    raise ValueError(f"unsupported format: {fmt!r}")
