"""Param risk-tagging (enrichment slice A) — pure, stdlib-only.

Classify an already-extracted request param by its NAME into zero or more advisory
risk tags (``auth``/``admin``/``idor``/``flag``) so the OpenAPI export (and a planned
recon report) can point a reviewer at the params worth poking first. Name-only heuristic: it never
sees a value, asserts nothing, and errs toward silence — an over-eager tag is noise, a
missing tag is honest.

The one load-bearing invariant is TOKENIZATION, not substring matching (spec trap T1):
match whole word tokens, so ``valid``/``grid``/``android`` are NOT ``idor`` and ``width``
is not an ``id``. Mirrors the dependency-free rule-module shape of ``findings.wrappers``
(no tree-sitter, no DB) so it stays trivially unit-testable.
"""

from __future__ import annotations

import re

# Split a param name into lowercase word tokens on separators AND camelCase / acronym
# boundaries: "userId"->[user,id], "api_key"->[api,key], "APIToken"->[api,token],
# "nextPageToken"->[next,page,token], "HTTPSProxy"->[https,proxy].
_SPLIT = re.compile(r"[_\-\s.]+|(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")

# Whole-token auth terms. "token"/"session" are matched separately, behind FP guards.
_AUTH = frozenset(
    {
        "auth",
        "authorization",
        "authentication",
        "jwt",
        "bearer",
        "secret",
        "credential",
        "credentials",
        "otp",
        "mfa",
        "password",
        "passwd",
        "passphrase",
        "apikey",
        "sessionid",
    }
)
# Cursor words that make a bare "token" a pagination cursor, not a credential (spec S1):
# nextToken / pageToken / continuationToken are NOT auth.
_PAGINATION = frozenset({"next", "page", "prev", "previous", "cursor", "continuation", "offset"})
_ADMIN = frozenset({"admin", "administrator", "superuser", "sudo", "privileged", "impersonate"})
# idor is whole-token only (spec M1): the tokenizer already yields userId->[user,id] and
# account_id->[account,id], and rejects valid/grid/android/solid — never "ends-with id".
_IDOR = frozenset({"id", "uuid", "guid"})
# Feature-toggle words. NOTE: the spec's bare is/has/can/allow prefix rule is deliberately
# dropped — it tags UI state (isLoading, hasError) as security flags, more noise than signal.
# isEnabled/featureDisabled are still caught via the enabled/disabled tokens below.
_FLAG = frozenset({"flag", "feature", "toggle", "enabled", "disabled", "beta", "experimental"})


def _tokens(name: str) -> list[str]:
    return [token.lower() for token in _SPLIT.split(name) if token]


def classify_param(name: str) -> tuple[str, ...]:
    """Zero or more risk tags for a param NAME. Sorted, deduped, order-stable.

    Returns ``()`` for the common (untagged) case. Tags: ``auth`` (a credential rides
    this param), ``admin`` (a privilege/impersonation control), ``idor`` (an object
    identifier — access-control worth testing), ``flag`` (a feature toggle).
    """
    if not name:
        return ()
    tokens = _tokens(name)
    token_set = set(tokens)
    tags: set[str] = set()

    if token_set & _AUTH:
        tags.add("auth")
    if "api" in token_set and "key" in token_set:  # api_key / apiKey / x-api-key
        tags.add("auth")
    if "token" in token_set and not (token_set & _PAGINATION):
        tags.add("auth")
    if "session" in token_set and "storage" not in token_set:
        tags.add("auth")

    if token_set & _ADMIN:
        tags.add("admin")
    if token_set & _IDOR:
        tags.add("idor")
    if token_set & _FLAG:
        tags.add("flag")

    return tuple(sorted(tags))
