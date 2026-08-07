"""Ingest an untrusted OpenAPI 3.x / Swagger 2.0 spec into a documented
operation set (design §4, `recon.spec.ingest`).

An uploaded spec sits on the same footing as target JS: a hostile caller
controls every byte. Generic YAML/JSON loaders and `openapi-spec-validator`
will, by default, expand YAML aliases/anchors and resolve `$ref` over the
filesystem and network — an SSRF / local-file-read / resource-exhaustion
side-window that would bypass the platform's egress guard
(`fetch/egress.py`) and contradict "no spec-by-URL" (gate B4, design §4.1).
`ingest_spec` runs three ordered guards, each of which MUST reject
(`SpecError`) before the next runs:

  1. hardened parse — source-size cap, a YAML loader that refuses
     anchors/aliases outright, then post-parse node-count + nesting-depth
     bounds on the already-parsed structure.
  2. reject any `$ref` that is not a local `#/...` JSON pointer — no
     network/file handler is ever registered, so this scan is the only
     thing standing between a hostile spec and a `file://`/HTTP fetch.
  3. only once 1-2 pass, hand the structure to `openapi-spec-validator`
     (0.9.0's `validate()`) for real OpenAPI/Swagger schema validation.

Only after all three succeed do we resolve `servers`/`basePath` (gate B5) and
build the documented `(method, path)` set. `path` is left server-base-
prefixed and RAW — still carrying `{param}`-style placeholders — because
reducing it to the canonical wildcarded compare-key (design §5.1) has to be
the exact same function used for the client-finding side, and that function
lives in `recon.spec.classify`, not here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import yaml
from openapi_spec_validator import validate

# A real OpenAPI/Swagger document is at most a few MB of text; this bounds
# the hostile end (gate B4) while leaving headroom for large, legitimate
# specs (e.g. Stripe's public spec, which runs into the low single-digit MB).
_MAX_SOURCE_BYTES = 5 * 1024 * 1024

# Post-parse bounds, checked on the structure the parser already built. These
# catch a size/breadth bomb that needs no YAML anchors at all — a plain JSON
# body has no alias syntax, so this is its only structural bound besides the
# byte cap above. Anchors are banned outright (see `_NoAliasSafeLoader`), so
# with no exponential blow-up possible, node count only grows linearly with
# input size — generous enough for real specs, still a real bound.
_MAX_NODES = 200_000
_MAX_DEPTH = 100

# Valid OpenAPI/Swagger Path Item Object keys that denote an operation (a
# spec-shape fact, not a JS-detection heuristic — kept local rather than
# imported from `recon.findings.extract.HTTP_METHODS` so this module doesn't
# reach into another feature for something that isn't actually its concern).
_PATH_ITEM_METHODS = frozenset(
    {"get", "put", "post", "delete", "options", "head", "patch", "trace"}
)


class SpecError(ValueError):
    """The spec body is malformed, oversized, or fails hardening/validation.

    One exception type for every rejection path (parse, size/depth bound,
    external `$ref`, OpenAPI/Swagger schema validation) — the future
    `POST /runs/{id}/spec` route maps it straight to HTTP 422; the spec body
    is untrusted input, same footing as target JS (gate B4)."""


class _NoAliasSafeLoader(yaml.SafeLoader):  # type: ignore[misc]
    """A `SafeLoader` that refuses YAML anchors and aliases outright.

    PyYAML's composer expands `&anchor`/`*alias` nodes before we ever see the
    result, so a handful of chained aliases (`b: [*a,*a]`) can expand into an
    exponential structure from a tiny input — the "billion laughs" /
    alias-bomb attack. Anchors have no legitimate use in an OpenAPI/Swagger
    document, so every anchor OR alias is rejected outright rather than
    bounding the expansion after the fact: verified against PyYAML 6.0.3
    (`Composer.compose_node`) that ANY composed event — scalar, sequence-
    start, mapping-start, or alias — carries a non-`None` `.anchor` the
    moment it defines or references one, so one check ahead of the real
    composer catches both cases uniformly.
    """

    def compose_node(self, parent: Any, index: Any) -> Any:
        event = self.peek_event()
        if getattr(event, "anchor", None) is not None:
            raise SpecError("YAML anchors/aliases are not allowed in a spec")
        return super().compose_node(parent, index)


@dataclass(frozen=True)
class DocumentedOp:
    """One documented `(method, path)` pair from the ingested spec.

    `path` is the server-base-prefixed path AS WRITTEN in the spec — raw,
    pre compare-key (design §5.1 wildcarding happens in `recon.spec.classify`,
    applied identically to both this and the client-finding side)."""

    method: str
    path: str


@dataclass(frozen=True)
class IngestedSpec:
    """The result of successfully parsing, hardening, and validating a spec."""

    format: str  # "openapi-3" | "swagger-2" — matches session_spec.spec_format's CHECK
    server_bases: list[str]
    documented: tuple[DocumentedOp, ...]


def ingest_spec(raw: bytes) -> IngestedSpec:
    """Parse, harden, and validate an OpenAPI 3.x / Swagger 2.0 spec body.

    See the module docstring for the three ordered guards. Raises
    `SpecError` on any failure at any stage."""
    if len(raw) > _MAX_SOURCE_BYTES:
        raise SpecError(f"spec exceeds the {_MAX_SOURCE_BYTES}-byte size cap")

    parsed = _parse(raw)
    if not isinstance(parsed, dict):
        raise SpecError("spec must be a JSON/YAML object at the top level")

    _check_bounds(parsed)
    _reject_external_refs(parsed)

    try:
        validate(parsed)
    except Exception as exc:  # noqa: BLE001 - untrusted input; any failure -> 422
        raise SpecError(f"spec failed OpenAPI/Swagger validation: {exc}") from exc

    fmt = _detect_format(parsed)
    server_bases = _server_bases(parsed, fmt)
    documented = _documented_ops(parsed, server_bases)
    return IngestedSpec(format=fmt, server_bases=server_bases, documented=documented)


def _parse(raw: bytes) -> object:
    """Decode + parse `raw` as JSON first, falling back to hardened YAML.

    JSON has no anchor/alias syntax, so a JSON body never touches
    `_NoAliasSafeLoader`; anything else — including a spec that merely looks
    like YAML, since JSON is a YAML subset — is parsed through the
    anchor-rejecting loader."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SpecError("spec is not valid UTF-8") from exc

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    except RecursionError as exc:
        # A deeply-nested-but-tiny body (valid JSON structure, well under
        # the byte cap) hits the C-accelerated json.loads' recursion limit
        # *during* parsing, before _check_bounds's own depth check ever runs
        # against the already-parsed structure -- RecursionError must be
        # caught explicitly here or it escapes ingest_spec uncaught instead
        # of being cleanly rejected.
        raise SpecError(f"spec is not valid JSON or YAML: {exc}") from exc

    try:
        return yaml.load(text, Loader=_NoAliasSafeLoader)
    except SpecError:
        raise  # our own hardening guard - never mistaken for a generic parse error
    except (yaml.YAMLError, RecursionError) as exc:
        # A deeply-nested-but-tiny body (no anchors, well under the byte/node
        # caps) blows PyYAML's composer recursion *during* this yaml.load
        # call, before _check_bounds's own depth check ever runs against the
        # already-parsed structure -- RecursionError is not a yaml.YAMLError
        # subclass, so it must be caught explicitly here or it escapes
        # ingest_spec uncaught instead of being cleanly rejected.
        raise SpecError(f"spec is not valid JSON or YAML: {exc}") from exc


def _check_bounds(parsed: object) -> None:
    """Reject a parsed structure that is too large or too deep to be a real
    spec (node-count + nesting-depth halves of gate B4's hardened parse).

    NOTE: this walks the structure the parser already built, so it bounds
    everything downstream (validation, ref-scan, our own extraction) but not
    the parse call's own peak memory. Recursion-depth exhaustion during parsing
    (`json.loads` or the YAML composer) is caught in `_parse` and re-raised as
    `SpecError`, so both JSON and YAML paths uniformly honor the over-bounds→
    SpecError contract; `_check_bounds` provides the explicit, lower node-count/
    depth bound on top of that."""
    node_count = 0

    def walk(node: object, depth: int) -> None:
        nonlocal node_count
        node_count += 1
        if node_count > _MAX_NODES:
            raise SpecError(f"spec exceeds the {_MAX_NODES}-node bound")
        if depth > _MAX_DEPTH:
            raise SpecError(f"spec exceeds the {_MAX_DEPTH}-level nesting bound")
        if isinstance(node, dict):
            for value in node.values():
                walk(value, depth + 1)
        elif isinstance(node, list):
            for item in node:
                walk(item, depth + 1)

    walk(parsed, 1)


def _reject_external_refs(node: object) -> None:
    """Recursively reject any `$ref` whose value is not a local JSON pointer.

    A pure structural scan over the already-parsed data (gate B4) — it never
    follows/dereferences a `$ref`, only inspects the literal string, so an
    in-document cycle (`$ref` A -> B -> A) can't make this recurse forever;
    there simply is no edge to follow. No resolver/handler is ever registered
    with openapi-spec-validator, so this scan is what actually stands between
    a hostile spec and a `file://`/HTTP fetch — a spec that requires an
    external ref is rejected outright here, never given the chance to
    trigger one."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and not (isinstance(value, str) and value.startswith("#")):
                raise SpecError(f"external $ref is not allowed: {value!r}")
            _reject_external_refs(value)
    elif isinstance(node, list):
        for item in node:
            _reject_external_refs(item)


def _detect_format(parsed: dict[str, Any]) -> str:
    if "openapi" in parsed:
        return "openapi-3"
    if parsed.get("swagger") == "2.0":
        return "swagger-2"
    raise SpecError("unrecognized spec: no 'openapi' or 'swagger' key")


def _server_bases(parsed: dict[str, Any], fmt: str) -> list[str]:
    if fmt == "swagger-2":
        return [parsed.get("basePath") or ""]

    servers = parsed.get("servers") or [{"url": ""}]
    return [_resolve_server_url(server) for server in servers]


def _resolve_server_url(server: object) -> str:
    """Resolve a 3.x `servers[]` entry's `{variable}` templates via each
    variable's `default` BEFORE prefixing (gate B5) — taking the template
    literally would flag every documented call under it as an undocumented
    shadow endpoint.

    Returns the PATH component ONLY — never `scheme://host` — via
    `urlsplit(...).path` after variable resolution. Design §4 says "prepend
    the base PATH from the spec's servers", and the client side of the diff
    (`recon.findings.normalize.endpoint_operation`) builds its operation from
    `urlsplit(url).path`, i.e. host-free by construction. A real OpenAPI 3.x
    `servers[].url` is almost always host-ful (`https://api.example.com/v1`);
    keeping the host here would make `_documented_ops` below produce a path
    like `https://api.example.com/v1/pets`, whose wildcarded segments
    (`compare_key`, in `recon.spec.classify`) can NEVER equal the client
    side's host-free segments — permanently emptying the `documented` bucket
    for any spec that uses a real server URL (final-review Fix 1). A
    host-only URL (`https://host`, no path) or an absent/empty `servers`
    resolves to `""`, same as before; a relative URL (`/api/{v}`) is
    unaffected since it was already path-only."""
    if not isinstance(server, dict):
        return ""
    url = server.get("url") or ""
    variables = server.get("variables") or {}
    for name, variable in variables.items():
        default = variable.get("default") if isinstance(variable, dict) else None
        if default is not None:
            url = url.replace("{" + name + "}", str(default))
    return urlsplit(url).path


def _documented_ops(parsed: dict[str, Any], server_bases: list[str]) -> tuple[DocumentedOp, ...]:
    paths = parsed.get("paths") or {}
    documented: list[DocumentedOp] = []
    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        for method in path_item:
            if method not in _PATH_ITEM_METHODS:
                continue
            for base in server_bases:
                documented.append(DocumentedOp(method.upper(), base + path))
    return tuple(documented)
