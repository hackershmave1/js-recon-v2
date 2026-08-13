"""Colocated tests for GraphQL operation extraction (enrichment slice C).

Pure unit tests — parse JS/GraphQL strings, assert the located operations. No infra.
"""

from __future__ import annotations

from recon.findings.graphql_ops import (
    GraphQLOperation,
    collect_operations,
    extract_documents,
    parse_operations,
)

# --- extract_documents: the two call shapes (design-gate M2) + object body keys --- #


def test_gql_tagged_template_document_is_located():
    # gql`...` parses as call_expression whose `arguments` FIELD is the template_string.
    docs = extract_documents("const q = gql`query Me { me { id } }`;")
    assert docs == ("query Me { me { id } }",)


def test_graphql_plain_call_document_is_located():
    # graphql(`...`) wraps the template in a normal `arguments` node (read via _args).
    docs = extract_documents("const q = graphql(`mutation Go { go { ok } }`);")
    assert docs == ("mutation Go { go { ok } }",)


def test_object_query_body_key_is_located():
    docs = extract_documents("const body = { query: `query Baz { a b }`, variables: {} };")
    assert docs == ("query Baz { a b }",)


def test_object_mutation_body_key_is_located():
    docs = extract_documents("const body = { mutation: 'mutation M { go }' };")
    assert docs == ("mutation M { go }",)


def test_non_graphql_call_is_ignored():
    # A same-shaped call to some other tag/function is not a GraphQL source.
    assert extract_documents("const x = styled`color: red;`;") == ()
    assert extract_documents('fetch("/api/users");') == ()


# --- parse_operations: structure, S2 field filtering, T2 soft-miss ------------- #


def test_parse_named_query_top_level_fields():
    (op,) = parse_operations("query Me { me { id } profile }")
    assert op == GraphQLOperation(op_type="query", name="Me", fields=("me", "profile"))


def test_parse_anonymous_operation_has_no_name():
    (op,) = parse_operations("{ health }")
    assert op == GraphQLOperation(op_type="query", name=None, fields=("health",))


def test_parse_multi_operation_document():
    ops = parse_operations("query A { a } mutation B { b } subscription C { c }")
    assert [(o.op_type, o.name) for o in ops] == [
        ("query", "A"),
        ("mutation", "B"),
        ("subscription", "C"),
    ]


def test_parse_filters_inline_fragments_to_field_nodes():
    # S2: an inline fragment / fragment spread in the top-level selection has no `.name`;
    # only FieldNode selections contribute field names (no AttributeError).
    (op,) = parse_operations("query Q { me ... on User { extra } ...frag }")
    assert op.fields == ("me",)


def test_parse_ignores_fragment_definitions():
    # A bare fragment definition is not an operation.
    assert parse_operations("fragment F on User { id }") == ()


def test_parse_malformed_document_is_soft_miss():
    # T2: never raise on a malformed document — return () so analyze cannot fail.
    assert parse_operations("query { unterminated ") == ()


def test_parse_interpolated_template_is_soft_miss():
    # A ${...}-interpolated template survives extraction verbatim and is unparseable → ().
    assert parse_operations("query Foo { me { ${sel} } }") == ()


def test_parse_deeply_nested_document_is_soft_miss():
    # T2 hardening: graphql-core's recursive-descent parse() has no depth limit, so a crafted
    # deeply-nested gql`` template raises RecursionError (NOT GraphQLSyntaxError). It must still
    # soft-miss — parsing one asset's hostile JS may never crash the analyze stage (a per-asset DoS).
    deep = "query D " + "{ a " * 6000 + "}" * 6000
    assert parse_operations(deep) == ()


# --- collect_operations: end-to-end locate → parse → dedup --------------------- #


def test_collect_operations_end_to_end_across_forms():
    source = """
        const A = gql`query Me { me { id } }`;
        const B = graphql(`mutation Go { go }`);
        const body = { query: `query List { items }` };
    """
    ops = collect_operations(source)
    assert {(o.op_type, o.name) for o in ops} == {
        ("query", "Me"),
        ("mutation", "Go"),
        ("query", "List"),
    }


def test_collect_operations_dedupes_identical_documents():
    # The same document repeated (a shared gql`` constant) collapses to one operation.
    source = "const a = gql`query Me { me { id } }`; const b = gql`query Me { me { id } }`;"
    assert collect_operations(source) == (
        GraphQLOperation(op_type="query", name="Me", fields=("me",)),
    )


def test_collect_operations_soft_misses_unparseable_without_dropping_others():
    source = "const bad = gql`query { ${x} }`; const ok = gql`query Ok { ok }`;"
    assert collect_operations(source) == (
        GraphQLOperation(op_type="query", name="Ok", fields=("ok",)),
    )


def test_collect_operations_empty_source():
    assert collect_operations("const x = 1;") == ()
