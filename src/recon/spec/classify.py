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

Pure, stdlib-only, no DB/network -- both `classify_operation` (this
module's own §5.3 decision-order dispatcher, below) and this module's
tests call these three functions directly.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from recon.findings.extract import HTTP_METHODS
from recon.findings.normalize import operation_of_endpoint_value
from recon.spec.ingest import DocumentedOp

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


@dataclass(frozen=True)
class Classification:
    """The §5.3 decision-order verdict for one client-finding operation.

    `matched_operation` is the documented op's own raw `"METHOD /path"` --
    braces intact, never the wildcarded compare-key -- so a human reviewing
    `finding_spec_status` sees the actual spec entry that was matched, not
    its canonicalized shape."""

    status: str  # "documented" | "shadow" | "unresolved"
    reason: str
    matched_operation: str | None


def _doc_operation(doc: DocumentedOp) -> str:
    """Render a `DocumentedOp` as `"METHOD /path"` -- the same shape
    `compare_key` takes on the client side (design §5.1: one compare-key,
    both sides), and also the human-readable form stored as
    `matched_operation`."""
    return f"{doc.method} {doc.path}"


def _key_parts(key: str) -> tuple[str, list[str]]:
    """Split a `compare_key()` output (`"METHOD /wildcarded/path"`) into
    `(METHOD, path-segments)`. `compare_key` already uppercased the method
    and stripped the query, so this is a cheap re-split, not a second pass
    through `_method_and_path`/`operation_of_endpoint_value`."""
    method, _, path = key.partition(" ")
    return method, _segments(path)


def _is_proper_suffix(shorter: list[str], longer: list[str]) -> bool:
    """True if `shorter`'s segments are a genuine, non-empty trailing run of
    `longer`'s -- one direction of step 4's bidirectional check (design
    §5.3, gate B2).

    Requires `shorter` to be non-empty: without this guard, a bare `/` root
    path (zero segments) would satisfy "proper suffix of everything" the
    moment any documented path is longer, silently swallowing every
    undocumented root-path finding into `unresolved`. The suffix rule exists
    to catch a genuinely SHARED trailing fragment (e.g. "search"); an empty
    fragment shares no literal content with anything, so it must not count."""
    if not shorter or len(shorter) >= len(longer):
        return False
    return longer[len(longer) - len(shorter) :] == shorter


def classify_operation(operation: str, documented: Sequence[DocumentedOp]) -> Classification:
    """Bucket `operation` against `documented` per design §5.3 -- FIRST MATCH
    WINS, and suffix-verify (step 4) runs before either shadow verdict (gate
    B2): a client path missing its base (e.g. `/search` for a spec's
    `/location/address/search`) must never be misclassified as an
    undocumented verb or path just because a *different* documented op
    happens to share its bare tail."""
    if is_non_http(operation):
        return Classification("unresolved", "non-http", None)
    if is_partial(operation):
        return Classification("unresolved", "partial", None)

    method, segments = _key_parts(compare_key(operation))
    doc_entries = [(doc, *_key_parts(compare_key(_doc_operation(doc)))) for doc in documented]

    # Step 3: exact compare-key match (same method, same wildcarded path).
    for doc, doc_method, doc_segments in doc_entries:
        if doc_method == method and doc_segments == segments:
            return Classification("documented", "documented", _doc_operation(doc))

    # Step 4: proper-suffix match, either direction -- MUST precede both
    # shadow branches below (gate B2's whole point).
    for doc, _doc_method, doc_segments in doc_entries:
        if _is_proper_suffix(segments, doc_segments) or _is_proper_suffix(doc_segments, segments):
            return Classification("unresolved", "suffix-verify", _doc_operation(doc))

    # Step 5: same wildcarded path, different method.
    for doc, doc_method, doc_segments in doc_entries:
        if doc_segments == segments and doc_method != method:
            return Classification("shadow", "undocumented-method", _doc_operation(doc))

    # Step 6: complete + statically-certain (not partial), no match at all.
    if not is_partial(operation):
        return Classification("shadow", "undocumented-path", None)

    # Step 7: unreachable given step 2's earlier return on the same,
    # side-effect-free `is_partial(operation)` check -- kept explicit for a
    # literal 1:1 correspondence with the design's 7 numbered branches (§5.3)
    # rather than silently relying on that coupling holding forever.
    return Classification("unresolved", "unresolved", None)


@dataclass(frozen=True)
class SpecSummary:
    """The §5.4/§6.4 run-scoped summary: one bucket count per `Classification`
    status, plus `base_url_incompleteness_ratio` -- the self-audit signal
    (design §5.4, gate N7). See `summarize`'s docstring for why this ratio is
    NOT the write-up's literal "suffix-shadow ratio" and what it measures
    instead."""

    documented: int
    shadow: int
    unresolved: int
    suffix_verify: int
    base_url_incompleteness_ratio: float


def summarize(classifications: Iterable[Classification]) -> SpecSummary:
    """Bucket-count `classifications` and compute the run's self-audit ratio.

    AS-BUILT DIVERGENCE from the design write-up (§5.4): the write-up defines
    the ratio as "the fraction of this run's `shadow` findings whose path is
    a proper suffix of some documented path". That metric is uncomputable by
    construction, not merely often zero -- `classify_operation`'s step 4
    (§5.3, gate B2) diverts EVERY proper-suffix match to
    `unresolved`/`suffix-verify` BEFORE either shadow branch (steps 5-6) ever
    runs, so a `Classification` with `status == "shadow"` can never carry
    `reason == "suffix-verify"`. A ratio defined over that intersection is
    always 0/0.

    This computes the same self-audit INTENT (flag when the shadow list is
    likely inflated by missing base-URL resolution) over the data that CAN
    vary: of the run's unmatched "shadow candidates" -- everything the
    suffix safety net actually caught (`suffix_verify`) plus everything left
    in `shadow` after it ran -- the fraction the net rescued:

        base_url_incompleteness_ratio = suffix_verify / (shadow + suffix_verify)

    A high ratio means the suffix net is rescuing most of what would
    otherwise look undocumented, i.e. base-URL resolution (REQ-C2) is likely
    incomplete and the remaining `shadow` entries are suspect. `0.0` covers
    both "nothing to be suspicious of" and the zero-denominator case (no
    `shadow` and no `suffix_verify` at all) identically -- there is nothing
    for the ratio to flag either way.

    `suffix_verify` only counts rows with BOTH `status == "unresolved"` AND
    `reason == "suffix-verify"` -- matching `status` alone would double-count
    against `unresolved`'s own tally, and matching `reason` alone would (per
    the paragraph above) accept a `status == "shadow"` row that real
    `classify_operation` output can never produce."""
    documented = shadow = unresolved = suffix_verify = 0
    for c in classifications:
        if c.status == "documented":
            documented += 1
        elif c.status == "shadow":
            shadow += 1
        elif c.status == "unresolved":
            unresolved += 1
            if c.reason == "suffix-verify":
                suffix_verify += 1

    denominator = shadow + suffix_verify
    ratio = suffix_verify / denominator if denominator else 0.0
    return SpecSummary(documented, shadow, unresolved, suffix_verify, ratio)
