"""Colocated tests for the hardened spec-ingest guards (design §4, §4.1 —
gates B4/B5). The brief's 5 required cases, plus one bonus proving the
cyclic-$ref concern named in §4.1 is actually handled. Pure unit tests — no
infra, no network (that's the point: gate B4 forbids the module from ever
making one).

NOTE: the brief's swagger2 fixture is corrected here to add the (mandatory)
`info` object and a non-empty `responses` entry — openapi-spec-validator
0.9.0 rejects the original one-liner on both counts (verified empirically:
'info' is a required property, then {} should be non-empty for `responses`).
Adding them doesn't change what the test proves (basePath + method/path
resolution) — it just makes the fixture a schema-valid Swagger 2.0 document
so the real `validate()` gate, not just our own scan, actually runs over it.
"""

from __future__ import annotations

import pytest

from recon.spec.ingest import SpecError, ingest_spec

OPENAPI3 = b"""openapi: 3.0.0
info: {title: t, version: '1'}
servers: [{url: '/api/{v}', variables: {v: {default: v2}}}]
paths: {/pets: {get: {responses: {'200': {description: ok}}}}}
"""

SWAGGER2 = (
    b'{"swagger":"2.0","info":{"title":"t","version":"1"},"basePath":"/v1",'
    b'"paths":{"/x":{"post":{"responses":{"200":{"description":"ok"}}}}}}'
)


def test_openapi3_resolves_server_variable():
    spec = ingest_spec(OPENAPI3)
    assert spec.format == "openapi-3"
    assert ("GET", "/api/v2/pets") in [(o.method, o.path) for o in spec.documented]


def test_swagger2_basepath():
    spec = ingest_spec(SWAGGER2)
    assert spec.format == "swagger-2"
    assert ("POST", "/v1/x") in [(o.method, o.path) for o in spec.documented]


def test_invalid_spec_raises():
    with pytest.raises(SpecError):
        ingest_spec(b"not a spec")


def test_external_ref_rejected():
    with pytest.raises(SpecError):
        ingest_spec(
            b'{"openapi":"3.0.0","info":{"title":"t","version":"1"},'
            b'"paths":{"/x":{"$ref":"file:///etc/passwd"}}}'
        )


def test_yaml_alias_bomb_rejected():
    bomb = b"a: &a [1,1]\nb: &b [*a,*a]\nc: [*b,*b]\npaths: {}"
    with pytest.raises(SpecError):
        ingest_spec(bomb)  # anchors/aliases denied by the hardened loader


# --- fix round 1: review finding on Task 4 -- a deeply-nested-but-tiny YAML
# body (no anchors, well under the _MAX_SOURCE_BYTES/_MAX_NODES caps) blows
# PyYAML's composer recursion *during* `yaml.load` inside `_parse`, before
# `_check_bounds`'s own depth check ever gets a chance to run against the
# already-parsed structure. Must surface as `SpecError`, not a raw
# `RecursionError` escaping `ingest_spec` uncaught. ---
def test_deeply_nested_yaml_raises_specerror_not_recursionerror():
    depth = 5000  # comfortably past CPython's default 1000-frame recursion
    # limit (empirically confirmed to raise RecursionError at this depth in
    # this environment). The "[" * N + "]" * N flow-sequence nests one level
    # per character pair with no anchors and no exponential blow-up -- the
    # "tiny but deep" shape the finding describes, distinct from the
    # size/breadth bomb _check_bounds already guards against.
    bomb = b"paths:\n  " + b"[" * depth + b"]" * depth
    with pytest.raises(SpecError):
        ingest_spec(bomb)


# --- fix round 2: JSON deep-nesting gap (mirrors fix round 1 for YAML) --
# The C-accelerated json.loads hits the recursion limit on deeply-nested
# valid JSON (e.g. "[" * 5000 + "]" * 5000) and raises RecursionError during
# parse, before _check_bounds' depth scan runs. Must surface as SpecError,
# not escape uncaught. ---
def test_deeply_nested_json_raises_specerror_not_recursionerror():
    depth = 5000
    # Syntactically valid JSON but pathologically deep; json.loads raises
    # RecursionError during parsing, before _check_bounds runs against the
    # already-parsed structure.
    bomb = ("[" * depth + "]" * depth).encode()
    with pytest.raises(SpecError):
        ingest_spec(bomb)


# --- bonus (beyond the brief's 5): design §4.1 names cyclic in-document $ref
# as a required gate B4 mitigation ("handled with a visited-set"). Our own
# $ref scan never dereferences (it only reads the literal string, so a
# logical cycle can't make it recurse), and openapi-spec-validator's resolver
# (jsonschema_path / referencing) supports recursive schemas natively —
# verified empirically below to return in well under a second rather than
# hang or blow the stack, so no bespoke cycle-tracking code was added (that
# would just reinvent what the dependency already does correctly).
def test_self_referential_schema_does_not_hang():
    cyclic = (
        b'{"openapi":"3.0.0","info":{"title":"t","version":"1"},'
        b'"paths":{"/nodes":{"get":{"responses":{"200":{"description":"ok",'
        b'"content":{"application/json":{"schema":'
        b'{"$ref":"#/components/schemas/Node"}}}}}}}},'
        b'"components":{"schemas":{"Node":{"type":"object","properties":'
        b'{"name":{"type":"string"},"children":{"type":"array","items":'
        b'{"$ref":"#/components/schemas/Node"}}}}}}}'
    )
    spec = ingest_spec(cyclic)
    assert spec.format == "openapi-3"
    assert ("GET", "/nodes") in [(o.method, o.path) for o in spec.documented]
