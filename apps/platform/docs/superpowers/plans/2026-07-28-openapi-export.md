# OpenAPI export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Emit the statically-reconstructed API of a run as a valid OpenAPI 3.0.3 document, on demand, downloadable and consumable in-process by the future threat-model stage.

**Architecture:** A pure serializer `src/recon/probe/openapi.py` turns `probe/reconstruct.py::reconstruct_run()` output (`ReconstructedRequest[]`) into an OpenAPI 3.0.3 `dict`, self-validated with `openapi-spec-validator` before returning; a thin `GET /runs/{run_id}/export/openapi?format=json|yaml` route (`src/recon/api/export_router.py`) mirrors `spec_router` and returns a file download. No persistence, no new dependency, no new blob kind.

**Tech Stack:** Python 3.11, FastAPI, `openapi-spec-validator` (already a dep), `pyyaml` (already a dep), pytest (colocated `*_test.py`).

**Spec:** `docs/superpowers/specs/2026-07-28-openapi-export-design.md` (branch `openapi-export`, gate-passed at commit `c8b43f8`).

## Global Constraints

- **OpenAPI 3.0.3** output only (spec §3.2). Swagger 2.0 / 3.1 are out of scope.
- **JSON is the default `format`; `yaml` is opt-in** via `?format=yaml` (spec §3.3).
- **Self-validate every emitted document** with `openapi_spec_validator.validate(...)` before returning; `build_openapi` raises on an invalid doc, and the route maps any build/validate exception to **500** via a broad `except Exception` (spec §3.4, §7, gate B2).
- **No new dependency** — `pyyaml` and `openapi-spec-validator` are already in `pyproject.toml`.
- **Honesty, never fabricate** (spec §5): parameter *names* are observed; parameter/body *types* and schemas are inferred (say so in `description`); **no `securitySchemes` / security** is emitted (headers are not captured); responses are a single honest `default` "not observed" placeholder.
- **No active traffic / no new egress** — this is pure serialization of already-stored findings (REQ-P1/P2).
- **Path canonicalization is mandatory** (spec §5, gate B1): every `{...}`/`${...}` interpolation in a path must become one balanced, uniquely-named path parameter with a matching declaration, or `openapi-spec-validator` rejects the document.
- **Test runner:** host lane `./.venv/Scripts/python.exe -m pytest <path> -v` (this env sometimes drops pytest's final "N passed" line — trust the exit code). Pure tests run under `-m "not integration"`; the router test is `@pytest.mark.integration` and needs the docker stack up.
- **Commits:** Conventional Commits; commit at the end of each task. Do **not** push (the user's call).

---

## File structure

| File | Responsibility |
|---|---|
| `src/recon/probe/openapi.py` (new) | Pure: `build_openapi(requests, *, run_id) -> dict` (+ validation) and `dump_openapi(document, fmt) -> (bytes, media_type)`. All mapping/canonicalization helpers are private here. |
| `src/recon/probe/openapi_test.py` (new) | Host-lane pure tests: construct `ReconstructedRequest` directly, assert mapping + that every fixture validates. |
| `src/recon/api/export_router.py` (new) | Thin `GET /runs/{run_id}/export/openapi?format=` route; mirrors `spec_router`. |
| `src/recon/api/export_router_test.py` (new) | Integration test: seed a run, GET the export, assert 200 + `Content-Disposition` + valid body + 404/422 paths. |
| `src/recon/api/app.py:29-33` (modify) | `import export_router` and `app.include_router(export_router.router)` — **before** `_mount_spa` (`:42`) registers the SPA catch-all. |

**Interfaces produced (used across tasks):**
- `_canonicalize_path(path: str) -> tuple[str, list[dict]]` — Task 1.
- `_operation_object(request: ReconstructedRequest, path_params: list[dict]) -> dict` — Task 2.
- `build_openapi(requests: list[ReconstructedRequest], *, run_id: str) -> dict` — Task 3.
- `dump_openapi(document: dict, fmt: str) -> tuple[bytes, str]` — Task 4.
- `reconstruct_run(tenant_id: str, run_id: str) -> list[ReconstructedRequest] | None` — **already exists** (`probe/reconstruct.py:130`); consumed by Task 5.
- `ReconstructedRequest(operation, method, path, hosts, query_params, body_params, content_type, example_url, probeable, endpoint_hashes)` and `QueryParam(name, example=None)` — **already exist** (`probe/reconstruct.py:28-45`).

---

### Task 1: Path canonicalization (`_canonicalize_path`)

The gate-B1 core. Turns a templated path into a path whose every interpolation is one balanced, uniquely-named path parameter — the invariant that keeps the emitted document valid.

**Files:**
- Create: `src/recon/probe/openapi.py`
- Test: `src/recon/probe/openapi_test.py`

**Interfaces:**
- Consumes: nothing (pure string logic).
- Produces: `_canonicalize_path(path: str) -> tuple[str, list[dict]]` — returns the rewritten path and the list of OpenAPI `parameters` objects (each `{"name", "in": "path", "required": True, "schema", "description"}`).

- [ ] **Step 1: Write the failing test**

```python
# src/recon/probe/openapi_test.py
from recon.probe.openapi import _canonicalize_path


def _names(params):
    return [p["name"] for p in params]


def test_recognized_tokens_become_typed_path_params():
    path, params = _canonicalize_path("/users/{id}/things/{uuid}/{hash}")
    assert path == "/users/{id}/things/{uuid}/{hash}"
    assert _names(params) == ["id", "uuid", "hash"]
    assert params[0]["schema"] == {"type": "integer"}
    assert params[1]["schema"] == {"type": "string", "format": "uuid"}
    assert params[2]["schema"] == {"type": "string"}
    assert all(p["in"] == "path" and p["required"] for p in params)


def test_dollar_interpolations_are_canonicalized():
    # ${userId} -> a clean name; ${user.id} and v${n} -> synthesized positional names.
    path, params = _canonicalize_path("/u/${userId}/x/${user.id}/y/v${n}")
    assert path == "/u/{userId}/x/{p1}/y/{p2}"
    assert _names(params) == ["userId", "p1", "p2"]
    assert all(p["schema"] == {"type": "string"} for p in params)


def test_bare_brace_name_and_unbalanced_brace():
    path, params = _canonicalize_path("/a/{orderId}/b/{c")
    assert path == "/a/{orderId}/b/{p1}"
    assert _names(params) == ["orderId", "p1"]


def test_repeated_type_tokens_get_unique_names():
    path, params = _canonicalize_path("/a/{id}/b/{id}")
    assert path == "/a/{id}/b/{id2}"
    assert _names(params) == ["id", "id2"]


def test_plain_path_has_no_params():
    path, params = _canonicalize_path("/location/address/search")
    assert path == "/location/address/search"
    assert params == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/probe/openapi_test.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'recon.probe.openapi'` (or `ImportError` for `_canonicalize_path`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/recon/probe/openapi.py
"""Serialize a run's reconstructed requests into a valid OpenAPI 3.0.3 document
(the inverse of spec-ingest). Pure over ``reconstruct.ReconstructedRequest`` — no
DB, no engines, no active traffic.

Honesty (REQ-C2): parameter NAMES are observed; parameter/body TYPES and schemas
are inferred and marked so; no security is asserted (headers are not captured).
Every emitted document is validated with ``openapi-spec-validator`` before it is
returned, so a caller never receives an invalid spec.
"""

from __future__ import annotations

import re

# Path tokens ``normalize.py`` emits for value-templated segments, mapped to an
# inferred OpenAPI schema. Every OTHER interpolation is handled generically.
_RECOGNIZED: dict[str, dict] = {
    "{id}": {"type": "integer"},
    "{uuid}": {"type": "string", "format": "uuid"},
    "{hash}": {"type": "string"},
}

# A segment that is exactly one clean ``{name}`` or ``${name}`` (name is a legal
# identifier) — its name is reusable verbatim; anything else gets a positional name.
_SINGLE_INTERP = re.compile(r"^\$?\{([A-Za-z_][A-Za-z0-9_]*)\}$")

_PARAM_DESCRIPTION = (
    "Name synthesized and type inferred from a templated path segment; "
    "the original parameter name is not recoverable from static analysis."
)


def _unique(base: str, used: set[str]) -> str:
    name = base
    counter = 2
    while name in used:
        name = f"{base}{counter}"
        counter += 1
    used.add(name)
    return name


def _path_param(name: str, schema: dict) -> dict:
    return {
        "name": name,
        "in": "path",
        "required": True,
        "schema": dict(schema),
        "description": _PARAM_DESCRIPTION,
    }


def _canonicalize_path(path: str) -> tuple[str, list[dict]]:
    """Rewrite ``path`` so every interpolation becomes one balanced, uniquely-named
    path parameter with a matching declaration. Guarantees the emitted path contains
    only balanced ``{legalName}`` tokens — the OpenAPI-validity invariant (gate B1/B2)."""
    out_segments: list[str] = []
    params: list[dict] = []
    used: set[str] = set()
    positional = 0
    for segment in path.split("/"):
        if segment in _RECOGNIZED:
            name = _unique(segment[1:-1], used)  # strip the braces
            out_segments.append("{" + name + "}")
            params.append(_path_param(name, _RECOGNIZED[segment]))
        elif "{" in segment or "}" in segment or "$" in segment:
            match = _SINGLE_INTERP.match(segment)
            if match:
                name = _unique(match.group(1), used)
            else:
                positional += 1
                name = _unique(f"p{positional}", used)
            out_segments.append("{" + name + "}")
            params.append(_path_param(name, {"type": "string"}))
        else:
            out_segments.append(segment)
    return "/".join(out_segments), params
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/probe/openapi_test.py -v`
Expected: PASS (5 tests).

Note on `test_dollar_interpolations`: `${userId}` matches `_SINGLE_INTERP` → `userId`; `${user.id}` has a `.` so it does not match → `p1`; `v${n}` is not a pure interpolation → `p2`. Positional counter only increments for the non-clean cases, so the names are `userId`, `p1`, `p2`.

- [ ] **Step 5: Commit**

```bash
git add src/recon/probe/openapi.py src/recon/probe/openapi_test.py
git commit -m "feat(openapi-export): path canonicalization for all interpolations"
```

---

### Task 2: Operation object (`_operation_object` + body/query helpers)

Assembles one OpenAPI operation: path params (from Task 1), query params (null-example omitted), request body (typed only when content-type observed; else honest `x-recon-body-params`), the `default` response, and `x-recon-confidence`.

**Files:**
- Modify: `src/recon/probe/openapi.py`
- Test: `src/recon/probe/openapi_test.py`

**Interfaces:**
- Consumes: `ReconstructedRequest`, `QueryParam` (from `recon.probe.reconstruct`); the param-dict shape from Task 1.
- Produces: `_operation_object(request: ReconstructedRequest, path_params: list[dict]) -> dict`.

- [ ] **Step 1: Write the failing test**

```python
# append to src/recon/probe/openapi_test.py
from recon.probe.openapi import _operation_object
from recon.probe.reconstruct import QueryParam, ReconstructedRequest


def _req(**kw):
    base = dict(
        operation="GET /x", method="GET", path="/x", hosts=(),
        query_params=(), body_params=(), content_type=None,
        example_url=None, probeable=True, endpoint_hashes=(),
    )
    base.update(kw)
    return ReconstructedRequest(**base)


def test_query_params_omit_null_example():
    req = _req(query_params=(QueryParam("page", None), QueryParam("q", "hello")))
    op = _operation_object(req, [])
    params = {p["name"]: p for p in op["parameters"]}
    assert params["page"]["in"] == "query" and params["page"]["required"] is False
    assert "example" not in params["page"]
    assert params["q"]["example"] == "hello"


def test_body_with_content_type_is_typed_request_body():
    req = _req(method="POST", operation="POST /x", body_params=("street", "city"),
               content_type="application/json")
    op = _operation_object(req, [])
    schema = op["requestBody"]["content"]["application/json"]["schema"]
    assert schema["type"] == "object"
    assert set(schema["properties"]) == {"street", "city"}
    assert "x-recon-body-params" not in op
    assert op["x-recon-confidence"]["body"] == "inferred"


def test_body_without_content_type_is_extension_not_json():
    req = _req(method="POST", operation="POST /x", body_params=("a", "b"), content_type=None)
    op = _operation_object(req, [])
    assert "requestBody" not in op
    assert op["x-recon-body-params"] == ["a", "b"]
    assert "content-type not observed" in op["description"]
    assert op["x-recon-confidence"]["body"] == "names-only"


def test_default_response_always_present():
    op = _operation_object(_req(), [])
    assert set(op["responses"]) == {"default"}
    assert "not capture responses" in op["responses"]["default"]["description"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/probe/openapi_test.py -v`
Expected: FAIL — `ImportError: cannot import name '_operation_object'`.

- [ ] **Step 3: Write minimal implementation**

```python
# append to src/recon/probe/openapi.py
from recon.probe.reconstruct import ReconstructedRequest

_RESPONSES = {
    "default": {"description": "Not observed — static analysis does not capture responses."}
}


def _query_param(param) -> dict:
    obj = {
        "name": param.name,
        "in": "query",
        "required": False,
        "schema": {"type": "string"},
        "description": "Name observed; type inferred.",
    }
    if param.example is not None:
        obj["example"] = param.example
    return obj


def _request_body(request: ReconstructedRequest) -> dict | None:
    # Only assert a media type we actually observed (fetch/axios -> json). jQuery/xhr
    # bodies leave content_type None; those are surfaced via x-recon-body-params instead.
    if not request.body_params or request.content_type is None:
        return None
    properties = {
        name: {"type": "string", "description": "Name observed; type inferred."}
        for name in request.body_params
    }
    return {
        "required": False,
        "content": {
            request.content_type: {
                "schema": {
                    "type": "object",
                    "description": "Property names observed statically; types inferred; not exhaustive.",
                    "properties": properties,
                }
            }
        },
    }


def _body_confidence(request: ReconstructedRequest) -> str:
    if not request.body_params:
        return "absent"
    return "inferred" if request.content_type else "names-only"


def _operation_object(request: ReconstructedRequest, path_params: list[dict]) -> dict:
    parameters = list(path_params) + [_query_param(p) for p in request.query_params]
    operation: dict = {
        "x-recon-confidence": {
            "path": "certain",
            "methods": "observed-only",
            "param-names": "synthesized" if path_params else "observed",
            "param-types": "inferred",
            "body": _body_confidence(request),
        },
        "responses": dict(_RESPONSES),
    }
    if parameters:
        operation["parameters"] = parameters
    body = _request_body(request)
    if body is not None:
        operation["requestBody"] = body
    elif request.body_params:  # names known, content-type not observed
        operation["x-recon-body-params"] = list(request.body_params)
        operation["description"] = (
            "Request body observed with property names: "
            + ", ".join(request.body_params)
            + "; content-type not observed, so no request-body schema is asserted."
        )
    return operation
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/probe/openapi_test.py -v`
Expected: PASS (9 tests total).

- [ ] **Step 5: Commit**

```bash
git add src/recon/probe/openapi.py src/recon/probe/openapi_test.py
git commit -m "feat(openapi-export): operation object with honest params and body"
```

---

### Task 3: Document assembly + validation (`build_openapi`)

Filters WS/WSS out of `paths` (surfaced under a root `x-recon-websocket-endpoints`), merges canonicalization collisions, builds `info`/`servers`, and **validates** the whole document before returning.

**Files:**
- Modify: `src/recon/probe/openapi.py`
- Test: `src/recon/probe/openapi_test.py`

**Interfaces:**
- Consumes: `_canonicalize_path` (Task 1), `_operation_object` (Task 2), `ReconstructedRequest`.
- Produces: `build_openapi(requests: list[ReconstructedRequest], *, run_id: str) -> dict`.

- [ ] **Step 1: Write the failing test**

```python
# append to src/recon/probe/openapi_test.py
from openapi_spec_validator import validate

from recon.probe.openapi import build_openapi


def test_build_validates_and_shapes_a_document():
    req = _req(operation="GET /users/${id}/orders", method="GET",
               path="/users/${id}/orders", hosts=("api.example.com",),
               query_params=(QueryParam("page", None),), example_url=None)
    doc = build_openapi([req], run_id="5ac48ca0-db51-420c-939f-000000000000")
    validate(doc)  # must not raise
    assert doc["openapi"] == "3.0.3"
    assert "/users/{id}/orders" in doc["paths"]
    assert doc["servers"] == [
        {"url": "https://api.example.com",
         "description": "Host observed; scheme/port inferred where not seen in a concrete URL."}
    ]


def test_websocket_excluded_from_paths_and_surfaced():
    ws = _req(operation="WSS wss://api.example.com/live", method="WSS",
              path="wss://api.example.com/live", probeable=False,
              example_url="wss://api.example.com/live")
    doc = build_openapi([ws], run_id="00000000-0000-0000-0000-000000000000")
    validate(doc)
    assert doc["paths"] == {}
    assert doc["x-recon-websocket-endpoints"] == ["WSS wss://api.example.com/live"]


def test_canonicalization_collision_merges():
    a = _req(operation="GET /users/${id}", method="GET", path="/users/${id}",
             query_params=(QueryParam("a", None),))
    b = _req(operation="GET /users/{id}", method="GET", path="/users/{id}",
             query_params=(QueryParam("b", None),))
    doc = build_openapi([a, b], run_id="00000000-0000-0000-0000-000000000000")
    validate(doc)
    assert list(doc["paths"]) == ["/users/{id}"]
    names = {p["name"] for p in doc["paths"]["/users/{id}"]["get"]["parameters"]}
    assert {"a", "b"} <= names  # both operations' query params survive the merge


def test_scheme_and_port_from_example_url():
    req = _req(operation="GET /x", path="/x", hosts=("api.example.com",),
               example_url="http://api.example.com:8443/x")
    doc = build_openapi([req], run_id="00000000-0000-0000-0000-000000000000")
    assert doc["servers"] == [
        {"url": "http://api.example.com:8443",
         "description": "Host observed; scheme/port inferred where not seen in a concrete URL."}
    ]


def test_empty_run_is_a_valid_empty_document():
    doc = build_openapi([], run_id="00000000-0000-0000-0000-000000000000")
    validate(doc)
    assert doc["paths"] == {}
    assert "servers" not in doc


def test_no_host_omits_servers():
    doc = build_openapi([_req(path="/x", hosts=(), example_url=None)],
                        run_id="00000000-0000-0000-0000-000000000000")
    validate(doc)
    assert "servers" not in doc
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/probe/openapi_test.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_openapi'`.

- [ ] **Step 3: Write minimal implementation**

```python
# append to src/recon/probe/openapi.py
from urllib.parse import urlsplit

from openapi_spec_validator import validate

_INFO_DESCRIPTION = (
    "Statically reconstructed from JavaScript by the recon platform. Paths, HTTP "
    "methods, and parameter names are OBSERVED. Parameter and body TYPES and schemas "
    "are INFERRED. Response bodies were not observed. No authentication is asserted — "
    "request headers are not captured by static analysis."
)
_SERVER_DESCRIPTION = "Host observed; scheme/port inferred where not seen in a concrete URL."


def _merge_operations(existing: dict, other: dict) -> dict:
    seen = {(p["name"], p["in"]) for p in existing.get("parameters", [])}
    merged = list(existing.get("parameters", []))
    for param in other.get("parameters", []):
        key = (param["name"], param["in"])
        if key not in seen:
            merged.append(param)
            seen.add(key)
    if merged:
        existing["parameters"] = merged
    for key in ("requestBody", "x-recon-body-params"):
        if key not in existing and key in other:
            existing[key] = other[key]
    return existing


def _server_bases(request: ReconstructedRequest) -> set[str]:
    if request.example_url:
        split = urlsplit(request.example_url)
        if split.scheme and split.netloc:
            return {f"{split.scheme}://{split.netloc}"}
    return {f"https://{host}" for host in request.hosts}


def _servers(requests: list[ReconstructedRequest]) -> list[dict]:
    bases: set[str] = set()
    for request in requests:
        if request.probeable:  # WS/WSS hosts must never become HTTP server URLs
            bases |= _server_bases(request)
    return [{"url": base, "description": _SERVER_DESCRIPTION} for base in sorted(bases)]


def build_openapi(requests: list[ReconstructedRequest], *, run_id: str) -> dict:
    paths: dict[str, dict] = {}
    websockets: list[str] = []
    for request in requests:
        if not request.probeable:
            websockets.append(f"{request.method} {request.example_url or request.path}")
            continue
        canon_path, path_params = _canonicalize_path(request.path)
        operation = _operation_object(request, path_params)
        method = request.method.lower()
        path_item = paths.setdefault(canon_path, {})
        if method in path_item:
            path_item[method] = _merge_operations(path_item[method], operation)
        else:
            path_item[method] = operation

    document: dict = {
        "openapi": "3.0.3",
        "info": {
            "title": f"Reconstructed API — run {run_id[:8]}",
            "version": "0.0.0",
            "description": _INFO_DESCRIPTION,
        },
        "paths": paths,
    }
    servers = _servers(requests)
    if servers:
        document["servers"] = servers
    if websockets:
        document["x-recon-websocket-endpoints"] = sorted(websockets)

    validate(document)  # honesty guarantee — never return an invalid document
    return document
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/probe/openapi_test.py -v`
Expected: PASS (15 tests total).

- [ ] **Step 5: Commit**

```bash
git add src/recon/probe/openapi.py src/recon/probe/openapi_test.py
git commit -m "feat(openapi-export): assemble and self-validate the document"
```

---

### Task 4: Serialize to bytes (`dump_openapi`)

Render the document to JSON (default) or YAML bytes with a media type.

**Files:**
- Modify: `src/recon/probe/openapi.py`
- Test: `src/recon/probe/openapi_test.py`

**Interfaces:**
- Consumes: a `document` dict (Task 3).
- Produces: `dump_openapi(document: dict, fmt: str) -> tuple[bytes, str]` — `(bytes, media_type)`; raises `ValueError` on an unsupported `fmt`.

- [ ] **Step 1: Write the failing test**

```python
# append to src/recon/probe/openapi_test.py
import json

import pytest
import yaml

from recon.probe.openapi import dump_openapi


def test_dump_json_is_default_and_round_trips():
    doc = {"openapi": "3.0.3", "info": {"title": "t", "version": "0.0.0"}, "paths": {}}
    body, media_type = dump_openapi(doc, "json")
    assert media_type == "application/json"
    assert json.loads(body) == doc


def test_dump_yaml_round_trips_and_preserves_key_order():
    doc = {"openapi": "3.0.3", "info": {"title": "t", "version": "0.0.0"}, "paths": {}}
    body, media_type = dump_openapi(doc, "yaml")
    assert media_type == "application/yaml"
    assert yaml.safe_load(body) == doc
    assert body.decode("utf-8").splitlines()[0].startswith("openapi:")  # sort_keys=False


def test_dump_rejects_unknown_format():
    with pytest.raises(ValueError):
        dump_openapi({}, "xml")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/probe/openapi_test.py -v`
Expected: FAIL — `ImportError: cannot import name 'dump_openapi'`.

- [ ] **Step 3: Write minimal implementation**

```python
# append to src/recon/probe/openapi.py
import json

import yaml


def dump_openapi(document: dict, fmt: str) -> tuple[bytes, str]:
    if fmt == "json":
        return json.dumps(document, indent=2).encode("utf-8"), "application/json"
    if fmt == "yaml":
        text = yaml.safe_dump(document, sort_keys=False, allow_unicode=True)
        return text.encode("utf-8"), "application/yaml"
    raise ValueError(f"unsupported format: {fmt!r}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest src/recon/probe/openapi_test.py -v`
Expected: PASS (18 tests total).

- [ ] **Step 5: Run the full host lane to confirm no regressions**

Run: `./.venv/Scripts/python.exe -m pytest -m "not integration"`
Expected: exit 0 (previously 281 passed; now 281 + the new pure tests).

- [ ] **Step 6: Commit**

```bash
git add src/recon/probe/openapi.py src/recon/probe/openapi_test.py
git commit -m "feat(openapi-export): json/yaml serialization"
```

---

### Task 5: Route + app wiring + integration test

The thin `GET` route: reconstruct → build+validate → dump → file download. Mirrors `spec_router` (tenant dep, `run_in_threadpool`, `None`→404); a bad `format`→422; a build/validate failure→500 via a broad `except`.

**Files:**
- Create: `src/recon/api/export_router.py`
- Modify: `src/recon/api/app.py` (import at `:16`; `include_router` in the `:29-33` block, before `_mount_spa`)
- Test: `src/recon/api/export_router_test.py`

**Interfaces:**
- Consumes: `reconstruct_run` (`probe/reconstruct.py:130`), `build_openapi`/`dump_openapi` (Tasks 3-4), `get_tenant_id` (`api/deps.py:24`).
- Produces: `router` (an `APIRouter`) with `GET /runs/{run_id}/export/openapi`.

- [ ] **Step 1: Write the failing test**

```python
# src/recon/api/export_router_test.py
import json

import pytest
import yaml
from fastapi.testclient import TestClient
from openapi_spec_validator import validate

from recon.api.app import create_app
from recon.db import models
from recon.db.base import tenant_session
from recon.domain import FindingType
from recon.findings import store
from recon.sessions import service as sessions_service

pytestmark = pytest.mark.integration


@pytest.fixture()
def client():
    return TestClient(create_app())


def _headers(tenant):
    return {"X-Tenant-Id": tenant}


def _seed(tenant, session_id):
    """A run with one endpoint finding: GET /location/address/search on acme.io."""
    with tenant_session(tenant) as session:
        run = models.Run(tenant_id=tenant, session_id=session_id, state="done")
        session.add(run)
        session.flush()
        run_id = str(run.id)
        store.record_finding(
            session, tenant_id=tenant, run_id=run_id, finding_type=FindingType.ENDPOINT,
            value="GET /location/address/search", path="input.js",
            occurrence=store.Occurrence(host="acme.io", raw_url="https://acme.io/location/address/search"),
            attributes={"method": "GET", "kind": "fetch"}, first_stage="analyzing",
        )
        return run_id


def test_export_openapi_json(client, authorized_session):
    tenant, session_id = authorized_session
    run_id = _seed(tenant, session_id)

    resp = client.get(f"/runs/{run_id}/export/openapi", headers=_headers(tenant))

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.headers["content-disposition"] == f'attachment; filename="openapi-{run_id}.json"'
    doc = json.loads(resp.content)
    validate(doc)
    assert "/location/address/search" in doc["paths"]
    assert doc["servers"][0]["url"] == "https://acme.io"


def test_export_openapi_yaml(client, authorized_session):
    tenant, session_id = authorized_session
    run_id = _seed(tenant, session_id)

    resp = client.get(f"/runs/{run_id}/export/openapi?format=yaml", headers=_headers(tenant))

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/yaml")
    validate(yaml.safe_load(resp.content))


def test_export_bad_format_is_422(client, authorized_session):
    tenant, session_id = authorized_session
    run_id = _seed(tenant, session_id)
    resp = client.get(f"/runs/{run_id}/export/openapi?format=xml", headers=_headers(tenant))
    assert resp.status_code == 422


def test_export_unknown_run_is_404(client, tenant):
    resp = client.get(
        "/runs/00000000-0000-0000-0000-000000000000/export/openapi", headers=_headers(tenant)
    )
    assert resp.status_code == 404


def test_export_other_tenant_run_is_404(client, authorized_session):
    owner_tenant, session_id = authorized_session
    run_id = _seed(owner_tenant, session_id)
    other_tenant = sessions_service.create_tenant("export-router-other")
    resp = client.get(f"/runs/{run_id}/export/openapi", headers=_headers(other_tenant))
    assert resp.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run (needs the docker stack up): `./.venv/Scripts/python.exe -m pytest src/recon/api/export_router_test.py -v`
Expected: FAIL — the route does not exist yet, so the 200 tests get a 404 from the SPA fallback (and the import of `export_router` in a later step is not yet wired).

- [ ] **Step 3: Write the route**

```python
# src/recon/api/export_router.py
"""Export a run's reconstructed API as an OpenAPI 3.0.3 document (spec §6).

GET /runs/{run_id}/export/openapi?format=json|yaml — the inverse of the spec
attach/classify endpoint. Thin: reconstruct the run's requests (RLS-scoped),
serialize + self-validate in recon.probe.openapi, and stream the bytes as a file
download. No persistence; the threat-model stage calls build_openapi in-process.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response

from recon.api.deps import get_tenant_id
from recon.probe import openapi
from recon.probe.reconstruct import reconstruct_run

router = APIRouter(tags=["export"])


def _render(requests, run_id: str, fmt: str) -> tuple[bytes, str]:
    document = openapi.build_openapi(requests, run_id=run_id)
    return openapi.dump_openapi(document, fmt)


@router.get("/runs/{run_id}/export/openapi")
async def export_openapi(
    run_id: str,
    format: str = "json",
    tenant_id: str = Depends(get_tenant_id),
) -> Response:
    if format not in ("json", "yaml"):
        raise HTTPException(status_code=422, detail="format must be 'json' or 'yaml'")
    # reconstruct_run is a blocking DB read; keep it off the event loop like spec_router.
    requests = await run_in_threadpool(reconstruct_run, tenant_id, run_id)
    if requests is None:
        raise HTTPException(status_code=404, detail="run not found")
    try:
        body, media_type = await run_in_threadpool(_render, requests, run_id, format)
    except Exception as exc:  # noqa: BLE001 — self-validation backstop (gate B2) → 500
        raise HTTPException(
            status_code=500, detail="failed to build a valid OpenAPI document"
        ) from exc
    filename = f"openapi-{run_id}.{format}"
    return Response(
        content=body,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
```

- [ ] **Step 4: Wire the router into the app**

In `src/recon/api/app.py`, add `export_router` to the import on line 16:

```python
from recon.api import (
    export_router,
    findings_router,
    probe_router,
    runs_router,
    sessions_router,
    spec_router,
)
```

and register it in `create_app()` (in the `:29-33` block, **before** `_mount_spa(app, settings)` on `:42`):

```python
    app.include_router(spec_router.router)
    app.include_router(export_router.router)
```

- [ ] **Step 5: Run tests to verify they pass**

Run (stack up): `./.venv/Scripts/python.exe -m pytest src/recon/api/export_router_test.py -v`
Expected: PASS (5 tests).

- [ ] **Step 6: Confirm the host lane is still green**

Run: `./.venv/Scripts/python.exe -m pytest -m "not integration"`
Expected: exit 0 (no regressions; the new pure tests included).

- [ ] **Step 7: Commit**

```bash
git add src/recon/api/export_router.py src/recon/api/export_router_test.py src/recon/api/app.py
git commit -m "feat(openapi-export): GET /runs/{id}/export/openapi route"
```

---

## Self-review (checked against the spec)

- **Spec coverage:** §4 files → Tasks 1-5 + app wiring; §5 mapping (path canon, query, body-honesty, responses, servers, info, x-recon-confidence, WS extension) → Tasks 1-3; §6 route + Content-Disposition → Task 5; §7 errors (404/422/500-broad-except/empty-run-200) → Tasks 3 & 5; §8 tests (validate every fixture, canon corpus incl. `${user.id}`/`{userId}`/`v${n}`/unbalanced/collision, null-example, jQuery-body-extension, WS, servers, empty, json+yaml, 404/422) → Tasks 1-5; §12 gate B1/B2 → Task 1 (canonicalization) + Task 5 (broad except). No gaps.
- **Placeholder scan:** every code/test step contains real code; no TBD/TODO/"similar to".
- **Type consistency:** `_canonicalize_path -> (str, list[dict])`, `_operation_object(request, path_params) -> dict`, `build_openapi(requests, *, run_id) -> dict`, `dump_openapi(document, fmt) -> (bytes, str)` are used identically wherever referenced; `ReconstructedRequest`/`QueryParam` field names match `reconstruct.py:28-45`; `reconstruct_run(tenant_id, run_id)` and `get_tenant_id` match the real signatures.

## Execution handoff — offered after you approve the plan.
