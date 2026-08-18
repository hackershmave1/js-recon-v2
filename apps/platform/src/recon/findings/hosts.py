"""Read model for a run's discovered-host inventory (DEBT D26).

Aggregates every host a run surfaced — from its fetched assets, its endpoint
findings' resolved hosts, its detected tech stack, and any operator-declared
base-URL rule — and classifies each in/out of the session's declared scope with
the SAME canonical guard the crawl enforces (``egress.host_in_scope``), so the
badge never drifts from what egress actually allowed (REQ-P2). Only the scope
decision genuinely needs the server; the counts are derivable client-side, but
re-implementing the SSRF-sensitive subdomain/public-suffix match in TS would be
a drift risk, so the whole roll-up lives here behind one endpoint.

Isolation is the database's: ``tenant_session`` sets the RLS GUC, so a run that
belongs to another tenant is invisible and ``list_hosts`` returns ``None`` (the
HTTP layer maps that to 404) — distinct from a real run with no hosts (empty list).

Honesty (design §5): the inventory only counts what the pipeline actually
attributed. A relative-path endpoint (``/api/x``) carries no host until it is
resolved to one (an absolute-URL literal, or a capture-observed request), so
``endpoints_unattributed`` reports the endpoints with no resolved host rather
than silently shrinking the API surface. (A base-URL rule (REQ-C2) declares a
host but is a read-time overlay that never writes ``occurrence.host``, so it
surfaces as a ``declared`` row, not as a per-host endpoint count.) On
runtime-capture runs attribution is sparser still (DEBT D24 — many captured
findings keep ``host=null``).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import select

from recon.config import get_settings
from recon.db.base import tenant_session
from recon.db.models import (
    EngagementSession,
    Finding,
    FindingOccurrence,
    Run,
    RunAsset,
    RunTechnology,
    SessionBaseUrl,
)
from recon.domain import FindingType
from recon.fetch import egress


@dataclass(frozen=True)
class HostRow:
    host: str
    in_scope: bool
    # An operator-declared base-URL host (REQ-C2) with no directly-attributed
    # asset/endpoint/tech is still a real part of the surface — flagged so a
    # zero-count row reads as "you declared this", not noise.
    declared: bool
    assets: int
    endpoints: int
    techs: int


@dataclass(frozen=True)
class HostsView:
    run_id: str
    count: int
    in_scope: int
    # Endpoint findings with NO resolved host (relative paths not yet resolved to
    # a host). count(resolved endpoints) + endpoints_unattributed == the run's
    # total endpoint findings, so this reconciles with the Overview "Endpoints" card.
    endpoints_unattributed: int
    hosts: list[HostRow]


def _host(value: str | None) -> str:
    """The one normalizer for every host source: pull the host from a bare host
    OR a full URL (``egress.host_of``) and strip case + the FQDN trailing dot
    (``egress._normalize_host``), so ``API.acme.io.`` and ``acme.io`` never split
    the universe. ``""`` for anything without a host (callers drop it)."""
    return egress._normalize_host(egress.host_of(value))


def _aggregate_hosts(
    run_id: str,
    asset_urls: list[str],
    endpoint_occurrences: list[tuple[str | None, str]],
    tech_hosts: list[str],
    declared_hosts: list[str],
    scope_hosts: list[str],
    *,
    allow_local: bool,
) -> HostsView:
    """Pure roll-up (no DB/network) so the host-universe + scope logic is unit
    testable. ``endpoint_occurrences`` is ``(host, finding_hash)`` per endpoint
    occurrence; a finding recurs across hosts, so per-host endpoint counts may
    sum higher than the distinct endpoint total (an endpoint seen on two hosts
    counts under each)."""
    assets_by_host: dict[str, int] = defaultdict[str, int](int)
    for url in asset_urls:
        # Only a real http(s) asset carries a network host. Capture stores eval'd /
        # inline / worker scripts under non-web schemes — vm://<hash> (a V8 VM id),
        # blob:, data: — which have no host (DEBT D24). Without this, host_of() would
        # read the vm:// hash as a bare hostname and flood the inventory with one
        # content-hash pseudo-host per script.
        scheme = url.split("://", 1)[0].lower() if "://" in url else ""
        if scheme not in ("http", "https"):
            continue
        h = _host(url)
        if h:
            assets_by_host[h] += 1

    tech_by_host: dict[str, int] = defaultdict[str, int](int)
    for raw_tech_host in tech_hosts:
        h = _host(raw_tech_host)
        if h:
            tech_by_host[h] += 1

    # Group occurrences by finding so a finding counts once per host, and a
    # finding whose every occurrence is host-less becomes endpoints_unattributed.
    hosts_of_finding: dict[str, set[str]] = defaultdict[str, set[str]](set)
    endpoint_findings: set[str] = set()
    for raw_host, finding_hash in endpoint_occurrences:
        endpoint_findings.add(finding_hash)
        h = _host(raw_host)
        if h:
            hosts_of_finding[finding_hash].add(h)
    endpoints_by_host: dict[str, int] = defaultdict[str, int](int)
    for finding_hosts in hosts_of_finding.values():
        for h in finding_hosts:
            endpoints_by_host[h] += 1
    endpoints_unattributed = len(endpoint_findings) - len(hosts_of_finding)

    declared: set[str] = set()
    for raw_declared in declared_hosts:
        h = _host(raw_declared)
        if h:
            declared.add(h)

    universe = set(assets_by_host) | set(tech_by_host) | set(endpoints_by_host) | declared
    rows = [
        HostRow(
            host=h,
            in_scope=egress.host_in_scope(h, scope_hosts, allow_local=allow_local),
            declared=h in declared,
            assets=assets_by_host.get(h, 0),
            endpoints=endpoints_by_host.get(h, 0),
            techs=tech_by_host.get(h, 0),
        )
        for h in sorted(universe)
    ]
    return HostsView(
        run_id=run_id,
        count=len(rows),
        in_scope=sum(1 for r in rows if r.in_scope),
        endpoints_unattributed=endpoints_unattributed,
        hosts=rows,
    )


def list_hosts(tenant_id: str, run_id: str) -> HostsView | None:
    """The run's discovered-host inventory, or ``None`` if the run does not exist
    for this tenant (RLS-invisible → 404). Bounded run-scoped reads (each covered
    by an existing index: ix_run_asset_run, ix_finding_run, ix_occurrence_finding,
    ix_run_technology_run, ix_base_url_session); the endpoint hosts come from a
    direct ``Finding⋈FindingOccurrence`` join, never the heavy ``list_findings``."""
    with tenant_session(tenant_id) as session:
        run = session.get(Run, run_id)
        if run is None:
            return None
        session_id = str(run.session_id)
        scope_hosts = (
            session.scalar(
                select(EngagementSession.scope_hosts).where(EngagementSession.id == session_id)
            )
            or []
        )
        asset_urls = list(
            session.scalars(select(RunAsset.url).where(RunAsset.run_id == str(run_id))).all()
        )
        endpoint_occurrences: list[tuple[str | None, str]] = [
            (row[0], row[1])
            for row in session.execute(
                select(FindingOccurrence.host, Finding.finding_hash)
                .join(Finding, Finding.id == FindingOccurrence.finding_id)
                .where(
                    Finding.run_id == str(run_id),
                    Finding.type == FindingType.ENDPOINT.value,
                )
            ).all()
        ]
        tech_hosts = list(
            session.scalars(
                select(RunTechnology.host).where(RunTechnology.run_id == str(run_id))
            ).all()
        )
        declared_hosts = list(
            session.scalars(
                select(SessionBaseUrl.base_url).where(SessionBaseUrl.session_id == session_id)
            ).all()
        )
    return _aggregate_hosts(
        str(run_id),
        asset_urls,
        endpoint_occurrences,
        tech_hosts,
        declared_hosts,
        list(scope_hosts),
        allow_local=get_settings().allow_local_egress,
    )
