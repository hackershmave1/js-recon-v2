"""REQ-C2 manual base-URL resolution — the pure read-time overlay.

Analyst-set rules (prefix or selection) prepend a base to a host-less client
operation PATH. Prepend-only (never rewrite/truncate — upholds the classifier
SAFETY INVARIANT), segment-boundary matching, idempotent, relative-only. Pure
and stdlib-only: the DB/service layer builds ``BaseUrlRule`` from rows and passes
them in; reconstruct/classify apply the result at read time. Findings are never
rewritten, so finding identity never churns.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class BaseUrlRule:
    kind: str  # "prefix" | "selection"
    base_url: str  # 'https://api.example.com/v3' or '/location'
    path_prefix: str | None = None  # kind == "prefix"
    finding_hashes: tuple[str, ...] = ()  # kind == "selection"


@dataclass(frozen=True)
class ResolvedOp:
    path: str
    host: str | None  # netloc (host[:port]) if base_url carried one, else None
    scheme: str | None  # scheme if base_url carried one, else None
    changed: bool


class InvalidBaseUrl(ValueError):
    """A base_url that is neither a root-relative path nor an http(s) URL."""


def validate_base_url(base_url: str) -> None:
    """Raise :class:`InvalidBaseUrl` unless ``base_url`` is usable: a root-relative
    path (``/x``) or an absolute http(s) URL. No query/fragment, no userinfo."""
    if not base_url:
        raise InvalidBaseUrl("base_url must not be empty")
    split = urlsplit(base_url)
    if split.query or split.fragment:
        raise InvalidBaseUrl("base_url must not carry a query or fragment")
    if split.scheme or split.netloc:
        if split.scheme not in ("http", "https"):
            raise InvalidBaseUrl("base_url scheme must be http or https")
        if not split.netloc:
            raise InvalidBaseUrl("base_url with a scheme must include a host")
        if "@" in split.netloc:
            raise InvalidBaseUrl("base_url must not carry userinfo")
    elif not base_url.startswith("/"):
        raise InvalidBaseUrl("a path-only base_url must start with '/'")


def _segments(path: str) -> list[str]:
    return [s for s in path.split("/") if s != ""]


def _is_segment_prefix(prefix: str, path: str) -> bool:
    """True if ``path`` starts with ``prefix`` on whole-segment boundaries."""
    p, q = _segments(prefix), _segments(path)
    return len(p) <= len(q) and q[: len(p)] == p


def _split_base(base_url: str) -> tuple[str | None, str | None, str]:
    """``(scheme, netloc, path_prefix)`` — scheme/netloc are ``None`` for a
    path-only base; the path has any trailing slash stripped so a join is a clean
    concat."""
    split = urlsplit(base_url)
    if split.scheme or split.netloc:
        return split.scheme, split.netloc, split.path.rstrip("/")
    return None, None, base_url.rstrip("/")


def _match(
    path: str, endpoint_hashes: tuple[str, ...], rules: list[BaseUrlRule]
) -> BaseUrlRule | None:
    """At most one rule applies: a selection rule (explicit) beats every prefix
    rule; among prefix rules the longest (most segments) matching prefix wins."""
    hashset = set(endpoint_hashes)
    for rule in rules:
        if rule.kind == "selection" and hashset & set(rule.finding_hashes):
            return rule
    best: BaseUrlRule | None = None
    for rule in rules:
        if (
            rule.kind == "prefix"
            and rule.path_prefix
            and _is_segment_prefix(rule.path_prefix, path)
        ) and (
            best is None
            or len(_segments(rule.path_prefix)) > len(_segments(best.path_prefix or ""))
        ):
            best = rule
    return best


def resolve_operation(
    method: str,
    path: str,
    endpoint_hashes: tuple[str, ...],
    has_host: bool,
    rules: list[BaseUrlRule],
) -> ResolvedOp:
    """Apply the matched rule to a candidate op. Candidate = host-less
    (``has_host`` False) AND a root-relative ``path`` (begins ``/``); anything else
    is returned unchanged (no double-join, no false shadow — gate B1). Prepends the
    base path to the WHOLE op path (the prefix only selects; it is never stripped),
    idempotently (a path already under the base path is left as-is)."""
    if has_host or not path.startswith("/"):
        return ResolvedOp(path=path, host=None, scheme=None, changed=False)
    rule = _match(path, endpoint_hashes, rules)
    if rule is None:
        return ResolvedOp(path=path, host=None, scheme=None, changed=False)
    scheme, netloc, base_path = _split_base(rule.base_url)
    new_path = path
    if base_path and not _is_segment_prefix(base_path, path):
        new_path = base_path + path
    changed = new_path != path or netloc is not None
    return ResolvedOp(path=new_path, host=netloc, scheme=scheme, changed=changed)
