"""REQ-D3 finding-hash normalization — the stable identity of a finding.

Turns a raw extracted finding into a canonical ``finding_hash`` computed over
*stable fields only* (``type + value + normalized path``), so a retry with
slightly different evidence yields the same hash (REQ-D3). That hash keys the
exactly-once outbox write (REQ-A3) and the partial-aware diff (REQ-D5).

Everything here is pure and dependency-free (stdlib only) so it is trivially
testable and safe to call inside a staging transaction. Mutable per-sighting
detail (host, offsets, line/col, evidence) is deliberately NOT hashed — it lives
on occurrence rows so a normalization merge is visible, never silently dropped
(REQ-C2 honesty). Full rationale + the design review outcome:
``docs/req-d3-finding-hash-normalization.md``.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from dataclasses import dataclass
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
_UNRESERVED = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
)
_PCT_RE = re.compile(r"%([0-9A-Fa-f]{2})")

# Secret match boundaries are engine-defined; strip surrounding delimiters an
# engine may have captured (a trailing quote, brackets, whitespace) before
# hashing so v1 and v2 of a rule agree (REQ-D3 stability, review M2). `.`/`-`/`_`
# `=`/`+`/`/` are token-legal and are NOT stripped.
_SECRET_DELIMS = "\"'`()[]{}<>,;: \t\r\n"

# Kingfisher ships 950+ evolving rules; pin the rule-id -> provider map to a
# known ruleset version so an upstream rule rename cannot churn identity
# (review M1). Unknown rules fall back to a sanitized leading token.
KINGFISHER_RULES_VERSION = "1.x"
_PROVIDER_BY_RULE: dict[str, str] = {
    "stripe.live_secret_key": "stripe",
    "stripe.live_restricted_key": "stripe",
    "aws.access_key_id": "aws",
    "google.api_key": "google",
    "slack.incoming_webhook": "slack",
    "firebase.api_key": "firebase",
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
class Endpoint:
    """A normalized endpoint. ``value`` is hashed; ``host`` is occurrence-only
    (NOT hashed) so REQ-C2 base-URL re-resolution cannot churn identity."""

    value: str
    host: str | None


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


def _templatize_path(path: str) -> str:
    segments = [
        template_segment(_decode_unreserved(seg)) for seg in path.split("/") if seg != ""
    ]
    return "/" + "/".join(segments) if segments else "/"


def _normalize_query(query: str) -> str:
    """Sorted, de-duped param *names* only; array keys canonicalized (§4.1, L1)."""
    if not query:
        return ""
    keys: set[str] = set()
    for key, _value in parse_qsl(query, keep_blank_values=True):
        name = _ARRAY_KEY_RE.sub("", key)
        if name:  # drop empty keys so `?=v&x=1` == `?x=1` (review LOW-6)
            keys.add(name)
    return "&".join(sorted(keys))


def endpoint_operation(method: str, url: str) -> str:
    """`METHOD + templated path` (no query, no host) — the param's owning op."""
    path = urlsplit(url).path or "/"
    return f"{method.strip().upper()} {_templatize_path(path)}"


def normalize_endpoint(method: str, url: str) -> Endpoint:
    """Normalize an endpoint call into its hashed ``value`` + occurrence ``host``."""
    split = urlsplit(url)
    query = _normalize_query(split.query)
    value = endpoint_operation(method, url) + (f"?{query}" if query else "")
    return Endpoint(value=value, host=split.hostname)


def normalize_param_value(operation: str, location: str, name: str) -> str:
    """`operation + location:name` (§4.3). Build ``operation`` via
    ``endpoint_operation``."""
    return f"{operation} {location}:{name}"


# ---------------------------------------------------------------------------
# Secret value normalization (§4.2)
# ---------------------------------------------------------------------------

def strip_secret_delimiters(raw_token: str) -> str:
    return raw_token.strip(_SECRET_DELIMS)


def provider_for_rule(rule_id: str) -> str:
    """Map an engine rule id to a stable provider slug (pinned map, review M1)."""
    key = rule_id.strip().lower()
    if key in _PROVIDER_BY_RULE:
        return _PROVIDER_BY_RULE[key]
    lead = re.split(r"[./_\-]", key, maxsplit=1)[0]
    return lead or "unknown"


def normalize_secret_value(raw_token: str, rule_id: str) -> str:
    """`provider:sha256(token)` — the raw value is never in the hash cleartext."""
    provider = provider_for_rule(rule_id)
    token = strip_secret_delimiters(raw_token)
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return f"{provider}:{digest}"


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


def finding_hash(finding_type: str, value: str, path: str) -> str:
    """The stable REQ-D3 identity: sha256 over canonical {type, value, path}.

    ``value``/``path`` are NFC-normalized so two builds differing only in Unicode
    composition form do not churn in the D5 diff (review LOW-5)."""
    return hashlib.sha256(
        _canonical(
            {
                "type": finding_type,
                "value": unicodedata.normalize("NFC", value),
                "path": unicodedata.normalize("NFC", path),
            }
        )
    ).hexdigest()


def occurrence_hash(**fields: object) -> str:
    """Idempotency key for one sighting — canonical over its volatile identifying
    fields (raw value, host, source-path variant, offsets), so a retry re-emits
    the same occurrence_hash and the append is a no-op. Pass offsets as ``int``
    (an ``int`` and an equal ``float`` canonicalize differently)."""
    return hashlib.sha256(_canonical(dict(fields))).hexdigest()
