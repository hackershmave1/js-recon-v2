"""Webpack module scopes for the base-environment pre-pass (DEBT D80).

A webpack chunk is a registry of module FUNCTIONS (``{389:(e,t,n)=>{…}, 412:(e,t,n)=>{…}}``),
and the minifier reuses the same short names (``t``, ``o``, ``s``) in every one of them. Each
module is its own function scope, so a sibling module's ``o`` is a different binding from this
module's ``o``. Counting bindings per FILE (the pre-D80 rule) poisoned every such name and left
real endpoints unresolved. This module finds the module functions and answers "which module is
this node in" in O(1) per node, so the poison count and the collected bindings can be kept per
module.

Soundness rules (REQ-C2 0-FP), applied by :mod:`recon.findings._base_env`:

- A name DECLARED in a module is local to it: it is ambiguous only if it is bound more than once
  in that module or also bound outside every module (a shadow is still ambiguous, as before).
- A name a module only ASSIGNS (``o = "/b"`` with no declaration) writes the enclosing / global
  binding, so that write counts against the outer region, which every module can read.
- Code outside every module keeps the whole-file rule unchanged, so an outer binding a module
  reads is one no module rebinds.

Only the OUTERMOST module function opens a scope: a numeric-keyed function nested inside a module
is part of that module's scope (coarser, never wrong).
"""

from __future__ import annotations

from collections import ChainMap
from collections.abc import Container, Iterator, Mapping, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field, replace

from tree_sitter import Node

from recon.findings._dataflow import resolve_local_consts
from recon.findings._jsast import BaseEnv, InferredWrapper, ModuleScope

# The node types that can declare a webpack module (see `module_function`).
_MODULE_OPENERS = frozenset({"method_definition", "pair"})


def module_function(node: Node) -> Node | None:
    """The function node when ``node`` declares a webpack module, else ``None``.

    The three registry forms webpack emits: a ``method_definition`` (``{389(e,t,n){…}}``) and a
    ``pair`` whose value is an ``arrow_function`` (``{389:(e,t,n)=>{…}}``) or a
    ``function_expression`` (``{389:function(e,t,n){…}}``) — numeric key, a body, and an
    identifier 3rd parameter (its ``__webpack_require__``). O(1): counts, never lists, params.
    """
    function: Node | None
    if node.type == "method_definition":
        key, function = node.child_by_field_name("name"), node
    elif node.type == "pair":
        function = node.child_by_field_name("value")
        if function is None or function.type not in ("arrow_function", "function_expression"):
            return None
        key = node.child_by_field_name("key")
    else:
        return None
    if key is None or key.type != "number" or function.child_by_field_name("body") is None:
        return None
    params = function.child_by_field_name("parameters")
    if params is None or params.named_child_count < 3:
        return None
    require = params.named_child(2)
    return function if require is not None and require.type == "identifier" else None


def module_require_name(function: Node) -> str:
    """The ``__webpack_require__`` parameter name of a :func:`module_function` result."""
    params = function.child_by_field_name("parameters")
    require = params.named_child(2) if params is not None else None
    return (require.text or b"").decode("utf-8", "replace") if require is not None else ""


def is_require_alias(value: Node, require_name: str | None) -> bool:
    """``value`` is ``require(<numeric id>)`` for this module's require parameter."""
    if not require_name or value.type != "call_expression":
        return False
    fn = value.child_by_field_name("function")
    args = value.child_by_field_name("arguments")
    return (
        fn is not None
        and fn.type == "identifier"
        and fn.text == require_name.encode()
        and args is not None
        and args.named_child_count == 1
        and (arg := args.named_child(0)) is not None
        and arg.type == "number"
    )


def member_write_key(left: Node | None) -> str | None:
    """``"X.P"`` for an assignment target ``X.P`` or ``X["P"]`` (``X`` an identifier), else None.
    Reads only the identifier / property / string-index leaves, so it is O(1) per assignment."""
    if left is None or left.type not in ("member_expression", "subscript_expression"):
        return None
    obj = left.child_by_field_name("object")
    if obj is None or obj.type != "identifier":
        return None
    if left.type == "member_expression":
        prop = left.child_by_field_name("property")
        name = prop.text if prop is not None and prop.type == "property_identifier" else None
    else:
        index = left.child_by_field_name("index")
        name = None
        if index is not None and index.type == "string" and index.named_child_count <= 1:
            fragment = index.named_child(0)
            name = fragment.text if fragment is not None else b""
    if name is None:
        return None
    return f"{(obj.text or b'').decode('utf-8', 'replace')}.{name.decode('utf-8', 'replace')}"


class ScopeCursor:
    """Which module span a node starts in, for a walk in non-decreasing ``start_byte`` order
    (``_jsast._walk``'s pre-order). Spans are disjoint and sorted, so one forward pointer makes
    the whole walk O(nodes + spans)."""

    def __init__(self, spans: Sequence[tuple[int, int]]) -> None:
        self._spans = spans
        self._index = 0

    def index_at(self, start_byte: int) -> int:
        """The span index ``start_byte`` falls in, or ``-1`` outside every module."""
        spans = self._spans
        while self._index < len(spans) and spans[self._index][1] <= start_byte:
            self._index += 1
        if self._index < len(spans) and spans[self._index][0] <= start_byte:
            return self._index
        return -1


class ScopePoison(Container[str]):
    """The ambiguous names inside one module, without copying the outer counts into each module
    (a copy per module would be O(modules x outer names) — a DoS on a crafted chunk).

    A name the module DECLARES is ambiguous when its ``local`` count (declarations + the module's
    writes to it) plus the ``outer`` region's count (outer bindings + writes modules make to
    names they do not declare) exceeds one. A name it does not declare is free — it reads the
    enclosing / global binding — so the whole-file ``total`` decides, exactly as before D80."""

    def __init__(
        self, local: Mapping[str, int], outer: Mapping[str, int], total: Mapping[str, int]
    ) -> None:
        self._local = local
        self._outer = outer
        self._total = total

    def __contains__(self, name: object) -> bool:
        if not isinstance(name, str):
            return False
        if name in self._local:
            return self._local[name] + self._outer.get(name, 0) > 1
        return self._total.get(name, 0) > 1


class LayeredSet(AbstractSet[str]):
    """A module's names first, then the outer region's: a read-only union with no copy."""

    def __init__(self, local: AbstractSet[str], outer: AbstractSet[str]) -> None:
        self._local = local
        self._outer = outer

    def __contains__(self, name: object) -> bool:
        return name in self._local or name in self._outer

    def __iter__(self) -> Iterator[str]:
        yield from self._local
        yield from (name for name in self._outer if name not in self._local)

    def __len__(self) -> int:
        return sum(1 for _ in self)


@dataclass
class BindingCounts:
    """How often each name is bound, file-wide and per webpack module (DEBT D80).

    ``total`` is every mark in the file (the pre-D80 rule, still used outside every module).
    ``spans`` are the outermost webpack module functions; ``local[i]`` counts what module ``i``
    declares (plus its writes to those names); ``outer`` counts the outer region plus every write
    a module makes to a name it does NOT declare — that write lands on the enclosing / global
    binding, which every module can read (see recon.findings._module_scopes)."""

    total: dict[str, int] = field(default_factory=dict)
    spans: list[tuple[int, int]] = field(default_factory=list)
    outer: dict[str, int] = field(default_factory=dict)
    local: list[dict[str, int]] = field(default_factory=list)
    requires: list[str] = field(default_factory=list)  # each module's require parameter name

    def poison(self, scope: int) -> Container[str]:
        if scope < 0:
            return {name for name, count in self.total.items() if count > 1}
        return ScopePoison(self.local[scope], self.outer, self.total)


@dataclass
class ScopeBindings:
    """What one scope (a webpack module, or the region outside every module) binds, gathered in
    the single `_base_env.collect_base_env` walk. Receivers, `window.*` and `axios.defaults` stay
    file-wide: they describe shared runtime objects, not lexical bindings."""

    poisoned: Container[str]
    instances: dict[str, str | None] = field(default_factory=dict)
    # Candidate LOCAL consts (single-unshadowed name -> value node), resolved after the walk.
    raw_consts: dict[str, Node] = field(default_factory=dict)
    # QA #4: clients/wrappers the bundle proves under any (minified) name.
    inferred_instances: set[str] = field(default_factory=set)
    wrappers: dict[str, InferredWrapper | None] = field(default_factory=dict)

    # The module's `__webpack_require__` parameter, and the names bound once to `require(<id>)`.
    require_name: str | None = None
    require_aliases: set[str] = field(default_factory=set)

    def _is_proven(self, key: str, member_writes: AbstractSet[str]) -> bool:
        """A plain name always; a member-chain factory `t.A` only when `t` is this module's own
        `t = require(<id>)` alias (bound once) and nothing in the FILE writes `t.A`/`t["A"]` —
        so `t.A` is "export A of module <id>", which webpack defines getter-only (`require.d`).
        Outside every module there is no require alias, so no member factory is claimed."""
        if "." not in key:
            return True
        receiver = key.split(".", 1)[0]
        return receiver in self.require_aliases and key not in member_writes

    def proven_instances(self, member_writes: AbstractSet[str]) -> dict[str, str | None]:
        return {k: v for k, v in self.instances.items() if self._is_proven(k, member_writes)}

    def proven_inferred(self, member_writes: AbstractSet[str]) -> frozenset[str]:
        return frozenset(k for k in self.inferred_instances if self._is_proven(k, member_writes))

    def proven_wrappers(self) -> dict[str, InferredWrapper]:
        return {key: w for key, w in self.wrappers.items() if w is not None}


@dataclass(frozen=True)
class OuterLayer:
    """The outer region's resolved bindings, kept as concrete dicts so each module can layer
    over them with a `ChainMap` (no per-module copy — that would be O(modules x outer))."""

    instances: dict[str, str | None]
    const_prefixes: dict[str, str]
    inferred_instances: frozenset[str]
    wrappers: dict[str, InferredWrapper]


def module_scope(
    span: tuple[int, int],
    bindings: ScopeBindings,
    outer_env: BaseEnv,
    outer: OuterLayer,
    has_with: bool,
    member_writes: AbstractSet[str],
) -> ModuleScope:
    """One module's env: its own bindings first, then the outer region's. The two never share a
    key: an outer binding is kept only when its name is bound once in the WHOLE file, so no
    module declares it (a module-local name can never be answered by an outer value)."""
    window = outer_env.window_globals
    local_consts = (
        {} if has_with else resolve_local_consts(bindings.raw_consts, window, outer.const_prefixes)
    )
    env = replace(
        outer_env,
        instances=ChainMap(bindings.proven_instances(member_writes), outer.instances),
        const_prefixes=ChainMap(local_consts, outer.const_prefixes),
        inferred_instances=LayeredSet(
            bindings.proven_inferred(member_writes), outer.inferred_instances
        ),
        wrappers=ChainMap(bindings.proven_wrappers(), outer.wrappers),
    )
    return ModuleScope(start=span[0], end=span[1], env=env, local_consts=local_consts)


def with_imports(
    env: BaseEnv,
    cross_module_consts: Mapping[str, str] | None,
    webpack_members: Mapping[str, Mapping[str, str]] | None,
) -> BaseEnv:
    """`env` — and every module scope in it — with the run-level cross-module / webpack-member
    maps added. Both are file-level facts, so every scope sees the same ones."""
    if not cross_module_consts and not webpack_members:
        return env
    consts = dict(cross_module_consts or {})
    members = {k: dict(v) for k, v in (webpack_members or {}).items()}

    def add(scope_env: BaseEnv) -> BaseEnv:
        return replace(
            scope_env,
            cross_module_consts=consts or scope_env.cross_module_consts,
            webpack_members=members or scope_env.webpack_members,
        )

    scopes = tuple(replace(scope, env=add(scope.env)) for scope in env.scopes)
    return replace(add(env), scopes=scopes)
