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
