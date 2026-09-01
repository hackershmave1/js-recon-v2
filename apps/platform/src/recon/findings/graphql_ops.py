"""GraphQL definition extraction (enrichment slice C + the GraphQL-findings slice).

Locate GraphQL documents embedded in a JS bundle and parse them with ``graphql-core``.
Two consumers, deliberately kept apart:

- the OpenAPI export — ``collect_operations`` yields the operations a client sends (no
  fragments, no location), serialized into the ``x-recon-graphql-operations`` annotation.
  UNCHANGED from enrichment slice C so the exported doc stays byte-for-byte identical.
- first-class GraphQL findings — ``collect_definitions`` yields located operations AND
  fragment definitions (``FindingType.GRAPHQL``), each carrying its JS call-site line/offset
  so the workspace can link an operation to its place in the bundle.

Two ways a bundle carries a GraphQL document, both handled by ``_located_documents``:

- a call ``gql`...``` / ``graphql(`...`)`` (the ``graphql-tag`` idiom). The
  tree-sitter-javascript grammar has NO ``tagged_template_expression`` node (design-gate
  M2): the tagged form ``gql`...``` parses as a ``call_expression`` whose ``arguments``
  FIELD is the ``template_string`` itself, while the plain call ``graphql(`...`)`` wraps
  the template in an ordinary ``arguments`` node — so the two forms are read differently.
- an object-literal body key ``{query|mutation|subscription: `...`}`` — a
  GraphQL-over-HTTP request body assembled inline.

Parsing is offline and lossy-tolerant: ``parse_definitions`` runs ``graphql.parse`` and a
malformed or ``${...}``-interpolated template is a SOFT MISS (returns ``()``), never failing
analyze — the same opportunistic invariant the source-map recovery holds (T2). This module is
pure (no DB, no storage, no network); analyze persists the result and the export serializes it.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from graphql import (
    FieldNode,
    FragmentDefinitionNode,
    GraphQLSyntaxError,
    OperationDefinitionNode,
    parse,
)
from tree_sitter import Node, Parser

from recon.findings._jsast import _PARSER, _args, _object_pairs, _string_value, _text, _walk


@dataclass(frozen=True)
class GraphQLOperation:
    """One GraphQL operation (export-only advisory metadata; operations, no fragments/location).

    ``fields`` is the operation's TOP-LEVEL selection field names only — enough to name what it
    touches without asserting a schema. Frozen so identical operations dedupe.
    """

    op_type: str  # "query" | "mutation" | "subscription"
    name: str | None  # operation name, or None for an anonymous/shorthand operation
    fields: tuple[str, ...]  # top-level selection field names


@dataclass(frozen=True)
class GraphQLDefinition:
    """One located GraphQL definition — a query/mutation/subscription operation OR a fragment.

    ``fields`` is the TOP-LEVEL selection field names. ``on_type`` is a fragment's type
    condition (``fragment X on User`` → ``"User"``), ``None`` for an operation. Location is the
    document's JS CALL-SITE (line/col/byte offsets); every definition inside one multi-definition
    document shares that site — precise per-definition offsets are a fast-follow. Frozen so
    identical definitions at one site dedupe, while the same document at a distinct call site
    stays a distinct located definition (→ a separate occurrence downstream).
    """

    kind: str  # "query" | "mutation" | "subscription" | "fragment"
    name: str | None  # operation/fragment name, or None for an anonymous operation
    fields: tuple[str, ...]  # top-level selection field names
    on_type: str | None = None  # fragment type condition; None for an operation
    line: int | None = None  # 1-based line of the call-site in the JS bundle
    col: int | None = None  # 0-based column of the call-site
    offset_start: int | None = None  # byte offsets of the document node in the JS bundle
    offset_end: int | None = None


# JS callees whose string argument is a GraphQL document (the graphql-tag family). Only real JS
# identifiers can match `_call_document`'s `fn.type == "identifier"` gate, so `gql`/`graphql`.
_GRAPHQL_CALLEES = frozenset({"gql", "graphql"})
# Object-literal keys whose value is a GraphQL document (a GraphQL-over-HTTP request body).
_GRAPHQL_BODY_KEYS = frozenset({"query", "mutation", "subscription"})


@dataclass(frozen=True)
class _DocLoc:
    """A located GraphQL document's JS call-site position."""

    line: int
    col: int
    offset_start: int
    offset_end: int


def _loc(node: Node) -> _DocLoc:
    row, col = node.start_point
    return _DocLoc(line=row + 1, col=col, offset_start=node.start_byte, offset_end=node.end_byte)


def _located_documents(source: str | bytes, parser: Parser = _PARSER) -> list[tuple[str, _DocLoc]]:
    """Every embedded GraphQL document with its JS call-site location, in source order.

    A non-GraphQL string that happens to sit under one of the body keys is harmless — it simply
    fails to parse downstream and is soft-missed.
    """
    data = source.encode("utf-8") if isinstance(source, str) else source
    tree = parser.parse(data)
    documents: list[tuple[str, _DocLoc]] = []
    for node in _walk(tree.root_node):
        if node.type == "call_expression":
            document = _call_document(node)
            if document is not None:
                documents.append((document, _loc(node)))
        elif node.type == "object":
            for value, document in _object_document_nodes(node):
                documents.append((document, _loc(value)))
    return documents


def extract_documents(source: str | bytes, parser: Parser = _PARSER) -> tuple[str, ...]:
    """Every embedded GraphQL document SOURCE (text only), in source order (export back-compat)."""
    return tuple(document for document, _ in _located_documents(source, parser))


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


def _object_document_nodes(node: Node) -> list[tuple[Node, str]]:
    """(value node, GraphQL document) for each ``{query|mutation|subscription: ...}`` body key.

    The value NODE is returned so the caller can record where in the JS the document sits.
    """
    documents: list[tuple[Node, str]] = []
    for key, value in _object_pairs(node).items():
        if key in _GRAPHQL_BODY_KEYS:
            document = _string_value(value)
            if document is not None:
                documents.append((value, document))
    return documents


def parse_definitions(document: str) -> tuple[GraphQLDefinition, ...]:
    """Parse one document into its operations AND fragment definitions, or ``()`` if unparseable.

    A malformed, ``${...}``-interpolated, or pathologically nested template is a SOFT MISS →
    ``()`` (spec trap T2): analyze must never fail on a GraphQL template it cannot parse. Top-level
    selections are filtered to ``FieldNode`` before reading ``.name`` (spec S2 — an inline fragment
    / fragment spread has no field name and would otherwise ``AttributeError``). Location is left
    unset here; :func:`collect_definitions` attaches the document's call-site.
    """
    try:
        document_node = parse(document)
    except (GraphQLSyntaxError, RecursionError):
        # RecursionError: graphql-core's parse() is recursive-descent with no depth limit, so a
        # pathologically nested document (a crafted gql`` template in hostile fetched JS) exhausts
        # the Python recursion limit. Soft-miss it exactly like a syntax error — analyze must never
        # fail on a template it cannot parse (T2), and one asset's parse must never DoS the run.
        return ()
    definitions: list[GraphQLDefinition] = []
    for definition in document_node.definitions:
        if isinstance(definition, OperationDefinitionNode):
            definitions.append(
                GraphQLDefinition(
                    kind=definition.operation.value,
                    name=definition.name.value if definition.name else None,
                    fields=tuple(
                        selection.name.value
                        for selection in definition.selection_set.selections
                        if isinstance(selection, FieldNode)
                    ),
                )
            )
        elif isinstance(definition, FragmentDefinitionNode):
            definitions.append(
                GraphQLDefinition(
                    kind="fragment",
                    name=definition.name.value,
                    fields=tuple(
                        selection.name.value
                        for selection in definition.selection_set.selections
                        if isinstance(selection, FieldNode)
                    ),
                    on_type=definition.type_condition.name.value,
                )
            )
    return tuple(definitions)


def parse_operations(document: str) -> tuple[GraphQLOperation, ...]:
    """Operations only (no fragments), export shape — feeds the OpenAPI annotation (back-compat)."""
    return tuple(
        GraphQLOperation(op_type=definition.kind, name=definition.name, fields=definition.fields)
        for definition in parse_definitions(document)
        if definition.kind != "fragment"
    )


def collect_operations(source: str | bytes) -> tuple[GraphQLOperation, ...]:
    """Every GraphQL operation embedded in a JS bundle, deduped order-stably (export entrypoint).

    Locate the documents, parse each (soft-missing the unparseable), flatten, and drop exact
    duplicates (a bundle often repeats the same ``gql`` document). Location-free by design — the
    run-level ``graphql`` export artifact is built from it, and the OpenAPI annotation is unchanged.
    """
    operations: list[GraphQLOperation] = []
    for document in extract_documents(source):
        operations.extend(parse_operations(document))
    return tuple(dict.fromkeys(operations))  # order-stable dedup (frozen dataclass is hashable)


def collect_definitions(source: str | bytes) -> tuple[GraphQLDefinition, ...]:
    """Every located GraphQL definition (operations + fragments) in a bundle, deduped order-stably.

    Each definition inherits its document's JS call-site location; the same document at a distinct
    call site yields a distinct located definition (→ a separate occurrence downstream). The
    analyze stage writes these as located ``FindingType.GRAPHQL`` findings.
    """
    definitions: list[GraphQLDefinition] = []
    for document, loc in _located_documents(source):
        for definition in parse_definitions(document):
            definitions.append(
                replace(
                    definition,
                    line=loc.line,
                    col=loc.col,
                    offset_start=loc.offset_start,
                    offset_end=loc.offset_end,
                )
            )
    return tuple(dict.fromkeys(definitions))  # order-stable dedup (frozen dataclass is hashable)
