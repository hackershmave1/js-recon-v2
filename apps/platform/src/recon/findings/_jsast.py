"""Tree-sitter JavaScript AST primitives shared by the extractor (DEBT D11 leaf).

The dependency-free base of the ``findings`` extractor: the parser singleton and
sink vocabulary, the raw dataclasses the extractor emits (:class:`RawParam`,
:class:`RawEndpoint`, :class:`Extraction`) plus the base-URL environment record
(:class:`BaseEnv`), the low-level tree-walking helpers, and the param builders.
Imports only the standard library and tree-sitter — the leaf of the import DAG
``_jsast`` <- ``_base_env`` <- ``extract`` — so it can be shared without cycles.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from urllib.parse import parse_qsl

import tree_sitter_javascript as tsjs
from tree_sitter import Language, Node, Parser

_LANGUAGE = Language(tsjs.language())
_PARSER = Parser(_LANGUAGE)

HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})
_GLOBAL_OBJECTS = frozenset({"window", "globalThis", "self"})
_JQUERY = frozenset({"$", "jQuery"})
# jQuery helper -> HTTP method (config-driven ones resolve method from the config).
_JQUERY_METHODS = {"get": "GET", "post": "POST", "getJSON": "GET"}

# --- Tier 5 (generic-call): verb call on an unrecognised HTTP-client-shaped receiver ---
# A receiver-agnostic member call `receiver.{get,post,…}("/path")` catches an UNTAUGHT
# custom client (`apiClient.get(...)`). Two gates carry precision, because a receiver-name
# denylist ALONE is inert on minified bundles: there the receiver text `_dispatch_member`
# sees is a 1-2 char mangle (`n`/`e`), never a full word. So precision rests on a
# readable-receiver gate (_is_http_client_name) + a strict path-shape gate
# (_looks_like_api_path); verb methods only (not the ambiguous `.request`).
_GENERIC_METHODS = frozenset({"get", "post", "put", "patch", "delete"})

# Substrings that positively mark a receiver as an HTTP client at ANY length.
_HTTP_CLIENT_HINTS = (
    "api",
    "http",
    "client",
    "service",
    "rest",
    "request",
    "ajax",
    "axios",
    "graphql",
    "gql",
)

# Readable receiver names that are common NON-HTTP objects — never an API client even when
# long enough to pass the length gate. (Minified 1-2 char receivers never reach the gate;
# this list only has to disambiguate READABLE receivers like `cache`/`router`/`store`.)
_NON_HTTP_RECEIVERS = frozenset(
    {
        "cache",
        "caches",
        "map",
        "set",
        "weakmap",
        "weakset",
        "params",
        "searchparams",
        "urlsearchparams",
        "headers",
        "cookies",
        "cookie",
        "storage",
        "localstorage",
        "sessionstorage",
        "session",
        "router",
        "route",
        "history",
        "location",
        "store",
        "state",
        "model",
        "models",
        "query",
        "queries",
        "db",
        "database",
        "emitter",
        "events",
        "bus",
        "logger",
        "console",
        "promise",
        "jquery",
        "lodash",
        "dayjs",
        "moment",
        "window",
        "document",
        "self",
        "globalthis",
        # server-side route DEFINERS (`app.get("/x", handler)`) and config accessors — a `.get`
        # here defines/reads, it does not call out. Readable enough to pass the length gate, so
        # they must be named (unlike minified receivers, which the length gate handles).
        "app",
        "server",
        "express",
        "fastify",
        "koa",
        "config",
        "settings",
    }
)


@dataclass(frozen=True)
class RawParam:
    name: str
    location: str  # "query" | "body"


@dataclass(frozen=True)
class HeaderRef:
    """An auth-relevant request header seen statically: its NAME and, when the value starts
    with a string literal (incl. a ``"Bearer " + token`` concatenation), the auth SCHEME
    keyword. The credential VALUE is never captured (enrichment B / honesty T3)."""

    name: str
    scheme: str | None  # "bearer" | "basic" | None (unknown / dynamic value)


@dataclass(frozen=True)
class RawEndpoint:
    kind: str  # fetch | xhr | axios | jquery | websocket
    method: str
    url: str
    params: tuple[RawParam, ...]
    line: int
    col: int
    start_byte: int
    end_byte: int
    snippet: str
    # Provenance: the callee of the taught wrapper this endpoint came from, else
    # None. NOT folded into `kind` — `kind` stays "axios" so the POST-body
    # Content-Type gate at reconstruct.py:176 still fires (spec §7 / §12 Imp 3).
    wrapper: str | None = None
    # Auth-relevant request headers captured statically (enrichment B): names + scheme
    # keyword only, never a credential value. Empty for calls with no auth header.
    headers: tuple[HeaderRef, ...] = ()


@dataclass
class Extraction:
    endpoints: list[RawEndpoint] = field(default_factory=list)
    unattributed: int = 0  # sinks detected but URL not statically resolvable (REQ-C2)
    # Tier 4 (unconfirmed lane): the SAME unresolved sinks counted in `unattributed`,
    # surfaced as best-effort skeleton rows instead of silently dropped. They ride
    # alongside `unattributed` (never instead of it), so the honesty counter is
    # unchanged — an unresolved call is still counted as unattributed AND shown.
    unresolved: list[RawEndpoint] = field(default_factory=list)
    # Tier 5 (generic-call): SUSPECTED sinks — a verb call on an unrecognised HTTP-client-
    # shaped receiver (see recon.findings.extract._record_generic). NOT counted in
    # `unattributed` (it is not a DETECTED sink, unlike `unresolved`), surfaced as a distinct
    # ENDPOINT_GENERIC finding so it stays out of coverage AND out of the confirmed read model.
    generic: list[RawEndpoint] = field(default_factory=list)


@dataclass(frozen=True)
class BaseEnv:
    instances: dict[str, str | None]  # axios.create var -> base literal, or None if dynamic
    default_base: str | None  # axios.defaults.baseURL literal
    const_prefixes: dict[str, str]  # const name -> string literal (for `${NAME}` prefixes)


# --- tree helpers ------------------------------------------------------------


def _walk(node: Node) -> Iterator[Node]:
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(reversed(current.children))


def _text(node: Node | None) -> str:
    return (node.text or b"").decode("utf-8", "replace") if node is not None else ""


# Cap for decoding a node's text when the RESULT is only matched against short names or
# rendered as a short token. No identifier the extractor cares about — a global, `axios`, a
# jQuery `$`, an axios-instance/const/wrapper var, a `WebSocket` constructor, a `:holder`
# leaf name — is anywhere near this many bytes (real ones are tens of bytes; minifiers make
# names SHORTER). Decoding a larger span is wasted O(span) work, and summed over a
# deeply-nested crafted expression (the `.concat()`/`+` string-splitting or nested-sink
# obfuscation this product analyzes) it is O(n^2) — a worker-stalling DoS on an in-cap
# bundle. The span is an O(1) field read taken BEFORE the decode. (DoS hardening.)
_MAX_NODE_TEXT_SPAN = 256

# Byte budget for a 200-char finding snippet: 200 chars are at most 200*4 bytes of UTF-8, so
# slicing this many SOURCE bytes (`_source_snippet`) yields output byte-identical to the old
# `node.text[:200]` while bounding the work — crucial because tree-sitter's `node.text`
# materializes the WHOLE node span (O(span)), which is O(n^2) summed over a nested-sink chain.
_SNIPPET_MAX_BYTES = 800

# Span cap for a sink's URL node (string/template). Larger than `_MAX_NODE_TEXT_SPAN` because
# a real query-string URL can run to a few KB, but a URL node bigger than this can't be
# statically resolved without an O(span) decode — and on a nested crafted template
# (`fetch(`a${ fetch(...) }`)`) that per-sink decode is O(n^2). Over-cap → treated as
# unresolvable / rendered as EXPR (honest, drop-only). (DoS hardening.)
_MAX_URL_SPAN = 8192


def _text_if_short(node: Node | None) -> str:
    """``_text(node)``, or ``""`` when the node's byte span exceeds ``_MAX_NODE_TEXT_SPAN``.

    For callers that only compare the text against short names: no realistically-sized
    receiver/constructor/holder reaches the cap, so an over-cap node is treated as
    unrecognized — the same outcome as any other non-matching text, drop-only and never
    invented — while the skipped decode keeps per-node work O(1)."""
    if node is None:
        return ""
    if node.end_byte - node.start_byte > _MAX_NODE_TEXT_SPAN:
        return ""
    return _text(node)


def _source_snippet(data: bytes, start_byte: int, end_byte: int) -> str:
    """A bounded finding snippet sliced from the SOURCE bytes — byte-identical to the old
    ``node.text[:200]`` but O(snippet), not O(node span).

    tree-sitter's ``node.text`` materializes the whole ``data[start:end]`` slice before any
    truncation, so ``node.text[:200]`` is O(span); summed over a nested-sink chain (each
    enclosing sink re-decoding an overlapping span) that is a worker-stalling O(n^2) DoS.
    Capping the slice at ``min(start+cap, end)`` reads at most ``_SNIPPET_MAX_BYTES`` and
    never past the node, so it equals ``data[start:end][:cap]`` == ``node.text[:cap]`` while
    staying O(cap). ``errors="replace"`` absorbs a multibyte char split at the byte boundary;
    the trailing ``[:200]`` re-truncates to the char budget. (DoS hardening.)"""
    stop = min(start_byte + _SNIPPET_MAX_BYTES, end_byte)
    return data[start_byte:stop].decode("utf-8", "replace")[:200]


def _string_value(node: Node | None) -> str | None:
    """Resolve a string/template literal to its text; ``None`` if not static.

    Template strings keep their ``${...}`` substitutions verbatim so the shape
    survives (`/users/${id}` stays visible) instead of being dropped or guessed.
    """
    if node is None:
        return None
    if node.type == "string":
        text = _text(node)
        if len(text) >= 2 and text[0] in "\"'" and text[-1] == text[0]:
            return text[1:-1]
        return text
    if node.type == "template_string":
        # A template can EMBED other sinks in `${…}`, so a crafted nested template
        # (`fetch(`a${ fetch(...) }`)`) makes every enclosing sink re-decode a URL spanning
        # all inner sinks — O(n^2). Over-cap → None (not statically resolvable, honest) bounds
        # that decode at its single source, covering every sink type (fetch/axios/xhr/jquery)
        # and `_collapse_url`. A plain `string` literal can't embed a sink, so it is left
        # uncapped (opaque, decoded once per referencing sink = linear). (DoS hardening.)
        if node.end_byte - node.start_byte > _MAX_URL_SPAN:
            return None
        text = _text(node)
        return text[1:-1] if text.startswith("`") and text.endswith("`") else text
    return None


_EXPR = "EXPR"  # jsluice-style placeholder for a non-constant URL sub-expression

_VOWELS = frozenset("aeiouAEIOU")


def _is_readable_name(name: str) -> bool:
    """True when ``name`` reads like a real identifier rather than a minifier mangle.
    Rejects 1-char names and vowelless 2-char names (``u``, ``e``, ``xr``, ``bt``) — the
    shapes that flood a mangled bundle — while keeping short-but-meaningful names
    (``id`` has a vowel, so it passes). This is what decides whether a dynamic URL leaf
    is worth surfacing as a ``:holder`` token instead of a blind ``EXPR``; a length
    floor alone would both admit mangles and reject the common ``:id`` param."""
    if not name or not name.replace("_", "").isalnum():
        return False
    if len(name) == 1:
        return False
    return len(name) != 2 or any(char in _VOWELS for char in name)


def _expr_token(node: Node | None) -> str:
    """Render a non-constant URL leaf as a ``:holder`` token from its source identifier
    (the developer's own name for the value), or ``EXPR`` when there is no readable name
    (a call result, computed access, or a minifier mangle).

    ``window.location.origin``/``.href`` (also ``document.location``/bare ``location``)
    ARE the current origin, so a same-origin URL built from them collapses the marker to
    ``""`` — letting a following ``/path`` root cleanly (``origin + "/x/" + id`` ->
    ``/x/:id``). The token is read from real source text, never invented; an unnameable
    leaf degrades to ``EXPR``, so the never-guess invariant holds."""
    if node is None:
        return _EXPR
    if node.end_byte - node.start_byte > _MAX_NODE_TEXT_SPAN:
        return _EXPR  # too large to be a readable holder name — honest EXPR, bounds the decode
    text = _text(node).replace(" ", "")
    segments = text.split(".")
    # Same-origin marker: window.location.origin / document.location.href / location.origin.
    # A structural segment check (not a substring endswith) so `geolocation.origin`,
    # `mylocation.origin`, `dislocation.href` are NOT collapsed — they are ordinary holders.
    if len(segments) >= 2 and segments[-1] in ("origin", "href") and segments[-2] == "location":
        return ""
    if node.type in ("identifier", "member_expression", "property_identifier"):
        last = segments[-1]
        if _is_readable_name(last):
            return ":" + last
    return _EXPR


def _collapse_url(node: Node | None, _depth: int = 0) -> str:
    """Best-effort skeleton for a sink's UNRESOLVED URL argument (Tier 4 / jsluice
    ``CollapsedString``): keep static string/template text, reconstruct ``+`` AND
    ``.concat()`` chains, and render a readable non-constant leaf as a ``:holder`` token
    from its source identifier — ``"/api/" + id`` -> ``/api/:id``, ``g.downloadUrl`` ->
    ``:downloadUrl`` — while a minifier mangle (``u``, ``xr``) stays ``EXPR``. Only the
    unconfirmed/generic lanes use this; a statically resolvable URL never reaches
    here (it lands in ``endpoints`` instead), so a ``:holder`` can never enter the
    confirmed lane.

    ``_depth`` bounds the recursion for BOTH ``+`` and ``.concat()``: string-splitting
    (``"a"+"b"+…`` or ``"a".concat("b")…``) is a common static-analysis-evasion
    obfuscation this product targets, and a pathologically deep chain must degrade to
    ``EXPR`` rather than blow the Python stack. The cap sits far beyond any real URL."""
    if node is None or _depth >= 32:
        return _EXPR
    if node.type in ("string", "template_string"):
        value = _string_value(node)  # an empty literal ("") is a real value, not a miss
        return value if value is not None else _EXPR
    if node.type == "binary_expression":
        operator = node.child_by_field_name("operator")
        if operator is not None and _text(operator) == "+":  # string concatenation only
            return _collapse_url(node.child_by_field_name("left"), _depth + 1) + _collapse_url(
                node.child_by_field_name("right"), _depth + 1
            )
        return _EXPR
    if node.type == "call_expression":
        fn = node.child_by_field_name("function")
        if (
            fn is not None
            and fn.type == "member_expression"
            and _text(fn.child_by_field_name("property")) == "concat"
        ):
            # String#concat: ``a.concat(b, c)`` == ``a + b + c`` — reconstructed like ``+``,
            # sharing the depth cap so a ``.concat()``-split obfuscation degrades to a
            # skeleton instead of a stack blow-out.
            parts = [_collapse_url(fn.child_by_field_name("object"), _depth + 1)]
            parts += [_collapse_url(arg, _depth + 1) for arg in _args(node)]
            return "".join(parts)
        return _EXPR
    return _expr_token(node)


def _is_http_client_name(name: str) -> bool:
    """Generic-call receiver gate (Tier 5): True when a member-call receiver plausibly denotes
    an HTTP client. A readable identifier that is NOT a known non-HTTP object, and that either
    carries an HTTP-ish hint (`apiClient`, `httpService`, `this.http`) or is at least 4 chars —
    long enough to be a real name rather than a minifier's 1-2 char mangle (`n`, `e`, `xr`).
    The length floor is what carries precision on minified code: there the receiver is a mangle,
    so the gate simply does NOT fire (and the real gap on minified fetch-based bundles is Tier
    4, already surfaced). `map`/`cache`/`store`/`router` are excluded even though they are
    readable — they are the common non-HTTP `.get`/`.set`/`.delete` receivers."""
    lowered = name.lower()
    # Match the LAST dotted segment against the denylist, so a member-chain receiver like
    # `this.store`/`this.cache`/`this.router` is excluded too — not just a bare `store`. The
    # denylist is segment-exact (unlike the substring hint check below) so it never denies a
    # real client like `apiStore`; rsplit with no dot returns the whole name unchanged.
    if lowered.rsplit(".", 1)[-1] in _NON_HTTP_RECEIVERS:
        return False
    if any(hint in lowered for hint in _HTTP_CLIENT_HINTS):
        return True
    return len(name) >= 4


def _looks_like_api_path(skeleton: str) -> bool:
    """Generic-call argument gate (Tier 5): True only for a rooted path (`/users`), an absolute
    URL (`https://…`), or a template base + path (`${base}/users`). A bare word (`userId`), a
    dotted path (lodash `_.get("a.b.c")`), a relative `a/b`, or a pure `EXPR` is rejected.
    Deliberately stricter than jsluice `MaybeURL` (which passes ANY leading-`/` string and
    dotted hostnames): the unconfirmed lane guesses less, so a signal this weak needs a clear
    path anchor. `skeleton` is `_collapse_url` output — non-constant sub-exprs are already
    `EXPR`, so a leading `/` here means a real static path head, not a guessed one."""
    if "://" in skeleton:
        return True
    if skeleton.startswith("/"):
        return True
    return skeleton.startswith("${") and "/" in skeleton


def _args(call: Node) -> list[Node]:
    arguments = call.child_by_field_name("arguments")
    return list(arguments.named_children) if arguments is not None else []


def _object_pairs(node: Node | None) -> dict[str, Node]:
    """Map an object literal's keys to their value nodes (string + identifier keys)."""
    pairs: dict[str, Node] = {}
    if node is None or node.type != "object":
        return pairs
    for child in node.named_children:
        if child.type != "pair":
            continue
        key_node = child.child_by_field_name("key")
        value_node = child.child_by_field_name("value")
        if key_node is None or value_node is None:
            continue
        if key_node.type in ("string", "template_string"):
            key = _string_value(key_node)
        else:  # property_identifier / identifier / computed_property_name (`[expr]`)
            # `_text_if_short`, not `_text`: a computed key `[expr]` can nest a sink, and its
            # uncapped decode per enclosing object was O(n^2); an over-cap computed key isn't
            # a static param name anyway, so "" (dropped below) is the honest result. (DoS)
            key = _text_if_short(key_node)
        if key:
            pairs[key] = value_node
    return pairs


# --- param extraction --------------------------------------------------------


def _query_params(url: str) -> list[RawParam]:
    query = url.split("?", 1)[1] if "?" in url else ""
    seen: dict[str, None] = {}
    for key, _value in parse_qsl(query, keep_blank_values=True):
        if key:
            seen.setdefault(key, None)
    return [RawParam(name, "query") for name in seen]


def _body_params(node: Node | None) -> list[RawParam]:
    return [RawParam(name, "body") for name in _object_pairs(node)]


def _body_params_from_value(node: Node | None) -> list[RawParam]:
    """Body params from an object literal OR a ``JSON.stringify({...})`` wrapper
    (the near-universal way a JSON body is built)."""
    if node is None:
        return []
    if node.type == "object":
        return _body_params(node)
    if node.type == "call_expression":
        fn = node.child_by_field_name("function")
        if fn is not None and _text(fn) == "JSON.stringify":
            inner = _args(node)
            if inner and inner[0].type == "object":
                return _body_params(inner[0])
    return []


def _config_query_params(config: Node | None) -> list[RawParam]:
    """axios's ``params`` config key is serialized into the query string."""
    params_obj = _object_pairs(config).get("params")
    return [RawParam(name, "query") for name in _object_pairs(params_obj)]


# Request headers that describe an auth surface (case-insensitive). Only these become
# OpenAPI security schemes; everything else (Content-Type, Accept, ...) is ignored.
_AUTH_HEADERS = frozenset(
    {
        "authorization",
        "authentication",
        "proxy-authorization",
        "x-api-key",
        "api-key",
        "apikey",
        "x-auth-token",
        "x-access-token",
        "x-amz-security-token",
    }
)


def _leading_string(node: Node | None) -> str | None:
    """The literal string at the head of a value node: a bare string/template, or the left
    operand of a ``"prefix" + expr`` concatenation. None if the head is not a literal — so
    ``"Bearer " + token`` yields ``"Bearer "`` while a bare ``token`` yields None.

    Walks the ``+`` left-spine ITERATIVELY, not recursively: a crafted deep ``"a"+"b"+…``
    header value would otherwise overflow the Python stack on this hot path — a crash-class
    DoS (cf. ``_collapse_url``'s ``_depth`` cap). Only the leftmost literal is needed."""
    while node is not None:
        if node.type in ("string", "template_string"):
            return _string_value(node)
        if node.type == "binary_expression":
            node = node.child_by_field_name("left")
            continue
        return None
    return None


def _header_scheme(value: Node | None) -> str | None:
    text = _leading_string(value)
    if text is None:
        return None
    lowered = text.strip().lower()
    if lowered.startswith("bearer"):
        return "bearer"
    if lowered.startswith("basic"):
        return "basic"
    return None


def _auth_headers(headers_node: Node | None) -> list[HeaderRef]:
    """Auth-relevant headers from a ``headers:`` object literal: NAME + scheme keyword,
    filtered to the auth allow-list. A dynamic value keeps the name with scheme=None."""
    result: list[HeaderRef] = []
    for name, value in _object_pairs(headers_node).items():
        if name.lower() in _AUTH_HEADERS:
            result.append(HeaderRef(name=name, scheme=_header_scheme(value)))
    return result


def _endpoint(
    kind: str,
    method: str,
    url: str,
    params: list[RawParam],
    call: Node,
    wrapper: str | None = None,
    headers: list[HeaderRef] | None = None,
) -> RawEndpoint:
    row, col = call.start_point
    deduped = list(dict.fromkeys(params))  # preserve order, drop repeats
    return RawEndpoint(
        kind=kind,
        method=method.upper(),
        url=url,
        params=tuple(deduped),
        line=row + 1,
        col=col,
        start_byte=call.start_byte,
        end_byte=call.end_byte,
        # Snippet deferred to `extract()`'s post-pass (`_source_snippet`), which has the source
        # bytes and slices them O(cap); building it here from `call.text` would be O(node span)
        # → O(n^2) over a nested-sink chain. Filled before `extract` returns.
        snippet="",
        wrapper=wrapper,
        headers=tuple(headers or ()),
    )
