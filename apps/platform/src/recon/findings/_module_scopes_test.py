"""Webpack module scopes (DEBT D80): detection, the per-node cursor, and the poison rule."""

from __future__ import annotations

import pytest

from recon.findings._base_env import _binding_counts, collect_base_env
from recon.findings._jsast import _PARSER, _walk
from recon.findings._module_scopes import ScopeCursor, ScopePoison, module_function
from recon.findings.extract import extract


def _modules(src: str) -> int:
    root = _PARSER.parse(src.encode()).root_node
    return sum(1 for node in _walk(root) if module_function(node) is not None)


@pytest.mark.parametrize(
    "registry",
    [
        "{1(e,t,n){var o=1},2(e,t,n){var o=2}}",  # method shorthand
        "{1:(e,t,n)=>{var o=1},2:(e,t,n)=>{var o=2}}",  # webpack 5 production arrow
        "{1:function(e,t,n){var o=1},2:function(e,t,n){var o=2}}",  # function expression
    ],
)
def test_each_webpack_registry_form_is_a_module(registry):
    assert _modules(f"x.push([[1],{registry}])") == 2


def test_a_function_that_is_not_a_module_opens_no_scope():
    assert _modules('{a:(e,t,n)=>1, 1:(e,t)=>1, 2:(e,t,{n})=>1, "3":(e,t,n)=>1}') == 0


def test_only_the_outermost_module_opens_a_scope():
    counts = _binding_counts(_PARSER.parse(b"x({1:(e,t,n)=>{y({2:(a,b,c)=>{var o}})}})").root_node)
    assert len(counts.spans) == 1


def test_the_cursor_walks_disjoint_spans_forward():
    cursor = ScopeCursor([(10, 20), (30, 40)])
    assert [cursor.index_at(b) for b in (0, 10, 19, 20, 29, 30, 39, 40)] == [
        -1,
        0,
        0,
        -1,
        -1,
        1,
        1,
        -1,
    ]


def test_a_declared_name_counts_its_module_plus_the_outer_region():
    poison = ScopePoison(local={"o": 1, "p": 2}, outer={"o": 1}, total={"o": 5, "q": 2, "r": 1})
    assert "o" in poison  # declared here and bound outside too: a shadow stays ambiguous
    assert "p" in poison  # bound twice in this module
    assert "q" in poison  # free here: the whole-file count decides
    assert "r" not in poison
    assert 3 not in poison


def test_a_write_to_an_undeclared_name_lands_on_the_outer_region():
    src = b"var o=1;x({1:(e,t,n)=>{o=2},2:(e,t,n)=>{var p=1;p=3}})"
    counts = _binding_counts(_PARSER.parse(src).root_node)
    assert counts.outer["o"] == 2  # the outer `var o` + module 1's write to it
    assert counts.local[0] == {"e": 1, "t": 1, "n": 1}
    assert counts.local[1]["p"] == 2  # a module's write to its own name stays local


def test_a_file_without_modules_keeps_the_whole_file_env():
    src = b'const api="/v1";fetch(api+"/x")'
    env = collect_base_env(_PARSER.parse(src).root_node, src)
    assert env.scopes == ()
    assert dict(env.const_prefixes) == {"api": "/v1"}


def test_every_registry_form_resolves_its_own_base():
    for module in ("{}(e,t,n){{{}}}", "{}:function(e,t,n){{{}}}"):
        body_a = 'var o="/a";fetch(o+"/x")'
        body_b = "var o=n(1);o.y()"
        src = f"x([[1],{{{module.format(1, body_a)},{module.format(2, body_b)}}}])"
        assert [(e.method, e.url) for e in extract(src).endpoints] == [("GET", "/a/x")], src
