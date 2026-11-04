"""REQ-D3 finding-hash normalization — the stable identity of a finding.

Turns a raw extracted finding into a canonical ``finding_hash`` computed over
*stable fields only* (``version + type + value``), so a retry with slightly
different evidence yields the same hash (REQ-D3). That hash keys the exactly-once
outbox write (REQ-A3) and the partial-aware diff (REQ-D5).

Source ``path`` is NOT hashed (v2): the same logical finding (e.g. ``GET :url``)
seen in several source files is ONE finding with several occurrences, not one row
per file — the same treatment already given to ``host`` (identity decision C1).
Every distinct source path still survives as an ``occurrence.source_path`` (REQ-C2
honesty: a merge is visible, never silently dropped), and dropping path IMPROVES
D5 rebuild-survival (a bundle re-chunk that moves a call between files no longer
churns its identity). See ``FINDING_HASH_VERSION`` for the versioned rollout.

Everything here is pure and dependency-free (stdlib only) so it is trivially
testable and safe to call inside a staging transaction. Mutable per-sighting
detail (host, path, offsets, line/col, evidence) is deliberately NOT hashed — it
lives on occurrence rows. Full rationale + the design review outcome:
``docs/req-d3-finding-hash-normalization.md``.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlsplit

# Sentinels: a null source-map entry (allowed by ECMA-426) must not be conflated
# with "no source map at all", and neither with a real empty path.
NO_MAP = "{no-map}"
NULL_SOURCE = "{null-source}"

# Entropy gates (bits/char). A real content-hash token is high-entropy; human
# slugs are not. Bias is conservative: an over-merge silently loses attack
# surface (review C2/H1), so ambiguous segments stay literal (an extra finding
# is honest; a missing one is not).
_PATH_HASH_ENTROPY = 3.0
_SEG_TOKEN_ENTROPY = 4.0

_INT_RE = re.compile(r"^\d+$")
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_HEX16_RE = re.compile(r"^[0-9a-fA-F]{16,}$")
# Contiguous alnum only (a hyphen/underscore is word-separator structure -> slug,
# kept literal). A digit + high entropy then separates random tokens from long
# identifiers like `oauth2callbackhandler`.
_TOKEN_RE = re.compile(r"^[A-Za-z0-9]{24,}$")
_SCHEME_RE = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.\-]*):(//)?(.*)$", re.DOTALL)
_ARRAY_KEY_RE = re.compile(r"\[\d*\]$")

# Percent-decode only unreserved octets before templating, so an encoded literal
# (%41 -> A) is compared correctly while a reserved char (e.g. %2F) stays encoded
# and cannot forge an extra path segment.
_UNRESERVED = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
_PCT_RE = re.compile(r"%([0-9A-Fa-f]{2})")

# Secret match boundaries are engine-defined; strip surrounding delimiters an
# engine may have captured (a trailing quote, brackets, whitespace) before
# hashing so v1 and v2 of a rule agree (REQ-D3 stability, review M2). `.`/`-`/`_`
# `=`/`+`/`/` are token-legal and are NOT stripped.
_SECRET_DELIMS = "\"'`()[]{}<>,;: \t\r\n"

# Kingfisher ships ~930 evolving rules with ids `kingfisher.<provider>.<n>`; the
# provider slug is derived from the second segment (see provider_for_rule), which
# is stable across rule renumbering (review M1). This map is a pinned OVERRIDE for
# any specific rule id whose second segment is not the desired provider slug — it
# is keyed by the real full rule id and empty until such a case is found.
# KINGFISHER_RULES_VERSION is informational: it records which ruleset the
# second-segment derivation was validated against (not wired into any hash).
KINGFISHER_RULES_VERSION = "1.106"
_PROVIDER_BY_RULE: dict[str, str] = {
    # The shipped standalone-AKIA rule (recon.findings.rules.custom_rules)
    # has id `custom.aws.akia_standalone`; its FIRST segment is "custom", so the
    # generic derivation would return "custom". Pin it to "aws" so a lone AWS
    # access key id hashes as aws:sha256(token) — a stable, honest provider slug,
    # the same way a built-in AWS secret hashes. (This does NOT dedupe against the
    # built-in AWS rule: that rule keys on the paired *secret* access key — a
    # different token — so the values differ.) The hand-fixed-slug case this
    # override map exists for (review M1).
    "custom.aws.akia_standalone": "aws",
    # The config-GUID rule (recon.findings.rules.custom_rules, DEBT D33) has id
    # custom.config.guid_assignment; its first segment "custom" would derive to
    # "custom". Pin it to "config" — NOT "azure": the rule also catches non-Azure
    # `*_API_KEY: '<guid>'` keys, and provider is user-visible (it prefixes
    # finding.value = provider:sha256, rendered as the finding header), so "azure"
    # would be a false claim on a generic key. "config" honestly says "a GUID
    # exposed in configuration" without guessing an issuer. The slug is baked into
    # the finding hash (a one-way door under REQ-D5), so it is chosen once here.
    "custom.config.guid_assignment": "config",
    # D33-B recall sibling (custom.config.guid_assignment_low) — kebab/camelCase key
    # GUIDs surfaced only in the opt-in low-confidence "suspected" tier. Same "config"
    # provider slug and rationale as the medium rule above; the finding TYPE
    # (secret_suspected), not the provider, is what separates the tiers.
    "custom.config.guid_assignment_low": "config",
}


def shannon_entropy(text: str) -> float:
    """Shannon entropy in bits/char (0.0 for empty)."""
    if not text:
        return 0.0
    counts: dict[str, int] = {}
    for ch in text:
        counts[ch] = counts.get(ch, 0) + 1
    n = len(text)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


# ---------------------------------------------------------------------------
# Source-path normalization (§3) — best-effort stable across rebuilds.
# ---------------------------------------------------------------------------


def _split_scheme(source: str) -> tuple[str | None, str]:
    """Split a source into (authority, path). The scheme is dropped; the
    authority/namespace is KEPT (webpack adds it to prevent path collisions) and
    lowercased. Returned separately so `..` resolution can never pop it."""
    match = _SCHEME_RE.match(source)
    if not match:
        return None, source
    _scheme, slashes, rest = match.groups()
    if slashes == "//":
        authority, _sep, path = rest.partition("/")
        return (authority.lower() or None), path
    # `webpack:/js/...` or `webpack:js/...` — no authority, just a path.
    return None, rest.lstrip("/")


def _is_hash_token(component: str, *, allow_wordish: bool) -> bool:
    # Hex hashes (with a digit, so hex-letter words like `decade`/`facade` stay
    # literal) collapse in any position.
    if re.fullmatch(r"[0-9a-fA-F]{6,}", component) and any(c.isdigit() for c in component):
        return True
    # Ambiguous base64url-ish tokens collapse ONLY in a non-stem position, so a
    # camelCase filename stem (`Utf8Decoder`) can never be mistaken for a hash.
    return bool(
        allow_wordish
        and re.fullmatch(r"[A-Za-z0-9]{8,}", component)
        and shannon_entropy(component) >= _PATH_HASH_ENTROPY
    )


def _collapse_hashes_in_segment(segment: str) -> str:
    """Replace content-hash components (`app.9f8e7d6c.js` -> `app.{hash}.js`) by
    POSITION + entropy (spec §3.2): the filename stem (first component) and the
    extension (last) are never collapsed, so two distinct camelCase files like
    `Base64Encoder.js` / `Utf8Decoder.js` keep separate identities (review HIGH-1);
    hex hashes may sit anywhere. Catches `[contenthash:6]` and rollup base64url."""
    parts = re.split(r"([._\-])", segment)
    comp_idx = [i for i, part in enumerate(parts) if part and part not in "._-"]
    if not comp_idx:
        return segment
    first, last = comp_idx[0], comp_idx[-1]
    has_extension = len(comp_idx) > 1
    for i in comp_idx:
        if has_extension and i == last:
            continue  # extension is never a hash
        if _is_hash_token(parts[i], allow_wordish=(i != first)):
            parts[i] = "{hash}"
    return "".join(parts)


def normalize_source_path(source: str | None) -> str:
    """Normalize a source-map/bundle path to a best-effort stable identity.

    ``None`` (or blank) is a null source entry -> ``NULL_SOURCE``; a caller with
    no source map at all should use ``NO_MAP`` directly (§3.5).
    """
    if source is None or not source.strip():
        return NULL_SOURCE
    authority, path = _split_scheme(source.strip())
    parts: list[str] = []
    for part in path.replace("\\", "/").split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if parts:
                parts.pop()  # never pops the authority — it is prepended below
            continue
        parts.append(_collapse_hashes_in_segment(part))
    segments = ([authority] if authority else []) + parts
    return "/".join(segments)


# ---------------------------------------------------------------------------
# Endpoint / param value normalization (§4.1, §4.3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PathParam:
    """A positional path parameter of an endpoint (identity v3).

    ``position`` (1-based ordinal among the path's ``{id}``/``{uuid}``/``{hash}`` tokens) is
    the identity; ``name`` is EVIDENCE only — the identifier the source used (``userId`` from
    a source map, ``e`` from a minified build), never hashed; ``integer`` is True when this
    sighting was a literal integer segment (``/users/42``)."""

    position: int
    name: str | None
    integer: bool


@dataclass(frozen=True)
class Endpoint:
    """A normalized endpoint. ``value`` is hashed; ``host``, ``path_params`` and
    ``dynamic_query`` are occurrence/attribute evidence (NOT hashed), so REQ-C2 base-URL
    re-resolution or a minifier rename cannot churn identity."""

    value: str
    host: str | None
    path_params: tuple[PathParam, ...] = ()
    # The URL carries a query built at runtime (``?${qs}``): its keys are unknown, so none
    # is asserted (no fake ``query:${...}`` param).
    dynamic_query: bool = False


def _decode_unreserved(text: str) -> str:
    def repl(match: re.Match[str]) -> str:
        ch = chr(int(match.group(1), 16))
        return ch if ch in _UNRESERVED else match.group(0)

    return _PCT_RE.sub(repl, text)


def template_segment(segment: str) -> str:
    """Template a single URL path segment (balanced, entropy-aware, §4.1)."""
    if _INT_RE.match(segment):
        return "{id}"
    if _UUID_RE.match(segment):
        return "{uuid}"
    if _HEX16_RE.match(segment):
        return "{hash}"
    if (
        _TOKEN_RE.match(segment)
        and any(c.isdigit() for c in segment)
        and shannon_entropy(segment) >= _SEG_TOKEN_ENTROPY
    ):
        return "{hash}"
    return segment


_VALUE_TEMPLATES = frozenset({"{id}", "{uuid}", "{hash}"})

# A `${...}` substitution is masked before any URL splitting, because its JS expression can
# hold `/`, `?`, `&` or `=` (`${ids.join("/")}`, `${q ? `?${q}` : ""}`) that must never split
# the URL. Private-use code points: urlsplit strips leading C0 controls, and these never occur
# in a real URL.
_MASK_OPEN, _MASK_CLOSE = "\ue000", "\ue001"
_MASK_RE = re.compile(f"{_MASK_OPEN}(\\d+){_MASK_CLOSE}")


def _mask_substitutions(text: str) -> tuple[str, list[str]]:
    """Replace every balanced ``${...}`` (nested templates included) with an indexed mask.
    An unbalanced one masks to the end of the text — it can't be split safely either."""
    out: list[str] = []
    subs: list[str] = []
    i, n = 0, len(text)
    while i < n:
        if not text.startswith("${", i):
            out.append(text[i])
            i += 1
            continue
        depth, j = 0, i + 1
        while j < n:
            if text[j] == "{":
                depth += 1
            elif text[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        end = min(j + 1, n)
        out.append(f"{_MASK_OPEN}{len(subs)}{_MASK_CLOSE}")
        subs.append(text[i:end])
        i = end
    return "".join(out), subs


def _unmask(text: str, subs: list[str]) -> str:
    return _MASK_RE.sub(lambda m: subs[int(m.group(1))], text)


def is_readable_name(name: str) -> bool:
    """True when ``name`` reads like a real identifier rather than a minifier mangle.
    Rejects 1-char names and vowelless 2-char names (``u``, ``e``, ``xr``, ``bt``) — the
    shapes that flood a mangled bundle — while keeping short-but-meaningful names (``id``)."""
    if not name or not name.replace("_", "").isalnum():
        return False
    if len(name) == 1:
        return False
    return len(name) != 2 or any(char in "aeiouAEIOU" for char in name)


# Value-preserving wrappers a URL substitution is commonly passed through; the identifier
# inside is the developer's name for the value.
_PASS_THROUGH_CALL = re.compile(r"(?:encodeURIComponent|encodeURI|String|Number)\((.*)\)", re.S)
_IDENTIFIER_CHAIN = re.compile(r"[A-Za-z_$][\w$]*(?:\s*\.\s*[A-Za-z_$][\w$]*)*")


def placeholder_name(substitution: str) -> str | None:
    """The identifier a ``${...}`` substitution reads, as evidence for a path param's name:
    the readable leaf of an identifier/member chain (``${item.DSStoragePath}`` ->
    ``DSStoragePath``), through ``encodeURIComponent``/``String``/... wrappers. A mangled
    name is returned as-is (``${e}`` -> ``e``: still evidence); any other expression (a call,
    a ternary) -> ``None``. Read from the source text, never invented."""
    inner = substitution[2:-1] if substitution.endswith("}") else substitution[2:]
    inner = inner.strip()
    while match := _PASS_THROUGH_CALL.fullmatch(inner):
        inner = match.group(1).strip()
    if not _IDENTIFIER_CHAIN.fullmatch(inner):
        return None
    segments = [segment.strip() for segment in inner.split(".")]
    return next((s for s in reversed(segments) if is_readable_name(s)), segments[-1])


def display_param_name(names: list[str] | tuple[str, ...]) -> str | None:
    """The most meaningful observed name for a path param: readable beats mangled, then
    longest, then alphabetical (deterministic). ``None`` when no observed name is readable —
    the caller falls back to a positional name and must NOT derive risk tags from it."""
    readable = sorted({n for n in names if is_readable_name(n)}, key=lambda n: (-len(n), n))
    return readable[0] if readable else None


def merge_path_param_evidence(*evidence_lists: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Union per-position path-param evidence (``{position, names, integer_only}`` dicts, the
    endpoint ``path_params`` attribute): names unioned, ``integer_only`` ANDed (one template
    sighting means the value is not known to be an integer). Order-independent."""
    by_position: dict[int, dict[str, Any]] = {}
    for entry in (entry for entries in evidence_lists for entry in entries):
        position = int(entry["position"])
        names = set(entry.get("names") or [])
        current = by_position.get(position)
        if current is None:
            by_position[position] = {
                "position": position,
                "names": sorted(names),
                "integer_only": bool(entry.get("integer_only")),
            }
            continue
        current["names"] = sorted(set(current["names"]) | names)
        current["integer_only"] = current["integer_only"] and bool(entry.get("integer_only"))
    return [by_position[position] for position in sorted(by_position)]


def display_endpoint_value(value: str, path_params: list[dict[str, Any]] | None) -> str:
    """The endpoint value as a reader should see it: each positional ``{id}`` named from its
    most meaningful observed identifier (``GET /users/{id}`` -> ``GET /users/{userId}``), so
    the hint the developer's name carries (IDOR on ``userId``) survives identity v3.
    Display-only — the stored value and its hash never change."""
    names = {
        int(entry["position"]): display_param_name(entry.get("names") or [])
        for entry in path_params or []
    }
    if not any(names.values()):
        return value
    masked, subs = _mask_substitutions(value)
    head, sep, query = masked.partition("?")
    ordinal = 0
    segments = head.split("/")
    for index, segment in enumerate(segments):
        if segment in _VALUE_TEMPLATES:
            ordinal += 1
            if segment == "{id}" and names.get(ordinal):
                segments[index] = "{" + str(names[ordinal]) + "}"
    return _unmask("/".join(segments) + sep + query, subs)


def _whole_placeholder(segment: str, *, holders: bool) -> bool:
    if _MASK_RE.fullmatch(segment):
        return True
    return holders and (segment == _SUSPECTED_EXPR or bool(_SUSPECTED_HOLDER.fullmatch(segment)))


def _templatize_path(
    path: str, subs: list[str], *, holders: bool = False
) -> tuple[str, tuple[PathParam, ...]]:
    """Template a MASKED path. A whole-segment placeholder AFTER a static segment becomes a
    positional ``{id}`` (identity v3: the name is evidence, not identity); a leading one is an
    unresolved base and a partial one is shape, so both stay verbatim (spec classification and
    capture correlation key on that literal ``${``). ``holders`` adds the unconfirmed lane's
    ``:holder``/``EXPR`` tokens to the placeholder set."""
    out: list[str] = []
    params: list[PathParam] = []
    seen_static = False
    for raw in (segment for segment in path.split("/") if segment != ""):
        if _whole_placeholder(raw, holders=holders):
            if not seen_static:
                out.append(_unmask(raw, subs))
                continue
            mask = _MASK_RE.fullmatch(raw)
            name = placeholder_name(subs[int(mask.group(1))]) if mask else raw.lstrip(":")
            params.append(PathParam(len(params) + 1, name, integer=False))
            out.append("{id}")
            continue
        if _MASK_RE.search(raw):
            out.append(_unmask(raw, subs))
            seen_static = True
            continue
        templated = template_segment(_decode_unreserved(raw))
        if templated in _VALUE_TEMPLATES:
            params.append(PathParam(len(params) + 1, None, integer=templated == "{id}"))
        else:
            seen_static = True
        out.append(templated)
    return ("/" + "/".join(out) if out else "/"), tuple(params)


def _query_keys(masked_query: str) -> tuple[list[str], bool]:
    """Sorted, de-duped param *names* only; array keys canonicalized (§4.1, L1). A key that
    is (or contains) a runtime substitution is not a name — dropped, and reported as dynamic."""
    if not masked_query:
        return [], False
    keys: set[str] = set()
    dynamic = False
    for key, _value in parse_qsl(masked_query, keep_blank_values=True):
        if _MASK_RE.search(key):
            dynamic = True
            continue
        name = _ARRAY_KEY_RE.sub("", key)
        if name:  # drop empty keys so `?=v&x=1` == `?x=1` (review LOW-6)
            keys.add(name)
    return sorted(keys), dynamic


def url_query_names(url: str) -> list[str]:
    """The literal query param names in a URL, in first-seen order — substitution-safe (a
    ``?`` inside ``${...}`` is not the query) and without runtime-built keys."""
    masked, _subs = _mask_substitutions(url)
    query = masked.split("?", 1)[1] if "?" in masked else ""
    seen: dict[str, None] = {}
    for key, _value in parse_qsl(query.split("#", 1)[0], keep_blank_values=True):
        if key and not _MASK_RE.search(key):
            seen.setdefault(key, None)
    return list(seen)


def _normalize(method: str, url: str, *, holders: bool) -> tuple[Endpoint, str] | None:
    masked, subs = _mask_substitutions(url)
    if holders and masked.startswith("//"):
        # A leading `//` (a protocol-relative literal or a `_collapse_url` artifact) would let
        # urlsplit eat the first PATH segment as a host; the unconfirmed lane collapses it.
        masked = "/" + masked.lstrip("/")
    split = urlsplit(masked)
    path = split.path or "/"
    if holders and _path_static_segment_count(_unmask(path, subs)) < 1:
        return None
    template, params = _templatize_path(path, subs, holders=holders)
    keys, dynamic = _query_keys(split.query)
    verb = method.strip().upper()
    operation = f"{verb} {template}" if verb else template
    value = operation + (f"?{'&'.join(keys)}" if keys else "")
    host = _unmask(split.hostname, subs) if split.hostname else None
    return Endpoint(value=value, host=host, path_params=params, dynamic_query=dynamic), operation


def endpoint_operation(method: str, url: str) -> str:
    """`METHOD + templated path` (no query, no host) — the param's owning op."""
    normalized = _normalize(method, url, holders=False)
    assert normalized is not None  # the confirmed lane never rejects
    return normalized[1]


def normalize_endpoint(method: str, url: str) -> Endpoint:
    """Normalize an endpoint call into its hashed ``value`` + occurrence ``host``."""
    normalized = _normalize(method, url, holders=False)
    assert normalized is not None  # the confirmed lane never rejects
    return normalized[0]


# Placeholder token forms in a `_collapse_url` skeleton (mirrors recon.findings._jsast's
# `${...}` template subs, `:holder` dynamic-leaf names, and the `EXPR` sentinel). Redefined
# here — NOT imported — to keep this module stdlib-only + dependency-free (see module docstring:
# _jsast pulls in tree-sitter). The token contract is stable (part of the collapse-URL shape).
_SUSPECTED_TEMPLATE_SUB = re.compile(r"\$\{[^}]*\}")
_SUSPECTED_HOLDER = re.compile(r":[A-Za-z_][A-Za-z0-9_]*")
_SUSPECTED_EXPR = "EXPR"


def _path_static_segment_count(path: str) -> int:
    """Count path segments carrying LITERAL (non-placeholder) text.

    A placeholder is a ``${...}`` sub, a ``:holder``, or the bare ``EXPR`` sentinel; a segment
    is static iff stripping those leaves a non-empty residue. ``/inbox/subjects`` -> 2;
    ``/:handle/inbox_views`` -> 1 (``inbox_views``); ``${e.id}/${t}`` -> 0. This gates the
    suspected-endpoint lane: >=1 static segment is a real (if partial) endpoint; an
    all-placeholder path is not (it stays unconfirmed)."""
    masked, _subs = _mask_substitutions(path)
    count = 0
    for segment in masked.split("/"):
        residue = _SUSPECTED_HOLDER.sub("", _MASK_RE.sub("", segment)).strip()
        if residue and residue != _SUSPECTED_EXPR:
            count += 1
    return count


def normalize_suspected_endpoint(method: str, url: str) -> Endpoint | None:
    """Normalize a SUSPECTED sink skeleton (a generic-client call, or an unresolved sink whose
    collapsed URL still carries a real path) into a counted endpoint ``value`` + ``host``,
    mirroring :func:`normalize_endpoint` — or ``None`` when the path is all-placeholder /
    pure-variable, which stays in the unconfirmed lane. A whole-segment ``${...}``/``:holder``/
    ``EXPR`` after a static segment becomes the same positional ``{id}`` as the confirmed lane;
    leading/partial placeholders survive verbatim.

    Guards the ``urlsplit`` ``//``-authority swallow: a leading ``//`` (a protocol-relative
    literal or a ``_collapse_url`` artifact) would otherwise eat the first PATH segment as a
    host, so it is collapsed to a single ``/`` before splitting — no path segment is lost, while
    a real ``http(s)://`` URL still splits its host out normally into the occurrence attribute."""
    normalized = _normalize(method, url, holders=True)
    return normalized[0] if normalized is not None else None


def normalize_param_value(operation: str, location: str, name: str) -> str:
    """`operation + location:name` (§4.3). Build ``operation`` via
    ``endpoint_operation``. A path param's ``name`` is its 1-based position (identity v3)."""
    return f"{operation} {location}:{name}"


def operation_of_endpoint_value(value: str) -> str:
    """The operation (`METHOD + templated path`) of a stored ENDPOINT finding value.

    An endpoint value is ``operation`` + an optional ``?query`` suffix
    (see :func:`normalize_endpoint`), so the operation is everything before the first ``?``
    that is not inside a verbatim ``${...}``."""
    masked, subs = _mask_substitutions(value)
    return _unmask(masked.split("?", 1)[0], subs)


def operation_of_param_value(value: str) -> str:
    """The operation of a stored PARAM finding value.

    A param value is ``f"{operation} {location}:{name}"`` (see
    :func:`normalize_param_value`); ``location:name`` is the final space-separated
    token outside any ``${...}``, so the operation is everything before it."""
    masked, subs = _mask_substitutions(value)
    return _unmask(masked.rsplit(" ", 1)[0], subs)


def rewrite_v2_value(finding_type: str, value: str) -> str | None:
    """Map a stored identity-v2 ``value`` to its v3 value — what the v2→v3 migration uses to
    re-key session state (triage, spec status, base-URL picks, threat citations) so it
    survives the identity change. ``None`` = the finding has no v3 counterpart (a fake
    runtime-built query key). Types whose value did not change map to themselves."""
    if finding_type in ("endpoint", "endpoint_suspected"):
        method, _sep, rest = value.partition(" ")
        if value.startswith("/"):
            method, rest = "", value
        if finding_type == "endpoint":
            return normalize_endpoint(method, rest).value
        suspected = normalize_suspected_endpoint(method, rest)
        return suspected.value if suspected is not None else value
    if finding_type == "param":
        masked, subs = _mask_substitutions(value)
        head, _sep, token = masked.rpartition(" ")
        if _MASK_RE.search(token):
            return None
        operation = _unmask(head, subs)
        method, _sep, path = operation.partition(" ")
        if operation.startswith("/"):
            method, path = "", operation
        return f"{endpoint_operation(method, path)} {token}"
    return value


# ---------------------------------------------------------------------------
# Secret value normalization (§4.2)
# ---------------------------------------------------------------------------


def strip_secret_delimiters(raw_token: str) -> str:
    return raw_token.strip(_SECRET_DELIMS)


def provider_for_rule(rule_id: str) -> str:
    """Map an engine rule id to a stable provider slug (pinned map, review M1).

    Kingfisher ids are ``kingfisher.<provider>.<n>`` (e.g. ``kingfisher.stripe.2``),
    so the provider is the SECOND segment — the leading ``kingfisher`` token is the
    engine name, not the provider. Non-Kingfisher ids fall back to the leading
    token. The pinned map overrides both for any id needing a hand-fixed slug."""
    key = rule_id.strip().lower()
    if key in _PROVIDER_BY_RULE:
        return _PROVIDER_BY_RULE[key]
    segments = [s for s in re.split(r"[./_\-]", key) if s]
    if not segments:
        return "unknown"
    if segments[0] == "kingfisher":
        return segments[1] if len(segments) >= 2 else "unknown"
    return segments[0]


def normalize_secret_value(raw_token: str, rule_id: str) -> str:
    """`provider:sha256(token)` — the raw value is never in the hash cleartext."""
    provider = provider_for_rule(rule_id)
    token = strip_secret_delimiters(raw_token)
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return f"{provider}:{digest}"


# ---------------------------------------------------------------------------
# GraphQL value normalization
# ---------------------------------------------------------------------------


def normalize_graphql_value(
    kind: str, name: str | None, on_type: str | None = None, body_digest: str | None = None
) -> str:
    """Path-free identity ``value`` for a GraphQL definition finding.

    - fragment: ``fragment <name> on <on_type>``
    - named operation: ``<kind> <name>`` (e.g. ``mutation CreateOrganizationApiToken``)
    - anonymous operation: ``<kind> · <body_digest>`` — a short print_ast digest of the op
      body, supplied by the caller (this module stays graphql-core-free). It disambiguates
      spread-only anonymous ops, whose top-level FieldNode set is empty and would otherwise all
      collapse to one identity (review fix #2).

    GraphQL op names are conventionally unique, so two ops sharing a name but with different
    selections deliberately merge to one finding (only ``attributes.fields`` differs) — the same
    accepted identity behavior as the endpoint same-path merge.
    """
    if kind == "fragment":
        return f"fragment {name} on {on_type}"
    if name:
        return f"{kind} {name}"
    return f"{kind} · {body_digest or 'anonymous'}"


# ---------------------------------------------------------------------------
# Hashing (§5)
# ---------------------------------------------------------------------------


def _canonical(obj: dict[str, object]) -> bytes:
    # allow_nan=False: NaN/Inf are non-standard JSON and would break cross-process
    # stability. default=str: tolerate bytes/other by a deterministic repr rather
    # than raising (review LOW-4).
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str
    ).encode("utf-8")


# The identity-algorithm version, hashed INTO every finding_hash so a change to
# the algorithm produces a disjoint hash space from prior runs. v1 hashed
# {type, value, path}; v2 drops path (it moved to occurrences — Starbucks-QA dedup,
# the C1 treatment applied to path). Bumping this is the rollout lever: new runs
# hash under the current version and dedupe; runs analyzed under an older version
# keep their stored hashes untouched (no backfill — new-runs-only). ``record_finding``
# uses this to refuse an out-of-band re-extract onto an older-version run, whose
# additive write would otherwise land a divergent hash (a duplicate finding).
FINDING_HASH_VERSION = 3


def finding_hash(finding_type: str, value: str) -> str:
    """The stable REQ-D3 identity: sha256 over canonical {v, type, value}.

    ``value`` is NFC-normalized so two builds differing only in Unicode
    composition form do not churn in the D5 diff (review LOW-5). Source ``path``
    is intentionally absent (v2) — it lives on occurrences (see module docstring)."""
    return hashlib.sha256(
        _canonical(
            {
                "v": FINDING_HASH_VERSION,
                "type": finding_type,
                "value": unicodedata.normalize("NFC", value),
            }
        )
    ).hexdigest()


def occurrence_hash(**fields: object) -> str:
    """Idempotency key for one sighting — canonical over its volatile identifying
    fields (raw value, host, source-path variant, offsets), so a retry re-emits
    the same occurrence_hash and the append is a no-op. Pass offsets as ``int``
    (an ``int`` and an equal ``float`` canonicalize differently)."""
    return hashlib.sha256(_canonical(dict(fields))).hexdigest()
