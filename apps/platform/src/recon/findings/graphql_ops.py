"""GraphQL operation extraction (enrichment slice C) — export-only, no findings.

Locate GraphQL documents embedded in a JS bundle and parse them with ``graphql-core``
so the OpenAPI export can annotate the operations a client sends. A GraphQL operation is
NOT an HTTP endpoint — it rides a single POST to one ``/graphql`` route — so it is never
emitted as an OpenAPI ``paths`` entry or as an endpoint/param finding (locked decision 1);
it surfaces only in the export's ``x-recon-graphql-operations`` annotation.

Two ways a bundle carries a GraphQL document, both handled by ``extract_documents``:

- a call ``gql`...``` / ``graphql(`...`)`` (the ``graphql-tag`` idiom). The
  tree-sitter-javascript grammar has NO ``tagged_template_expression`` node (design-gate
  M2): the tagged form ``gql`...``` parses as a ``call_expression`` whose ``arguments``
  FIELD is the ``template_string`` itself, while the plain call ``graphql(`...`)`` wraps
  the template in an ordinary ``arguments`` node — so the two forms are read differently.
- an object-literal body key ``{query: `...`}`` / ``{mutation: `...`}`` — a
  GraphQL-over-HTTP request body assembled inline.

Parsing is offline and lossy-tolerant: ``parse_operations`` runs ``graphql.parse`` and a
malformed or ``${...}``-interpolated template is a SOFT MISS (returns ``()``), never
failing analyze — the same opportunistic invariant the source-map recovery holds (T2).
This module is pure (no DB, no storage, no network); analyze persists the result and the
export serializes it.
"""

from __future__ import annotations

from dataclasses import dataclass

from graphql import FieldNode, GraphQLSyntaxError, OperationDefinitionNode, parse
from tree_sitter import Node, Parser

from recon.findings._jsast import _PARSER, _args, _object_pairs, _string_value, _text, _walk


@dataclass(frozen=True)
class GraphQLOperation:
    """One GraphQL operation located in a bundle (export-only advisory metadata).

    ``fields`` is the operation's TOP-LEVEL selection field names only — enough to name
    what it touches without asserting a schema. Frozen so identical operations dedupe.
    """

    op_type: str  # "query" | "mutation" | "subscription"
    name: str | None  # operation name, or None for an anonymous/shorthand operation
    fields: tuple[str, ...]  # top-level selection field names


# JS callees whose string argument is a GraphQL document (the graphql-tag family).
_GRAPHQL_CALLEES = frozenset({"gql", "graphql", "graphql-tag"})
# Object-literal keys whose value is a GraphQL document (a GraphQL-over-HTTP request body).
_GRAPHQL_BODY_KEYS = frozenset({"query", "mutation"})


def extract_documents(source: str | bytes, parser: Parser = _PARSER) -> tuple[str, ...]:
    """Every embedded GraphQL document SOURCE in the JS ``source``, in source order.

    Returns the raw document text (``gql``/``graphql()`` call arguments and
    ``{query|mutation: ...}`` body values); :func:`parse_operations` turns each into
    structured operations. A non-GraphQL string that happens to sit under one of those
    keys is harmless — it simply fails to parse and is soft-missed downstream.
    """
    data = source.encode("utf-8") if isinstance(source, str) else source
    tree = parser.parse(data)
    documents: list[str] = []
    for node in _walk(tree.root_node):
        if node.type == "call_expression":
            document = _call_document(node)
            if document is not None:
                documents.append(document)
        elif node.type == "object":
            documents.extend(_object_documents(node))
    return tuple(documents)


def _call_document(call: Node) -> str | None:
    """The GraphQL document string of a ``gql`...``` / ``graphql(`...`)`` call, else None.

    Handles BOTH tree-sitter shapes (design-gate M2): the tagged form's ``arguments`` FIELD
    is the ``template_string`` directly; the plain-call form wraps it in an ``arguments``
    node read via ``_args``. ``_string_value`` strips the quotes/backticks and keeps any
    ``${...}`` verbatim (so an interpolated template stays visibly unparseable → soft miss).
    """
    fn = call.child_by_field_name("function")
    if fn is None or fn.type != "identifier" or _text(fn) not in _GRAPHQL_CALLEES:
        return None
    arguments = call.child_by_field_name("arguments")
    if arguments is None:
        return None
    if arguments.type == "template_string":  # tagged form: gql`...`
        return _string_value(arguments)
    args = _args(call)  # plain-call form: graphql(`...`)
    return _string_value(args[0]) if args else None


def _object_documents(node: Node) -> list[str]:
    """GraphQL documents carried by an object-literal body key (``{query|mutation: ...}``)."""
    documents: list[str] = []
    for key, value in _object_pairs(node).items():
        if key in _GRAPHQL_BODY_KEYS:
            document = _string_value(value)
            if document is not None:
                documents.append(document)
    return documents


def parse_operations(document: str) -> tuple[GraphQLOperation, ...]:
    """Parse one GraphQL document into its operations, or ``()`` if it cannot be parsed.

    A malformed or ``${...}``-interpolated template is a SOFT MISS → ``()`` (spec trap T2):
    analyze must never fail on a GraphQL template it cannot parse, mirroring the source-map
    recovery's opportunistic invariant. Top-level selections are filtered to ``FieldNode``
    before reading ``.name`` (spec S2 — an inline fragment / fragment spread has no field
    name and would otherwise ``AttributeError``).
    """
    try:
        document_node = parse(document)
    except GraphQLSyntaxError:
        return ()
    operations: list[GraphQLOperation] = []
    for definition in document_node.definitions:
        if not isinstance(definition, OperationDefinitionNode):
            continue  # fragment / type-system definitions are not operations
        fields = tuple(
            selection.name.value
            for selection in definition.selection_set.selections
            if isinstance(selection, FieldNode)
        )
        operations.append(
            GraphQLOperation(
                op_type=definition.operation.value,
                name=definition.name.value if definition.name else None,
                fields=fields,
            )
        )
    return tuple(operations)


def collect_operations(source: str | bytes) -> tuple[GraphQLOperation, ...]:
    """Every GraphQL operation embedded in a JS bundle, deduped order-stably.

    Locate the documents, parse each (soft-missing the unparseable), flatten, and drop
    exact duplicates (a bundle often repeats the same ``gql`` document). The analyze
    stage's single entrypoint — the run-level ``graphql`` export artifact is built from it.
    """
    operations: list[GraphQLOperation] = []
    for document in extract_documents(source):
        operations.extend(parse_operations(document))
    return tuple(dict.fromkeys(operations))  # order-stable dedup (frozen dataclass is hashable)
