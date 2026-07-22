"""Reconstruct a probeable request from a run's findings (REQ-P1).

On-demand at read time: group findings by operation key (METHOD + templated
path), union their params, collect candidate hosts, and keep a concrete example
URL so the artifact is ready-to-fire. Pure over the ``findings.queries`` read
model — no DB access here (that is :func:`reconstruct_run`, added later).

Honesty (REQ-C2): values we did not observe (path variables, body values) are
never invented; the serializer renders them as explicit ``<name>`` placeholders.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qsl, urlsplit

from recon.findings import normalize, queries

# WebSocket "endpoints" are not HTTP requests, so curl/raw-HTTP do not apply.
_WEBSOCKET_METHODS = frozenset({"WS", "WSS"})


@dataclass(frozen=True)
class QueryParam:
    name: str
    example: str | None = None


@dataclass(frozen=True)
class ReconstructedRequest:
    operation: str          # METHOD + templated path (the grouping key)
    method: str
    path: str               # templated path
    hosts: tuple[str, ...]  # distinct occurrence hosts; may be empty (relative URL)
    query_params: tuple[QueryParam, ...]
    body_params: tuple[str, ...]
    content_type: str | None
    example_url: str | None  # a representative concrete occurrence.raw_url
    probeable: bool          # False for websocket operations
    endpoint_hash: str       # the finding_hash to triage / mark confirmed


def _method_and_path(operation: str) -> tuple[str, str]:
    method, _sep, path = operation.partition(" ")
    return method, path or "/"


def build_requests(findings: list[queries.FindingView]) -> list[ReconstructedRequest]:
    """Group endpoint + param findings into one request per operation.

    Output is deterministic regardless of input order: params are sorted by name,
    the endpoint_hash is the minimum among the operation's endpoint findings,
    and example_url is selected in sorted-by-finding_hash order.
    """
    endpoints: dict[str, list[queries.FindingView]] = {}
    params: dict[str, list[queries.FindingView]] = {}
    for finding in findings:
        if finding.type == "endpoint":
            key = normalize.operation_of_endpoint_value(finding.value)
            endpoints.setdefault(key, []).append(finding)
        elif finding.type == "param":
            key = normalize.operation_of_param_value(finding.value)
            params.setdefault(key, []).append(finding)

    requests: list[ReconstructedRequest] = []
    for operation in sorted(endpoints):
        endpoint_findings = endpoints[operation]
        method, path = _method_and_path(operation)
        hosts = tuple(sorted({
            occurrence.host
            for finding in endpoint_findings
            for occurrence in finding.occurrences
            if occurrence.host
        }))
        # Select example_url deterministically: iterate findings in sorted-by-hash order
        example_url = next(
            (
                occurrence.raw_url
                for finding in sorted(endpoint_findings, key=lambda f: f.finding_hash)
                for occurrence in finding.occurrences
                if occurrence.raw_url
            ),
            None,
        )
        example_query = dict(parse_qsl(urlsplit(example_url).query)) if example_url else {}

        query_params: dict[str, QueryParam] = {}
        body_params: list[str] = []
        for param in params.get(operation, []):
            location = param.attributes.get("location")
            name = param.attributes.get("name")
            if not name:
                continue
            if location == "query" and name not in query_params:
                query_params[name] = QueryParam(name=name, example=example_query.get(name))
            elif location == "body" and name not in body_params:
                body_params.append(name)

        # Sort query_params and body_params by name for deterministic output
        sorted_query_params = tuple(
            query_params[name]
            for name in sorted(query_params.keys())
        )
        sorted_body_params = tuple(sorted(body_params))

        # Select endpoint_hash deterministically: use the minimum finding_hash
        endpoint_hash = min(f.finding_hash for f in endpoint_findings)

        requests.append(
            ReconstructedRequest(
                operation=operation,
                method=method,
                path=path,
                hosts=hosts,
                query_params=sorted_query_params,
                body_params=sorted_body_params,
                content_type="application/json" if sorted_body_params else None,
                example_url=example_url,
                probeable=method not in _WEBSOCKET_METHODS,
                endpoint_hash=endpoint_hash,
            )
        )
    return requests


def reconstruct_run(tenant_id: str, run_id: str) -> list[ReconstructedRequest] | None:
    """Reconstruct every probeable request for a run, or ``None`` if the run is
    invisible to the tenant. Reuses the findings read model (no new query)."""
    view = queries.list_findings(tenant_id, run_id)
    if view is None:
        return None
    return build_requests(view.findings)
