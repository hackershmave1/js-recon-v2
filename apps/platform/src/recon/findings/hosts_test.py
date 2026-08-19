"""Hermetic tests for the pure host-inventory roll-up (DEBT D26).

No DB/network: `_aggregate_hosts` takes plain rows, so the host-universe union,
normalization, scope classification, and the endpoints-unattributed honesty
counter are all exercised in the fast lane.
"""

from __future__ import annotations

from recon.findings.hosts import HostRow, HostsView, _aggregate_hosts


def _agg(
    *,
    asset_urls: list[str] | None = None,
    endpoint_occurrences: list[tuple[str | None, str]] | None = None,
    suspected_occurrences: list[tuple[str | None, str]] | None = None,
    route_occurrences: list[tuple[str | None, str]] | None = None,
    tech_hosts: list[str] | None = None,
    declared_hosts: list[str] | None = None,
    scope_hosts: list[str] | None = None,
    allow_local: bool = False,
) -> HostsView:
    return _aggregate_hosts(
        "r1",
        asset_urls or [],
        endpoint_occurrences or [],
        suspected_occurrences or [],
        route_occurrences or [],
        tech_hosts or [],
        declared_hosts or [],
        scope_hosts or [],
        allow_local=allow_local,
    )


def _row(view, host: str) -> HostRow:
    return next(r for r in view.hosts if r.host == host)


def test_unions_all_sources_and_rolls_up_counts():
    view = _agg(
        asset_urls=[
            "https://acme.io/a.js",
            "https://api.acme.io/b.js",
            "https://api.acme.io/c.js",
        ],
        endpoint_occurrences=[
            ("api.acme.io", "h1"),  # attributed, in-scope subdomain
            ("cdn.evil.com", "h2"),  # attributed, out of scope
            (None, "h3"),  # relative path -> no host -> unattributed
        ],
        tech_hosts=["acme.io"],
        declared_hosts=["https://declared.acme.io"],
        scope_hosts=["acme.io"],
    )
    # universe = union of every source, deduped
    assert [r.host for r in view.hosts] == [
        "acme.io",
        "api.acme.io",
        "cdn.evil.com",
        "declared.acme.io",
    ]
    assert view.count == 4
    assert view.in_scope == 3  # acme.io, api.acme.io, declared.acme.io (subdomains)
    assert view.endpoints_unattributed == 1  # h3

    acme = _row(view, "acme.io")
    assert (acme.assets, acme.endpoints, acme.techs) == (1, 0, 1)
    assert acme.in_scope and not acme.declared

    api = _row(view, "api.acme.io")
    assert (api.assets, api.endpoints, api.techs) == (2, 1, 0)
    assert api.in_scope

    evil = _row(view, "cdn.evil.com")
    assert (evil.assets, evil.endpoints, evil.techs) == (0, 1, 0)
    assert not evil.in_scope

    declared = _row(view, "declared.acme.io")
    assert declared.declared and declared.in_scope
    assert (declared.assets, declared.endpoints, declared.techs) == (0, 0, 0)


def test_empty_run_is_zeroed_not_errored():
    view = _agg()
    assert view.count == 0 and view.in_scope == 0
    assert view.endpoints_unattributed == 0 and view.hosts == []
    assert view.suspected_unattributed == 0


def test_suspected_lane_rolls_up_separately_from_confirmed():
    # Suspected-backend occurrences (endpoint_generic / endpoint_unresolved) roll up
    # into their OWN per-host column + suspected_unattributed, leaving the confirmed
    # endpoints count and endpoints_unattributed untouched — the reconciliation the
    # Overview "Endpoints" card depends on. A host with BOTH lanes counts each once.
    view = _agg(
        endpoint_occurrences=[("api.acme.io", "e1"), (None, "e2")],  # 1 resolved, 1 host-less
        suspected_occurrences=[
            ("api.acme.io", "s1"),  # suspected on a host that also has a confirmed endpoint
            ("guess.acme.io", "s2"),  # suspected-only host -> its own row
            (None, "s3"),  # host-less suspected -> suspected_unattributed
        ],
        scope_hosts=["acme.io"],
    )
    # Confirmed lane is unchanged by the suspected occurrences.
    assert _row(view, "api.acme.io").endpoints == 1
    assert view.endpoints_unattributed == 1  # only e2, never s3
    # Suspected lane is tallied separately (no double-count against endpoints).
    assert _row(view, "api.acme.io").suspected == 1
    guess = _row(view, "guess.acme.io")
    assert guess.suspected == 1 and guess.endpoints == 0  # suspected-only host
    assert view.suspected_unattributed == 1  # s3


def test_route_lane_rolls_up_separately_and_adds_route_only_hosts():
    # page_route (client-nav / doc-link) hosts get their OWN column and DO enter the
    # universe — the Hosts page shows EVERY discovered host (Starbucks QA #5) — but never
    # touch the confirmed `endpoints` or `suspected` counts. A host-less route (a relative
    # /path) is same-origin nav, not an unknown host, so it adds no row and no counter.
    view = _agg(
        endpoint_occurrences=[("api.acme.io", "e1")],
        route_occurrences=[
            ("about.acme.io", "r1"),  # route-only in-scope host -> its own row
            ("api.acme.io", "r2"),  # a host that also has a confirmed endpoint
            ("github.com", "r3"),  # out-of-scope referenced host is still listed
            (None, "r4"),  # relative route -> dropped, no phantom row
        ],
        scope_hosts=["acme.io"],
    )
    # The confirmed + suspected lanes are untouched by the routes.
    assert _row(view, "api.acme.io").endpoints == 1
    assert _row(view, "api.acme.io").suspected == 0
    assert view.endpoints_unattributed == 0
    # Routes are tallied on their own column, per host.
    assert _row(view, "api.acme.io").routes == 1
    about = _row(view, "about.acme.io")
    assert (about.routes, about.endpoints, about.suspected) == (1, 0, 0)
    assert about.in_scope
    gh = _row(view, "github.com")
    assert gh.routes == 1 and not gh.in_scope
    # The host-less route added no row (a host inventory has no entry for same-origin nav).
    assert [r.host for r in view.hosts] == ["about.acme.io", "api.acme.io", "github.com"]


def test_suspected_only_host_enters_the_universe():
    # A host known ONLY from a suspected call still gets a row (it is discovered
    # attack surface) and is scope-classified like any other host.
    view = _agg(suspected_occurrences=[("api.evil.com", "s1")], scope_hosts=["acme.io"])
    assert view.count == 1
    row = _row(view, "api.evil.com")
    assert (row.assets, row.endpoints, row.suspected, row.techs) == (0, 0, 1, 0)
    assert not row.in_scope


def test_normalizes_case_and_trailing_dot_and_drops_non_hosts():
    # "ACME.io." (upper + FQDN root dot) folds with "acme.io" into ONE host; an
    # empty source and a non-web-scheme capture asset (a vm://<hash> eval'd script,
    # DEBT D24) each contribute NO host row (no per-script pseudo-host flood).
    view = _agg(
        asset_urls=[
            "https://ACME.io./x.js",
            "",
            "vm://0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        ],
        tech_hosts=["acme.io"],
        endpoint_occurrences=[("", "h1")],
    )
    assert [r.host for r in view.hosts] == ["acme.io"]
    acme = _row(view, "acme.io")
    assert acme.assets == 1 and acme.techs == 1
    # the "" occurrence host resolves to no host -> h1 is an unattributed endpoint
    assert acme.endpoints == 0 and view.endpoints_unattributed == 1


def test_endpoint_on_two_hosts_counts_under_each():
    # One distinct finding (h1) seen on two hosts: per-host counts intentionally
    # sum to 2 (it IS on both), while it is a single attributed endpoint.
    view = _agg(
        endpoint_occurrences=[("a.acme.io", "h1"), ("b.acme.io", "h1")],
        scope_hosts=["acme.io"],
    )
    assert _row(view, "a.acme.io").endpoints == 1
    assert _row(view, "b.acme.io").endpoints == 1
    assert view.endpoints_unattributed == 0


def test_out_of_scope_lookalike_is_not_in_scope():
    # "evil-acme.io" shares no dot boundary with "acme.io" -> out of scope
    # (mirrors egress.host_in_scope's anti-suffix-spoof rule).
    view = _agg(asset_urls=["https://evil-acme.io/x.js"], scope_hosts=["acme.io"])
    assert view.count == 1 and view.in_scope == 0
    assert not _row(view, "evil-acme.io").in_scope
