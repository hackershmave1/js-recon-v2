"""Config-object client calls: `<receiver>.<request-ish name>({method: "<verb>", url})`.

A real SPA sent ~88% of its API paths through `this.httpClient.request({method, url})` on a client
injected via `Object.assign(this, deps)` — nothing proved the receiver an HTTP client, so every call
was dropped. The `{method: <HTTP-verb literal>, url}` object is the evidence; the method NAME is what
separates a call out from the same shape used as a route table (`fastify.route`), a mock
(`fetchMock.mock`), a log line (`logger.logRequest`) or plain data (`observations.push`).
"""

from __future__ import annotations

import re

from tree_sitter import Node

from recon.findings._jsast import HTTP_METHODS, _object_pairs, _string_value, _text_if_short

# A whole name (`request`, `fetch`) or a camelCase name ENDING in one (`apiRequest`, `cxRequest`,
# `sendHttp`). Never a substring: `requestAnimationFrame`, `prefetch`, `fetchMore`, `httpsAgent`.
_CLIENT_NAME = re.compile(
    r"^(?:request|fetch|ajax|http)$|^[a-z][A-Za-z0-9]*(?:Request|Fetch|Ajax|Http)$"
)
# camelCase prefixes that describe a request rather than send one: logging, telemetry, test
# doubles, interceptors and event hooks all take the same `{method, url}` object.
_NON_SENDING_PREFIX = re.compile(
    r"^(?:log|track|report|on|mock|stub|fake|emit|record|intercept|handle|trace|audit|cache)[A-Z]"
)


def is_config_call(prop: str, first_arg: Node | None) -> bool:
    """True for a request-ish method called with one config object holding a literal HTTP-verb
    `method` and a `url` (a shorthand `url` counts — still a request site, just unresolvable). A
    forwarded `{method: e.method}` is a wrapper's own body, not a call site."""
    if not _CLIENT_NAME.match(prop) or _NON_SENDING_PREFIX.match(prop):
        return False
    if first_arg is None or first_arg.type != "object":
        return False
    # `_object_pairs` decodes keys through `_text_if_short`: a computed `[expr]` key can nest a whole
    # sink, and decoding it in full at every level is the O(n^2) shape the extractor is hardened against.
    pairs = _object_pairs(first_arg)
    method = _string_value(pairs.get("method"))
    if method is None or method.upper() not in HTTP_METHODS:
        return False
    return "url" in pairs or any(
        child.type == "shorthand_property_identifier" and _text_if_short(child) == "url"
        for child in first_arg.named_children
    )
