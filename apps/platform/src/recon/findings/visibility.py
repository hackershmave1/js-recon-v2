"""Which stored findings the workspace SHOWS by default, and why the rest are hidden.

The platform captures every sighting (a junk literal like ``https://x`` is still useful raw
material, e.g. for a later model reversing it from source), but only findings that make sense to
a human are shown. This is a READ-TIME, REVERSIBLE overlay: nothing is deleted, and
``include_hidden`` brings everything back with no re-analysis. Every surface that lists or counts
"the run's findings" (findings list, Sessions card, extension popup, hosts inventory, run diff,
threat-model context, the OpenAPI download, the spec summary) goes through
:func:`hidden_finding_ids`, so they agree. The GraphQL list needs no overlay — no rule here applies
to it.

Reasons:
- ``noise``: every attributed host is a known analytics / telemetry / vendor host
  (``noise_hosts``) — any finding type, unless every one of them is first-party (a target that
  is itself on the vendor list keeps its own surface).
- ``junk_or_dynamic`` (page routes): an absolute URL with no real host — a single label
  (``https://x``), a template (``https://${e}``), loopback, an IP — the same
  ``egress.attributed_host`` bar scope uses.
- ``off_scope`` (page routes): a real host that is not first-party (a library doc link, a schema
  id, a third-party site). A third-party API call the app makes is real attack surface, so the
  scope rule applies to page routes only.
- ``third_party`` (any type but a confirmed ``secret``): EVERY occurrence comes from a third-party
  package file (GLOSSARY: Third-party code — ``recon.findings.origin``), and none of them is a call
  to the target itself: a relative call (no host — the app's own origin, e.g. an auth library's
  ``/api/auth/session``) or an in-scope host keeps it shown, as does any occurrence from app code
  or from a bundle. This deliberately extends hiding to off-scope ENDPOINTS, but only those that
  live solely in package code; a package the researcher treats as app code (the session's
  ``app_packages``) is not third-party. Path-based, so it applies even with no known scope.
"""

from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from recon.config import get_settings
from recon.db.models import Engagement, EngagementSession, Finding, FindingOccurrence, Run, RunAsset
from recon.domain import FindingType
from recon.fetch import egress
from recon.findings import noise_hosts, origin

NOISE = "noise"
JUNK_OR_DYNAMIC = "junk_or_dynamic"
OFF_SCOPE = "off_scope"
THIRD_PARTY = "third_party"


def expand_scope_for_display(scope_hosts: list[str], *, allow_local: bool = False) -> list[str]:
    """Expand scope entries to their apex domain (last two labels): declaring
    ``assets.canditech.io`` makes ``canditech.io`` and its sibling subdomains first-party for
    DISPLAY. Public suffixes (``github.io``, ``amazonaws.com``…) are excluded via the existing
    denylist, so scope never expands to a shared-hosting neighbour. The egress guard that controls
    what the fetcher reaches is unchanged."""
    expanded = list(scope_hosts)
    for entry in scope_hosts:
        normalized = egress._normalize_host(entry)
        if not egress.is_valid_scope_entry(normalized, allow_local=allow_local):
            continue
        labels = normalized.split(".")
        if len(labels) < 3:
            continue  # already at apex (two labels) — nothing to add
        apex = ".".join(labels[-2:])
        if egress.is_valid_scope_entry(apex, allow_local=allow_local) and apex not in expanded:
            expanded.append(apex)
    return expanded


def why_hidden(
    finding_type: str,
    value: str,
    occurrence_hosts: Iterable[str | None],
    first_party: list[str] | None,
    *,
    allow_local: bool = False,
    occurrences: Iterable[tuple[str | None, str | None]] = (),
    app_packages: Iterable[str] = (),
) -> str | None:
    """Why a finding is hidden by default, or ``None`` when it is shown.

    ``first_party`` is the run's expanded first-party host list, or ``None`` when nothing is
    known about it (an upload with no scope) — then only junk and noise are hidden."""
    attributed: set[str | None] = {h for h in occurrence_hosts if h}
    own = (
        bool(first_party)
        and bool(attributed)
        and all(
            egress.host_in_scope(h, first_party or [], allow_local=allow_local) for h in attributed
        )
    )
    if not own and noise_hosts.is_all_noise(attributed):
        return NOISE
    if _third_party(finding_type, occurrences, first_party, app_packages, allow_local=allow_local):
        return THIRD_PARTY
    if finding_type != FindingType.PAGE_ROUTE.value:
        return None
    url = "https:" + value if value.startswith("//") else value  # protocol-relative = absolute
    if "://" not in url:
        return None  # a relative route (`/`, `/dashboard`) is always the app's own
    host = _static_host(url)
    if first_party and host and egress.host_in_scope(host, first_party, allow_local=allow_local):
        return None  # first-party wins over the junk bar (a `localhost` lab target is in scope)
    if egress.attributed_host(url) is None:
        return JUNK_OR_DYNAMIC
    if first_party is not None:
        return OFF_SCOPE
    return None


def _third_party(
    finding_type: str,
    occurrences: Iterable[tuple[str | None, str | None]],
    first_party: list[str] | None,
    app_packages: Iterable[str],
    *,
    allow_local: bool,
) -> bool:
    """See ``third_party`` in the module docstring."""
    if finding_type == FindingType.SECRET.value:
        return False  # a leaked key matters wherever it sits; never drop it from the count
    occurrences = list(occurrences)
    if not occurrences:
        return False
    packages = list(app_packages)
    for host, path in occurrences:
        if not path or not origin.is_third_party(path, packages):
            return False  # app code, or a bundle (unclassified): shown
        if host is None and finding_type in _OWN_ORIGIN_WHEN_HOSTLESS:
            return False  # a relative call targets the app's own origin
        if (
            host
            and first_party
            and egress.host_in_scope(host, first_party, allow_local=allow_local)
        ):
            return False
    return True


# Types whose host-less occurrence is a call to the app's own origin (a relative URL).
_OWN_ORIGIN_WHEN_HOSTLESS = frozenset(
    {
        FindingType.ENDPOINT.value,
        FindingType.ENDPOINT_SUSPECTED.value,
        FindingType.ENDPOINT_UNRESOLVED.value,
        FindingType.ENDPOINT_GENERIC.value,
        FindingType.PARAM.value,
        FindingType.PAGE_ROUTE.value,
        FindingType.GRAPHQL.value,
    }
)


def _static_host(url: str) -> str | None:
    """The URL's host when it is a literal one (a template only in the path or query still has a
    real host); ``None`` for a templated or unparseable host (``https://[a-z]/x``)."""
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError:
        return None
    return None if "${" in parts.netloc else host


def first_party_hosts(session: Session, run: Run) -> list[str] | None:
    """The run's first-party hosts for display: the session's scope plus its engagement's
    in-scope domains, apex-expanded. With none declared (most extension captures), the hosts the
    run's assets were captured from stand in. ``None`` when there is nothing to go on."""
    scope = session.scalar(
        select(EngagementSession.scope_hosts).where(EngagementSession.id == run.session_id)
    )
    entries = [str(h) for h in scope or []]
    domains = session.scalar(
        select(Engagement.in_scope_domains)
        .join(EngagementSession, EngagementSession.engagement_id == Engagement.id)
        .where(EngagementSession.id == run.session_id)
    )
    entries += [str(d) for d in domains or []]
    if not entries:
        asset_urls = session.scalars(select(RunAsset.url).where(RunAsset.run_id == run.id)).all()
        entries = sorted(
            {
                h
                for h in (_static_host(u) for u in asset_urls)
                if h and not noise_hosts.is_noise_host(h)
            }
        )
    if not entries:
        return None
    return expand_scope_for_display(entries, allow_local=get_settings().allow_local_egress)


def hidden_finding_ids(session: Session, run_id: str) -> dict[str, str]:
    """Every hidden finding of the run, by id, with its reason (see the module docstring)."""
    run = session.get(Run, run_id)
    if run is None:
        return {}
    first_party = first_party_hosts(session, run)
    allow_local = get_settings().allow_local_egress
    app_packages = (
        session.scalar(
            select(EngagementSession.app_packages).where(EngagementSession.id == run.session_id)
        )
        or []
    )
    # ONE pass over the run's occurrences: hosts (noise/scope) and (host, source path) pairs
    # (third-party) — this runs per Sessions card, so no second query.
    hosts_by_finding: dict[str, set[str | None]] = {}
    occurrences_by_finding: dict[str, list[tuple[str | None, str | None]]] = {}
    for finding_id, host, source_path in session.execute(
        select(FindingOccurrence.finding_id, FindingOccurrence.host, FindingOccurrence.source_path)
        .join(Finding, Finding.id == FindingOccurrence.finding_id)
        .where(Finding.run_id == str(run_id))
    ).all():
        if host is not None:
            hosts_by_finding.setdefault(str(finding_id), set()).add(host)
        occurrences_by_finding.setdefault(str(finding_id), []).append((host, source_path))
    route_values = dict(
        session.execute(
            select(Finding.id, Finding.value).where(
                Finding.run_id == str(run_id), Finding.type == FindingType.PAGE_ROUTE.value
            )
        )
        .tuples()
        .all()
    )
    hidden: dict[str, str] = {}
    for finding_id, finding_type in session.execute(
        select(Finding.id, Finding.type).where(Finding.run_id == str(run_id))
    ).all():
        value = route_values.get(finding_id, "")
        reason = why_hidden(
            finding_type,
            value,
            hosts_by_finding.get(str(finding_id), ()),
            first_party,
            allow_local=allow_local,
            occurrences=occurrences_by_finding.get(str(finding_id), ()),
            app_packages=app_packages,
        )
        if reason:
            hidden[str(finding_id)] = reason
    return hidden
