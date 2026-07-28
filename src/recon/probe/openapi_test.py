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
