"""Reconstruct a probeable request from a run's findings (REQ-P1).

On-demand at read time: group findings by operation key (METHOD + templated
path), union their params, collect candidate hosts, and keep a concrete example
URL so the artifact is ready-to-fire. Pure over the ``findings.queries`` read
model — no DB access here (that is :func:`reconstruct_run`, added later).

Honesty (REQ-C2): values we did not observe (path variables, body values) are
never invented; the serializer renders them as explicit ``<name>`` placeholders.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from urllib.parse import parse_qsl, urlsplit

from recon.findings import base_url, normalize, queries

# WebSocket "endpoints" are not HTTP requests, so curl/raw-HTTP do not apply.
_WEBSOCKET_METHODS = frozenset({"WS", "WSS"})

# Body serialized as JSON only for these client kinds; jQuery `data` is
# form-urlencoded and xhr/unknown is not known, so we omit Content-Type rather
# than assert a wrong one (REQ-C2 honesty).
_JSON_BODY_KINDS = frozenset({"fetch", "axios"})


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
    endpoint_hashes: tuple[str, ...]  # every contributing endpoint finding_hash


def _method_and_path(operation: str) -> tuple[str, str]:
    method, _sep, path = operation.partition(" ")
    return method, path or "/"


def _apply_rule(request: ReconstructedRequest, rules: list[base_url.BaseUrlRule]) -> ReconstructedRequest:
    """Apply the base-URL overlay to one assembled request (post param-join, gate
    B2). Candidate gate uses request.hosts (empty == host-less)."""
    if not request.probeable:
        return request
    resolved = base_url.resolve_operation(
        request.method, request.path, request.endpoint_hashes, bool(request.hosts), rules
    )
    if not resolved.changed:
        return request
    hosts = request.hosts
    example_url = request.example_url
    if resolved.host:
        hosts = tuple(sorted(set(request.hosts) | {resolved.host}))
        example_url = f"{resolved.scheme}://{resolved.host}{resolved.path}"
    return replace(
        request,
        path=resolved.path,
        operation=f"{request.method} {resolved.path}",
        hosts=hosts,
        example_url=example_url,
    )


def _merge(a: ReconstructedRequest, b: ReconstructedRequest) -> ReconstructedRequest:
    """Order-independent merge of two requests that resolved onto the same
    operation: union query/body params, hosts, endpoint_hashes; deterministic
    example_url."""
    by_name = {p.name: p for p in a.query_params}
    for param in b.query_params:
        by_name.setdefault(param.name, param)
    query_params = tuple(by_name[name] for name in sorted(by_name))
    body_params = tuple(sorted(set(a.body_params) | set(b.body_params)))
    hosts = tuple(sorted(set(a.hosts) | set(b.hosts)))
    endpoint_hashes = tuple(sorted(set(a.endpoint_hashes) | set(b.endpoint_hashes)))
    example_url = min(filter(None, (a.example_url, b.example_url)), default=None)
    return replace(
        a,
        hosts=hosts,
        query_params=query_params,
        body_params=body_params,
        content_type=a.content_type or b.content_type,
        example_url=example_url,
        endpoint_hashes=endpoint_hashes,
    )


def build_requests(
    findings: list[queries.FindingView],
    rules: list[base_url.BaseUrlRule] = (),
) -> list[ReconstructedRequest]:
    """Group endpoint + param findings into one request per operation.

    Output is deterministic regardless of input order: params are sorted by name,
    endpoint_hashes is the sorted tuple of every contributing endpoint finding's
    hash, and example_url is selected in sorted-by-finding_hash order.

    ``rules`` (REQ-C2 manual base-URL overlay) is applied AFTER this grouping, so
    the resolver sees requests with params already joined (gate B2). Empty
    ``rules`` (the default) returns ``requests`` unchanged -- today's behavior is
    preserved exactly.
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

        kinds = {f.attributes.get("kind") for f in endpoint_findings}

        requests.append(
            ReconstructedRequest(
                operation=operation,
                method=method,
                path=path,
                hosts=hosts,
                query_params=sorted_query_params,
                body_params=sorted_body_params,
                content_type="application/json" if (sorted_body_params and kinds <= _JSON_BODY_KINDS) else None,
                example_url=example_url,
                probeable=method not in _WEBSOCKET_METHODS,
                endpoint_hashes=tuple(sorted(f.finding_hash for f in endpoint_findings)),
            )
        )

    if not rules:
        return requests
    merged: dict[str, ReconstructedRequest] = {}
    for request in (_apply_rule(r, rules) for r in requests):
        if request.operation in merged:
            merged[request.operation] = _merge(merged[request.operation], request)
        else:
            merged[request.operation] = request
    return [merged[operation] for operation in sorted(merged)]


def reconstruct_run(tenant_id: str, run_id: str) -> list[ReconstructedRequest] | None:
    """Reconstruct every probeable request for a run, or ``None`` if the run is
    invisible to the tenant. Reuses the findings read model (no new query)."""
    view = queries.list_findings(tenant_id, run_id)
    if view is None:
        return None
    rules = queries.list_base_url_rules(tenant_id, run_id)
    return build_requests(view.findings, rules)
